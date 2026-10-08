import asyncio
import hmac
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.models.schemas import (
    ModelConfigRequest,
    ModelConfigResponse,
    RefreshRunRecord,
    AdminRefreshRequest,
    NeonConfigPayload,
)
from backend.db.mongodb import mongo_manager
from backend.db.mongodb_adapter import mongodb_adapter
from backend.db.redis_client import redis_client
from backend.db.r2_storage import r2_manager
from backend.db.duckdb_engine import duckdb_engine
from backend.db.database_router import database_router
from backend.db.interfaces import DatabaseUnavailableError
from backend.db.replication_manager import replication_manager
from backend.db.failover_manager import failover_manager
from backend.db.neon_adapter import neon_adapter
from backend.db.neon_budget_guard import neon_budget_guard
from backend.providers.football_adapter import football_adapter
from backend.providers.basketball_adapter import basketball_adapter
from backend.providers.baseball_adapter import baseball_adapter
from backend.providers.sports_skills_hockey_provider import sports_skills_hockey_provider
from backend.providers.sports_skills_f1_provider import sports_skills_f1_provider
from backend.services.model_service import model_service
from backend.services.feed_service import feed_service
from backend.services.sync_service import sync_service
from backend.services.competition_registry import competition_registry_service
from backend.services.historical_ingestion_service import historical_ingestion_service
from backend.services.health_service import health_service

app = FastAPI(
    title="PredictPro Backend API",
    description="Probabilistic Sports Intelligence Engine — FastAPI Production Backend",
    version="2.0.0",
)

@app.on_event("startup")
async def startup_event():
    # Safe configuration state logging at startup (NEVER log keys, passwords, or connection strings)
    neon_cfg = bool(settings.neon_database_url)
    duckdb_cfg = True
    ss_cfg = bool(settings.sports_skills_base_url)

    print("[Startup] Safe Environment Configuration State:")
    print(f"Neon configured: {str(neon_cfg).lower()}")
    print(f"DuckDB configured: {str(duckdb_cfg).lower()}")
    print(f"SportsSkills configured: {str(ss_cfg).lower()}")

    # 1. Initialize DuckDB from bundled Parquet datasets without copying into Mongo/Neon
    try:
        duckdb_engine.init_catalog()
    except Exception as e:
        print(f"[Startup] DuckDB Parquet initialization notice: {e}")

LAGOS_TZ = timezone(timedelta(hours=1))

def validate_lagos_date(date_str: Optional[str]) -> Optional[str]:
    """
    Validates YYYY-MM-DD format, verifies it is a valid calendar date,
    and rejects past dates according to Africa/Lagos (WAT, UTC+1) current time.
    """
    if not date_str:
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date format '{date_str}'. Expected YYYY-MM-DD (e.g. 2026-09-23)",
        )
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid calendar date '{date_str}': {str(e)}",
        )

    today_lagos = datetime.now(LAGOS_TZ).strftime("%Y-%m-%d")
    if date_str < today_lagos:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Date cannot be in the past (Africa/Lagos). Requested: {date_str}, Today in Lagos: {today_lagos}",
        )
    return date_str

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def verify_admin_key(
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """
    Validates administrative credentials server-side.
    Fails closed:
    - If ADMIN_API_KEY is not configured or empty, administrative endpoints are disabled (403 Forbidden).
    - If missing or invalid credential is provided, returns 401 Unauthorized.
    """
    configured_key = settings.admin_api_key
    if not configured_key or not configured_key.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Administrative functionality is disabled because ADMIN_API_KEY is not configured",
        )

    provided_key: Optional[str] = None
    if x_admin_api_key:
        provided_key = x_admin_api_key.strip()
    elif authorization and authorization.startswith("Bearer "):
        provided_key = authorization.split("Bearer ", 1)[1].strip()

    if not provided_key or not hmac.compare_digest(provided_key, configured_key.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Valid ADMIN_API_KEY required for administrative operations",
        )

# 1. Health check endpoint (Read-Only)
@app.get("/api/health")
async def health_check():
    return await health_service.get_health_status()

