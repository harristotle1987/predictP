"""
Health Service.
Provides comprehensive health checks for all PredictPro storage, routing, and provider subsystems.
Tracks:
- MongoDB Atlas primary
- Neon PostgreSQL secondary (automatic failover)
- DatabaseRouter state & failover readiness
- Upstash Redis cache
- Cloudflare R2 analytics storage
- DuckDB analytical engine
- SportsSkills provider capabilities
"""

import asyncio
import socket
import time
import traceback
from datetime import datetime, timezone
from typing import Dict, Any
from urllib.parse import urlparse

from backend.config import settings
from backend.db.database_router import database_router
from backend.db.mongodb_adapter import mongodb_adapter
from backend.db.neon_adapter import neon_adapter
from backend.db.failover_manager import failover_manager
from backend.db.neon_budget_guard import neon_budget_guard
from backend.db.replication_manager import replication_manager
from backend.db.redis_client import redis_client
from backend.db.r2_storage import r2_manager
from backend.db.duckdb_engine import duckdb_engine
from backend.providers.football_adapter import football_adapter
from backend.providers.basketball_adapter import basketball_adapter
from backend.providers.baseball_adapter import baseball_adapter
from backend.providers.sports_skills_hockey_provider import sports_skills_hockey_provider
from backend.providers.sports_skills_f1_provider import sports_skills_f1_provider
from backend.services.model_service import model_service


class HealthService:
    """Aggregates and formats production health status."""

    async def get_health_status(self) -> Dict[str, Any]:
        """Runs concurrent subsystem health checks and formats the full health payload."""
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
            asyncio.to_thread(mongodb_adapter.check_connection),
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
            sports_overall = "connected"
        elif connected_count > 0:
            sports_overall = "degraded"
        else:
            sports_overall = "disconnected"

        sports_skills_status = {
            "status": sports_overall,
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

        # Format MongoDB block
        mongo_raw_status = str(mongo_conn.get("status", "disconnected")).lower()
        mongo_status_str = "connected" if mongo_raw_status in ("connected", "ok", "healthy") else ("degraded" if mongo_raw_status == "degraded" else "disconnected")
        mongo_db_block = {
            "status": mongo_status_str,
            "latencyMs": mongo_conn.get("latencyMs", 0),
            "error": mongo_conn.get("error") or (mongo_conn.get("details") if mongo_status_str != "connected" else None),
            "cluster": mongo_conn.get("cluster", "predictpro"),
            "poolActive": mongo_conn.get("poolActive", 1 if mongo_status_str == "connected" else 0),
        }

        # Format Neon block
        neon_raw_status = str(neon_conn.get("status", "disconnected")).lower()
        neon_status_str = "connected" if neon_raw_status in ("connected", "ok", "healthy", "configured_standby", "ready") else ("degraded" if neon_raw_status == "degraded" else "disconnected")
        neon_schema_ok = neon_adapter.check_schema_compatibility()
        neon_failover_ready = (
            neon_status_str == "connected"
            and neon_schema_ok
            and guard_metrics.get("safetyState") not in ("RESOURCE_LIMITED", "CIRCUIT_TRIPPED")
        )
        neon_postgres_block = {
            "status": neon_status_str,
            "latencyMs": neon_conn.get("latencyMs", 0),
            "error": neon_conn.get("error") or (neon_conn.get("details") if neon_status_str != "connected" else None),
            "failoverReady": neon_failover_ready,
            "schemaCompatible": neon_schema_ok,
            "safetyState": guard_metrics.get("safetyState", "OPTIMAL"),
        }

        neon_dashboard = {
            "connectionStatus": neon_status_str,
            "replicationStatus": repl_status.get("lastSummary", {}).get("status", "idle"),
            "replicationLagSeconds": repl_status.get("lastSummary", {}).get("replication_lag_seconds", 0.0),
            "safetyState": guard_metrics.get("safetyState", "OPTIMAL"),
            "failoverReady": neon_failover_ready,
            "predictproEstimatedUsage": guard_metrics.get("predictproEstimatedUsage", {}),
            "neonReportedUsage": guard_metrics.get("neonReportedUsage"),
            "configuredSafetyThresholds": guard_metrics.get("configuredSafetyThresholds", {}),
            "circuitBreakerTripped": guard_metrics.get("circuitBreakerTripped", False),
        }

        failover_st = (
            failover_manager.failover_state.value
            if hasattr(failover_manager.failover_state, "value")
            else str(failover_manager.failover_state)
        )

        overall_app_status = (
            "healthy"
            if neon_status_str in ("connected", "CONNECTED")
            else "degraded"
        )

        return {
            "status": overall_app_status,
            "service": "predictpro-fastapi-engine",
            "version": "2.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),

            # Exact requirement keys for Fix 7
            "MongoDB": mongo_status_str,
            "Neon": neon_status_str,
            "Neon failover ready": neon_failover_ready,
            "Redis": "connected" if redis_status.get("status") in ("connected", "healthy", "ok") else "disconnected",
            "R2": "connected" if r2_status.get("status") in ("connected", "healthy", "ok") else "disconnected",
            "DuckDB": "connected" if duckdb_status.get("status") in ("connected", "healthy", "ok") else "disconnected",
            "SportsSkills": "connected" if sports_overall != "disconnected" else "disconnected",

            "activeDatabase": "neon",
            "failoverState": failover_st,
            "mongoDb": mongo_db_block,
            "neonPostgres": neon_postgres_block,
            "mongoDbStatus": mongo_status_str,
            "neonStatus": neon_status_str,
            "neonFailoverReady": neon_failover_ready,
            "replicationLag": repl_status.get("lastSummary", {}).get("replication_lag_seconds", 0.0),
            "lastFailover": failover_manager._failover_started_at_iso,
            "lastRecovery": failover_manager._last_recovery_iso,
            "reconciliationStatus": failover_manager._last_reconciliation_result,
            "neonResourceGuardStatus": guard_metrics.get("safetyState", "OPTIMAL"),
            "neonDashboard": neon_dashboard,
            "databaseRouter": router_status,
            "redis": redis_status,
            "r2Storage": r2_status,
            "duckDb": duckdb_status,
            "sportsSkills": sports_skills_status,
            "activeModel": model_service.get_active_model(),
        }

    async def get_diagnostics_report(self) -> Dict[str, Any]:
        """
        Deep, verbose diagnostic inspector for pinpointing connection, network,
        DNS, SSL, authentication, driver, schema, or budget guard issues across all subsystems,
        especially Neon PostgreSQL.
        """
        raw_health = await self.get_health_status()
        
        # 1. Deep Neon PostgreSQL Diagnostics
        neon_url = settings.neon_database_url or ""
        neon_diag: Dict[str, Any] = {
            "configured": bool(neon_url),
            "status": "disconnected",
            "connectionStringMasked": None,
            "host": None,
            "port": 5432,
            "database": None,
            "user": None,
            "sslMode": None,
            "dns": {"resolved": False, "ips": [], "latencyMs": 0.0, "error": None},
            "tcp": {"reachable": False, "latencyMs": 0.0, "error": None},
            "driver": {"name": "psycopg", "installed": False, "version": None, "error": None},
            "queryTest": {"executed": False, "success": False, "latencyMs": 0.0, "serverVersion": None, "error": None, "traceback": None},
            "tablesFound": [],
            "schemaCompatible": False,
            "failoverReady": False,
            "safetyState": "UNKNOWN",
            "suggestedRemediation": None,
        }

        if neon_url:
            try:
                parsed = urlparse(neon_url)
                # Mask credentials
                masked_netloc = parsed.netloc
                if "@" in masked_netloc:
                    user_part, host_part = masked_netloc.split("@", 1)
                    if ":" in user_part:
                        u, _ = user_part.split(":", 1)
                        masked_netloc = f"{u}:***@{host_part}"
                    else:
                        masked_netloc = f"{user_part}:***@{host_part}"
                neon_diag["connectionStringMasked"] = parsed._replace(netloc=masked_netloc).geturl()
                neon_diag["host"] = parsed.hostname
                neon_diag["port"] = parsed.port or 5432
                neon_diag["database"] = parsed.path.lstrip("/") if parsed.path else None
                neon_diag["user"] = parsed.username
                
                # Query params (sslmode, etc.)
                if parsed.query:
                    q_params = dict(qp.split("=", 1) for qp in parsed.query.split("&") if "=" in qp)
                    neon_diag["sslMode"] = q_params.get("sslmode", "require")
                else:
                    neon_diag["sslMode"] = "require"
            except Exception as pe:
                neon_diag["connectionStringMasked"] = "Invalid URL format"
                neon_diag["suggestedRemediation"] = f"Fix connection URL syntax: {pe}"

            # Check driver
            try:
                import psycopg
                neon_diag["driver"]["installed"] = True
                neon_diag["driver"]["version"] = getattr(psycopg, "__version__", "3.x")
            except ImportError as ie:
                neon_diag["driver"]["installed"] = False
                neon_diag["driver"]["error"] = str(ie)
                neon_diag["suggestedRemediation"] = "Install psycopg via `python3 -m pip install 'psycopg[binary,pool]' --break-system-packages`"

            # DNS resolution test
            host = neon_diag["host"]
            port = neon_diag["port"] or 5432
            if host:
                t0 = time.perf_counter()
                try:
                    addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                    t_dns = (time.perf_counter() - t0) * 1000
                    ips = list(set([res[4][0] for res in addr_info]))
                    neon_diag["dns"]["resolved"] = True
                    neon_diag["dns"]["ips"] = ips
                    neon_diag["dns"]["latencyMs"] = round(t_dns, 2)
                except Exception as de:
                    t_dns = (time.perf_counter() - t0) * 1000
                    neon_diag["dns"]["resolved"] = False
                    neon_diag["dns"]["latencyMs"] = round(t_dns, 2)
                    neon_diag["dns"]["error"] = str(de)
                    neon_diag["suggestedRemediation"] = f"DNS resolution failed for host '{host}'. Check internet connection and host validity."

                # TCP socket test
                if neon_diag["dns"]["resolved"]:
                    t0 = time.perf_counter()
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(3.0)
                        s.connect((host, port))
                        s.close()
                        t_tcp = (time.perf_counter() - t0) * 1000
                        neon_diag["tcp"]["reachable"] = True
                        neon_diag["tcp"]["latencyMs"] = round(t_tcp, 2)
                    except Exception as te:
                        t_tcp = (time.perf_counter() - t0) * 1000
                        neon_diag["tcp"]["reachable"] = False
                        neon_diag["tcp"]["latencyMs"] = round(t_tcp, 2)
                        neon_diag["tcp"]["error"] = str(te)
                        neon_diag["suggestedRemediation"] = f"TCP connection to {host}:{port} timed out or was refused. Check firewall or Neon project status."

            # Query test via adapter
            if neon_diag["driver"]["installed"]:
                t0 = time.perf_counter()
                try:
                    conn_check = neon_adapter.check_connection()
                    t_query = (time.perf_counter() - t0) * 1000
                    neon_diag["queryTest"]["executed"] = True
                    neon_diag["queryTest"]["latencyMs"] = round(t_query, 2)
                    
                    if conn_check.get("status") in ("connected", "ok", "healthy", "configured_standby", "ready"):
                        neon_diag["status"] = "connected"
                        neon_diag["queryTest"]["success"] = True
                        
                        # Retrieve server version
                        try:
                            v_row = neon_adapter.executor.fetch_one("SELECT version();")
                            if v_row:
                                neon_diag["queryTest"]["serverVersion"] = v_row.get("version")
                        except Exception:
                            pass
                            
                        # Retrieve tables
                        try:
                            t_rows = neon_adapter.executor.fetch_all("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name LIMIT 20;")
                            neon_diag["tablesFound"] = [r.get("table_name") for r in t_rows if r.get("table_name")]
                        except Exception:
                            pass
                            
                        # Schema check
                        neon_diag["schemaCompatible"] = neon_adapter.check_schema_compatibility()
                        neon_diag["failoverReady"] = True
                    else:
                        neon_diag["status"] = "disconnected"
                        neon_diag["queryTest"]["success"] = False
                        err_msg = conn_check.get("error") or conn_check.get("details") or "Connection failed"
                        neon_diag["queryTest"]["error"] = str(err_msg)
                        neon_diag["suggestedRemediation"] = f"Neon query test failed: {err_msg}"
                except Exception as qe:
                    t_query = (time.perf_counter() - t0) * 1000
                    neon_diag["status"] = "disconnected"
                    neon_diag["queryTest"]["executed"] = True
                    neon_diag["queryTest"]["success"] = False
                    neon_diag["queryTest"]["latencyMs"] = round(t_query, 2)
                    neon_diag["queryTest"]["error"] = str(qe)
                    neon_diag["queryTest"]["traceback"] = traceback.format_exc()
                    neon_diag["suggestedRemediation"] = f"Error executing Neon query: {qe}"
        else:
            neon_diag["suggestedRemediation"] = "NEON_DATABASE_URL is not set. Add your Neon connection string to /.env"

        guard_metrics = neon_budget_guard.get_metrics()
        neon_diag["safetyState"] = guard_metrics.get("safetyState", "OPTIMAL")
        neon_diag["budgetGuardMetrics"] = guard_metrics

        return {
            "status": raw_health.get("status", "healthy"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "predictpro-engine",
            "diagnostics": {
                "neon": neon_diag,
                "mongodb": raw_health.get("mongoDb", {}),
                "redis": raw_health.get("redis", {}),
                "r2Storage": raw_health.get("r2Storage", {}),
                "duckDb": raw_health.get("duckDb", {}),
                "sportsSkills": raw_health.get("sportsSkills", {}),
                "databaseRouter": raw_health.get("databaseRouter", {}),
                "replication": raw_health.get("neonDashboard", {}),
            },
            "summary": {
                "neonConnected": neon_diag["status"] == "connected",
                "neonLatencyMs": neon_diag["queryTest"]["latencyMs"] or neon_diag["tcp"]["latencyMs"],
                "activePrimaryStore": "neon",
                "totalSubsystemsHealthy": sum([
                    1 if neon_diag["status"] == "connected" else 0,
                    1 if raw_health.get("duckDb", {}).get("status") == "connected" else 0,
                    1 if raw_health.get("sportsSkills", {}).get("status") in ("connected", "degraded") else 0,
                ]),
            }
        }


health_service = HealthService()