# 2. Feed of published predictions (Read-Only)
@app.get("/api/predictions/feed")
async def get_predictions_feed(
    sport: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=20),
):
    try:
        validated_date = validate_lagos_date(date)
        items = await feed_service.get_feed(sport=sport, league=league, date=validated_date, limit=limit)
        return {
            "status": "connected",
            "count": len(items),
            "modelVersion": model_service.get_active_model(),
            "date": validated_date or datetime.now(LAGOS_TZ).strftime("%Y-%m-%d"),
            "timezone": "Africa/Lagos (WAT, UTC+1)",
            "data": items,
        }
    except DatabaseUnavailableError as db_err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Operational database disconnected: {str(db_err)}",
        )

# Available Prediction Dates in Lagos
@app.get("/api/predictions/available-dates")
async def get_available_dates():
    dates = await feed_service.get_available_prediction_dates()
    return {
        "todayLagos": datetime.now(LAGOS_TZ).strftime("%Y-%m-%d"),
        "timezone": "Africa/Lagos (WAT, UTC+1)",
        "availableDates": dates,
    }

# Diagnostics Summary for UI (Discovered, Eligible, Validated, Published, Sports, Leagues)
@app.get("/api/predictions/summary")
@app.get("/api/telemetry/summary")
async def get_predictions_summary():
    return await feed_service.get_latest_diagnostics_summary()

async def _enrich_operational_fixtures(fixtures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not fixtures:
        return []
    preds_map = {}
    try:
        pred_docs = await database_router.predictions.get_published_feed(limit=100)
        for p in pred_docs:
            p_fid = p.get("fixture_id") or p.get("fixtureId") or p.get("id")
            if p_fid:
                preds_map[p_fid] = p
    except Exception as e:
        print(f"[App] Fixture enrichment prediction notice: {e}")

    enriched = []
    for fix in fixtures:
        item = dict(fix)
        f_id = item.get("id")
        pred = preds_map.get(f_id)
        if pred and pred.get("validationStatus") == "validated" and pred.get("validatedMarkets"):
            item["validationStatus"] = "validated"
            item["validatedMarkets"] = pred.get("validatedMarkets", [])
            item["highestPercentagePrediction"] = pred.get("highestPercentagePrediction")
            item["percentage"] = pred.get("percentage")
            item["market"] = pred.get("market")
            item["selection"] = pred.get("selection")
            item["modelVersion"] = pred.get("modelVersion", model_service.get_active_model())
            item["predictionAvailable"] = True
        else:
            item["validationStatus"] = item.get("validationStatus") or "unpredicted"
            item["validatedMarkets"] = []
            item["highestPercentagePrediction"] = None
            item["percentage"] = None
            item["predictionAvailable"] = False
        enriched.append(item)
    return enriched

# Operational Fixtures Catalogue Endpoint (Read-Only)
@app.get("/api/fixtures")
@app.get("/api/matches")
async def get_fixtures_and_matches(
    sport: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    startDate: Optional[str] = Query(None, alias="startDate"),
    endDate: Optional[str] = Query(None, alias="endDate"),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    page: Optional[int] = Query(None, ge=1),
):
    validated_date = validate_lagos_date(date) if date else None
    validated_start_date = validate_lagos_date(startDate) if startDate else None
    validated_end_date = validate_lagos_date(endDate) if endDate else None

    bounded_limit = min(100, max(1, limit))
    effective_offset = (page - 1) * bounded_limit if page is not None else offset

    raw_fixtures = await database_router.fixtures.get_fixtures(
        sport=sport,
        league=league,
        date=validated_date,
        status=status,
        limit=bounded_limit,
        offset=effective_offset,
        start_date=validated_start_date,
        end_date=validated_end_date,
    )
    total_count = await database_router.fixtures.count_fixtures(
        sport=sport,
        league=league,
        date=validated_date,
        status=status,
        start_date=validated_start_date,
        end_date=validated_end_date,
    )
    enriched = await _enrich_operational_fixtures(raw_fixtures)

    return {
        "count": len(enriched),
        "total": total_count,
        "page": page or (effective_offset // bounded_limit + 1),
        "limit": bounded_limit,
        "offset": effective_offset,
        "sport": sport,
        "league": league,
        "date": validated_date,
        "startDate": validated_start_date,
        "endDate": validated_end_date,
        "status": status,
        "data": enriched,
    }

# Dedicated Goal Predictions Feed (Read-Only, returns List)
@app.get("/api/predictions/goals")
@app.get("/api/goals")
async def get_goal_predictions():
    feed = await feed_service.get_feed(sport="football", limit=20)
    goals_list = []
    for f in feed:
        stats = f.get("sportStats", {})
        h_xg = stats.get("xGRecentHome")
        a_xg = stats.get("xGRecentAway")
        xg_combined = round(h_xg + a_xg, 2) if (h_xg is not None and a_xg is not None) else None

        h_scored = stats.get("homeGoalsScoredAvg")
        # Correctly use the actual away scoring statistic; do NOT substitute conceded goals
        a_scored = stats.get("awayGoalsScoredAvg")

        # Use actual calculated bothTeamsScoredRecentRate feature
        btts_rate = stats.get("bothTeamsScoredRecentRate")
        if btts_rate is None:
            btts_rate = stats.get("bttsRate")
        if btts_rate is None:
            features = f.get("features", {})
            btts_rate = features.get("bothTeamsScoredRecentRate")
            if btts_rate is None:
                btts_rate = features.get("bttsRate")

        for m in f.get("validatedMarkets", []):
            m_name = m.get("marketName", "")
            if any(term in m_name.lower() for term in ["over", "under", "btts", "both teams"]):
                goals_list.append({
                    "id": f"goal_{m.get('id', '')}",
                    "fixtureId": f.get("id"),
                    "league": f.get("league"),
                    "homeTeam": f.get("homeTeam"),
                    "awayTeam": f.get("awayTeam"),
                    "kickoffUtc": f.get("kickoffUtc"),
                    "status": f.get("status"),
                    "marketType": m_name,
                    "predictedOutcome": m.get("selection"),
                    "percentage": m.get("probabilityPercentage"),
                    "xGCombined": xg_combined,
                    "homeAvgScored": h_scored,
                    "awayAvgScored": a_scored,
                    "bothTeamsScoredRecentRate": btts_rate,
                    "modelVersion": f.get("modelVersion", model_service.get_active_model()),
                })
    return goals_list

# Match details (Read-Only)
@app.get("/api/matches/{match_id}")
async def get_match_detail(match_id: str):
    item = await feed_service.get_match_by_id(match_id)
    if not item:
        raise HTTPException(status_code=404, detail="Match not found in operational datastore")
    return item

@app.get("/api/fixtures/{match_id}")
async def get_fixture_detail(match_id: str):
    return await get_match_detail(match_id)

# Competitions Registry (Read-Only)
@app.get("/api/competitions")
async def get_competitions(sport: Optional[str] = Query("football")):
    comps = await competition_registry_service.get_or_discover_competitions(sport=sport or "football")
    return {
        "sport": sport or "football",
        "count": len(comps),
        "competitions": comps,
    }

# Cross-Sport Competition Telemetry Endpoint (Read-Only)
@app.get("/api/telemetry/competitions")
async def get_competition_telemetry():
    telemetry_data = await feed_service.get_competition_telemetry()
    table_str = await feed_service.get_competition_telemetry_table()
    return {
        "status": "connected",
        "rows": telemetry_data,
        "table": table_str,
        "count": len(telemetry_data),
    }

# Admin refresh (Authenticated only)
@app.post("/api/admin/refresh", response_model=RefreshRunRecord)
@app.post("/admin/refresh", response_model=RefreshRunRecord)
@app.post("/api/v1/admin/refresh", response_model=RefreshRunRecord)
@app.post("/v1/admin/refresh", response_model=RefreshRunRecord)
async def admin_refresh(
    request: Optional[AdminRefreshRequest] = None,
    sport: Optional[str] = Query(None),
    date: Optional[str] = Query(None),
    force: bool = Query(False),
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    verify_admin_key(x_admin_api_key, authorization)
    
    req_sports = request.sports if request and request.sports else ([sport] if sport else None)
    req_date = request.date if request and request.date else date
    req_date_range = request.date_range if request and request.date_range else None
    req_competitions = request.competitions if request and request.competitions else None
    req_force = request.force if request and request.force is not None else force

    record = await sync_service.execute_refresh(
        sports=req_sports,
        date=req_date,
        date_range=req_date_range,
        competitions=req_competitions,
        force=req_force,
    )
    return record

# Admin sync feed (Authenticated only)
@app.post("/api/admin/sync-feed")
@app.post("/admin/sync-feed")
@app.post("/api/v1/admin/sync-feed")
@app.post("/v1/admin/sync-feed")
async def admin_sync_feed(
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    verify_admin_key(x_admin_api_key, authorization)
    diagnostics = await sync_service.execute_sync_feed()
    return diagnostics

# Admin / System Status
@app.get("/api/admin/status")
@app.get("/admin/status")
@app.get("/api/admin/diagnostics")
@app.get("/admin/diagnostics")
@app.get("/api/settings/status")
@app.get("/settings/status")
async def get_admin_status():
    import asyncio
    (
        fb_status,
        bk_status,
        bb_status,
        hk_status,
        f1_status,
        mongo_conn,
        neon_conn,
        redis_status,
        r2_status,
        duckdb_status,
    ) = await asyncio.gather(
        football_adapter.check_connection(),
        basketball_adapter.check_connection(),
        baseball_adapter.check_connection(),
        sports_skills_hockey_provider.check_connection(),
        sports_skills_f1_provider.check_connection(),
        asyncio.to_thread(mongo_manager.check_connection),
        asyncio.to_thread(neon_adapter.check_connection),
        redis_client.check_connection(),
        asyncio.to_thread(r2_manager.check_connection),
        asyncio.to_thread(duckdb_engine.check_connection),
    )

    capabilities = {
        "football": fb_status,
        "basketball": bk_status,
        "baseball": bb_status,
        "hockey": hk_status,
        "formula_1": f1_status,
    }

    connected_count = sum(1 for s in capabilities.values() if s.get("status") == "connected")
    latencies = [s.get("latencyMs", 0) for s in capabilities.values() if s.get("status") == "connected"]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0.0

    if connected_count == len(capabilities):
        overall_status = "connected"
    elif connected_count > 0:
        overall_status = "degraded"
    else:
        overall_status = "disconnected"

    sports_skills_status = {
        "status": overall_status,
        "latencyMs": avg_latency,
        "version": "2.0.0",
        "lastHeartbeat": datetime.now(timezone.utc).isoformat(),
        "capabilities": capabilities,
        "connectedCount": connected_count,
        "totalCapabilities": len(capabilities),
    }

    router_status = database_router.get_status()
    guard_metrics = neon_budget_guard.get_metrics()
    repl_status = replication_manager.get_status()

    mongo_str = "connected" if mongo_conn.get("status") in ("connected", "healthy", "ok") else "disconnected"
    neon_str = "connected" if neon_conn.get("status") in ("connected", "healthy", "ok", "configured_standby", "ready") else "disconnected"
    neon_schema_ok = neon_adapter.check_schema_compatibility()
    neon_failover_ready = bool(
        neon_str == "connected"
        and neon_schema_ok
        and guard_metrics.get("safetyState") not in ("RESOURCE_LIMITED", "CIRCUIT_TRIPPED")
    )

    redis_str = "connected" if redis_status.get("status") in ("connected", "healthy", "ok") else "disconnected"
    r2_str = "connected" if r2_status.get("status") in ("connected", "healthy", "ok") else "disconnected"
    duckdb_str = "connected" if duckdb_status.get("status") in ("connected", "healthy", "ok") else "disconnected"
    ss_str = "connected" if overall_status != "disconnected" else "disconnected"

    neon_dashboard = {
        "connectionStatus": neon_str,
        "replicationStatus": repl_status.get("lastSummary", {}).get("status", "idle"),
        "replicationLagSeconds": repl_status.get("lastSummary", {}).get("replication_lag_seconds", 0.0),
        "safetyState": guard_metrics.get("safetyState", "OPTIMAL"),
        "predictproEstimatedUsage": guard_metrics.get("predictproEstimatedUsage", {}),
        "neonReportedUsage": guard_metrics.get("neonReportedUsage"),
        "configuredSafetyThresholds": guard_metrics.get("configuredSafetyThresholds", {}),
        "circuitBreakerTripped": guard_metrics.get("circuitBreakerTripped", False),
    }

    return {
        # Exact prompt required fields:
        "MongoDB": mongo_str,
        "Neon": neon_str,
        "Neon failover ready": neon_failover_ready,
        "Redis": redis_str,
        "R2": r2_str,
        "DuckDB": duckdb_str,
        "SportsSkills": ss_str,

        # Standard camelCase fields:
        "activeDatabase": database_router.get_active_database_name(),
        "mongoDbStatus": mongo_str,
        "neonStatus": neon_str,
        "neonFailoverReady": neon_failover_ready,
        "redisStatus": redis_str,
        "r2Status": r2_str,
        "duckDbStatus": duckdb_str,
        "sportsSkillsStatus": ss_str,
        "failoverState": failover_manager.failover_state.value if hasattr(failover_manager.failover_state, "value") else str(failover_manager.failover_state),
        "replicationLag": repl_status.get("lastSummary", {}).get("replication_lag_seconds", 0.0),
        "lastFailover": failover_manager._failover_started_at_iso,
        "lastRecovery": failover_manager._last_recovery_iso,
        "reconciliationStatus": failover_manager._last_reconciliation_result,
        "neonResourceGuardStatus": guard_metrics.get("safetyState", "OPTIMAL"),
        "sportsSkills": sports_skills_status,
        "mongoDb": mongo_conn,
        "neonPostgres": neon_conn,
        "neonDashboard": neon_dashboard,
        "databaseRouter": router_status,
        "redis": redis_status,
        "r2Storage": r2_status,
        "duckDb": duckdb_status,
        "activeModel": model_service.get_active_model(),
        "lastSyncTimestamp": datetime.now(timezone.utc).isoformat(),
    }

# Deep Subsystem Diagnostics & Verbose Connection Error Inspector
@app.get("/api/health/diagnostics")
@app.get("/health/diagnostics")
@app.get("/api/v1/health/diagnostics")
@app.get("/v1/health/diagnostics")
async def get_health_diagnostics():
    """
    Returns deep connection diagnostics for pinpointing network, DNS, TCP,
    SSL handshake, driver, authentication, schema, or budget guard issues,
    especially for Neon PostgreSQL and failover storage.
    """
    return await health_service.get_diagnostics_report()

@app.post("/api/admin/config/neon")
@app.post("/api/v1/admin/config/neon")
async def update_neon_config(
    payload: NeonConfigPayload,
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """
    Updates NEON_DATABASE_URL environment variable dynamically, writes to /.env,
    and re-establishes Neon PostgreSQL connection pool.
    """
    verify_admin_key(x_admin_api_key, authorization)
    url = (payload.neon_database_url or "").strip()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="NEON_DATABASE_URL connection string cannot be empty",
        )

    import os
    os.environ["NEON_DATABASE_URL"] = url
    settings.neon_database_url = url

    # Update /.env on disk if accessible
    try:
        env_path = "/.env"
        if not os.path.exists(env_path):
            env_path = ".env"

        content = ""
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                content = f.read()

        if "NEON_DATABASE_URL=" in content:
            new_lines = []
            for line in content.splitlines():
                if line.startswith("NEON_DATABASE_URL="):
                    new_lines.append(f"NEON_DATABASE_URL={url}")
                else:
                    new_lines.append(line)
            content = "\n".join(new_lines) + "\n"
        else:
            content += f"\nNEON_DATABASE_URL={url}\n"

        with open(env_path, "w") as f:
            f.write(content)
    except Exception as e:
        print(f"[Admin] Notice updating .env file: {e}")

    try:
        neon_adapter.close()
        neon_adapter.executor._url = url
        neon_adapter.executor._initialized = True
        neon_adapter.executor.connect()
        conn_res = neon_adapter.check_connection()
        return {
            "status": "success",
            "message": "NEON_DATABASE_URL updated successfully and connection established",
            "neon_status": conn_res,
        }
    except Exception as err:
        return {
            "status": "warning",
            "message": f"Saved NEON_DATABASE_URL, but connection test notice: {err}",
            "error": str(err),
        }

# Database Router & Failover Status Endpoints
@app.get("/api/admin/database/router")
@app.get("/api/v1/admin/database/router")
async def get_database_router_status():
    """
    Exposes operational database routing state:
    - active backend (MongoDB Atlas primary vs Neon PostgreSQL secondary)
    - backend health
    - read/write backend targets
    - failover state
    - last transition & reason
    FastAPI exclusively owns database selection.
    """
    return database_router.get_status()

@app.get("/api/admin/database/failover/audit")
@app.get("/api/v1/admin/database/failover/audit")
async def get_failover_audit_log(limit: int = 20):
    """
    Returns audit log of operational database transitions:
    - timestamp, previous active database, new active database
    - transition reason, health evidence, replication lag, resource guard state, reconciliation result
    """
    return failover_manager.get_audit_log(limit)

# Controlled Replication Status & Manual Trigger Endpoints
@app.get("/api/admin/database/replication")
@app.get("/api/v1/admin/database/replication")
async def get_replication_status():
    """
    Returns controlled replication status:
    - replication lag (seconds)
    - last successful replication
    - checkpoints per entity
    - records succeeded / failed
    - Neon storage & write volume estimate
    - Neon budget guard metrics
    """
    return replication_manager.get_status()

@app.post("/api/admin/database/replication/run")
@app.post("/api/v1/admin/database/replication/run")
async def trigger_replication_run(
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """
    Administratively triggers a bounded incremental replication run from MongoDB to Neon.
    """
    verify_admin_key(x_admin_api_key, authorization)
    return await replication_manager.execute_incremental_replication(trigger_source="admin_api")

# Neon Budget Guard Resource Protection Endpoints
@app.get("/api/admin/database/neon-guard")
@app.get("/api/v1/admin/database/neon-guard")
async def get_neon_budget_guard_status():
    """
    Exposes strict Neon resource-protection status:
    - safety state (OPTIMAL, DEGRADED, RESOURCE_LIMITED, CIRCUIT_TRIPPED)
    - PredictPro estimated usage (storage, daily/monthly egress, queries, rows)
    - Neon reported usage (if configured via official administrative integration)
    - Configured safety thresholds (headroom below provider limits)
    """
    return neon_budget_guard.get_metrics()

@app.post("/api/admin/database/neon-guard/prune")
@app.post("/api/v1/admin/database/neon-guard/prune")
async def trigger_neon_retention_prune(
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """
    Prunes expired operational records outside failover window.
    """
    verify_admin_key(x_admin_api_key, authorization)
    return await neon_budget_guard.prune_expired_failover_data(neon_adapter.executor)

# Model Configuration Endpoints
@app.get("/api/model/config", response_model=ModelConfigResponse)
@app.get("/api/settings/model", response_model=ModelConfigResponse)
async def get_model_config():
    current = model_service.get_active_model()
    is_prod = current in {"ELO", "POISSON", "ELO + POISSON"}
    return ModelConfigResponse(
        activeModel=current,  # type: ignore
        tier="production" if is_prod else "evaluation",
        updatedAt=datetime.now(timezone.utc).isoformat(),
        isProductionReady=is_prod,
    )

@app.post("/api/model/config", response_model=ModelConfigResponse)
@app.post("/api/settings/model", response_model=ModelConfigResponse)
async def set_model_config(
    payload: ModelConfigRequest,
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    verify_admin_key(x_admin_api_key, authorization)
    return model_service.set_active_model(payload.activeModel)


# =============================================================
# v1 DATA SOURCES API (Phase 1, Phase 2, Phase 3 Endpoints)
# =============================================================

@app.get("/api/v1/events")
async def get_v1_events(
    sport: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    from_time: Optional[str] = Query(None, alias="from"),
    to_time: Optional[str] = Query(None, alias="to"),
):
    try:
        if status == "live":
            events = await database_router.fixtures.get_live_fixtures(sport)
        else:
            events = await database_router.fixtures.get_recent_completed(sport, hours=72)
    except Exception as e:
        events = []
    return {
        "count": len(events),
        "filters": {"sport": sport or "all", "status": status or "all", "from": from_time, "to": to_time},
        "data": events,
    }

@app.get("/api/v1/events/{event_id}")
async def get_v1_event_by_id(event_id: str):
    event = await database_router.fixtures.get_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found in operational store")
    return {
        "data": event,
        "current_status": event.get("status"),
        "last_seen_at": event.get("last_seen_at"),
        "is_stale": event.get("is_stale", False),
    }

@app.post("/api/v1/admin/sports-skills/sync")
async def sync_sports_skills(
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    verify_admin_key(x_admin_api_key, authorization)
    record = await sync_service.execute_refresh()
    return record

@app.get("/api/v1/admin/sports-skills/sync-runs")
async def get_sports_skills_sync_runs(
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    verify_admin_key(x_admin_api_key, authorization)
    runs = await database_router.refresh_state.get_runs()
    return {"total_runs": len(runs), "runs": runs}

@app.post("/api/v1/admin/sports-skills/reconcile")
async def reconcile_sports_skills(
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    verify_admin_key(x_admin_api_key, authorization)
    reconciled = await mongo_manager.reconcile_events()
    return {"status": "success", "reconciled_count": reconciled, "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/api/v1/health/data-sources")
async def get_data_sources_health():
    manifests = await mongo_manager.get_dataset_manifests()
    versions: Dict[str, str] = {}
    for m in manifests:
        s_vers = m.get("source_versions", {})
        versions.update(s_vers)

    db_router_status = database_router.get_status()
    mongo_health = db_router_status.get("backendHealth", {}).get("mongodb", {})
    redis_health = await redis_client.check_connection()
    duckdb_health = duckdb_engine.check_connection()
    
    # Real multi-sport capability checks for SportsSkills
    (
        fb_status,
        bk_status,
        bb_status,
        hk_status,
        f1_status,
    ) = await asyncio.gather(
        football_adapter.check_connection(),
        basketball_adapter.check_connection(),
        baseball_adapter.check_connection(),
        sports_skills_hockey_provider.check_connection(),
        sports_skills_f1_provider.check_connection(),
    )
    sports_capabilities = {
        "football": fb_status.get("status") == "connected",
        "basketball": bk_status.get("status") == "connected",
        "baseball": bb_status.get("status") == "connected",
        "hockey": hk_status.get("status") == "connected",
        "formula_1": f1_status.get("status") == "connected",
    }
    sports_operational = any(sports_capabilities.values())

    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "sports_skills": {
                "status": "operational" if sports_operational else "degraded",
                "provider": "machina-sports/sports-skills",
                "capabilities": sports_capabilities,
                "credentials_exposed": False
            },
            "openfootball": {
                "status": "available",
                "repository": "openfootball/football.json",
                "active_version": versions.get("openfootball", "e6744429ee")
            },
            "soccer_dataset": {
                "status": "available",
                "repository": "v-eatpizzanot/soccer-dataset",
                "active_version": versions.get("soccer-dataset", "af71e692ed")
            },
            "every_game_ever": {
                "status": "available",
                "repository": "roham/every-game-ever",
                "active_version": versions.get("every-game-ever", "a8fbc75826")
            },
            "nba_elo": {
                "status": "available",
                "repository": "Neil-Paine-1/NBA-elo",
                "active_version": versions.get("nba-elo", "9a485bacef")
            },
            "retrosheet_mlb": {
                "status": "available",
                "repository": "Neil-Paine-1/MLB-WAR-data-historical",
                "active_version": versions.get("retrosheet-mlb", "2b914aa")
            },
        },
        "mongodb": {"status": mongo_health["status"], "role": "primary", "credentials_exposed": False},
        "neon": {
            "status": database_router.get_status().get("backendHealth", {}).get("neon", {}).get("status", "disconnected"),
            "role": "secondary_failover",
            "credentials_exposed": False,
        },
        "database_router": database_router.get_status(),
        "database_replication": replication_manager.get_status(),
        "redis": {"status": redis_health["status"], "credentials_exposed": False},
        "duckdb": {
            "status": duckdb_health["status"],
            "registered_views": duckdb_health.get("registeredViews", []),
            "catalog_rows": duckdb_health.get("inMemoryCatalogRows", 0),
            "credentials_exposed": False,
        },
    }

@app.post("/api/v1/admin/historical/ingest")
async def start_historical_ingest(
    sport: str = Query("football"),
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    verify_admin_key(x_admin_api_key, authorization)
    manifest = await historical_ingestion_service.ingest_sport(sport)
    return manifest

@app.get("/api/v1/admin/historical/datasets")
async def get_historical_datasets(
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    verify_admin_key(x_admin_api_key, authorization)
    manifests = await mongo_manager.get_dataset_manifests()
    return {"count": len(manifests), "datasets": manifests}

@app.get("/api/v1/admin/historical/coverage")
async def get_historical_coverage(
    sport: Optional[str] = Query(None),
    x_admin_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    verify_admin_key(x_admin_api_key, authorization)
    views = duckdb_engine._registered_views
    target_sport = sport or "all"
    is_covered = (f"{sport}_matches" in views) if (sport and sport != "all") else len(views) > 0
    return {
        "sport": target_sport,
        "coverage_status": "active" if is_covered else "pending_ingestion",
        "registered_views": views,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# Standard ASGI entrypoint for deployment platforms (Vercel, Cloud Run, etc.)
application = app

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("backend.app:app", host="0.0.0.0", port=port, reload=True)

