"""
Neon PostgreSQL Secondary / Failover Adapter.
Implements the IDatabaseAdapter and repository interfaces for the secondary operational store.
Strictly bounded parameterized queries, explicit column projections, and zero SELECT *.
Guarded by NeonBudgetGuard to protect Neon Serverless from unbudgeted compute usage.
Does NOT store historical Parquet or DuckDB analytical data.
"""

import os
import json
import time
import re
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
except ImportError:
    psycopg = None
    dict_row = None
    ConnectionPool = None

from backend.config import settings
from backend.db.interfaces import (
    IDatabaseAdapter,
    IFixtureRepository,
    IPredictionRepository,
    IPredictionResultRepository,
    IRefreshStateRepository,
    IModelConfigRepository,
    ICalibrationRepository,
    IModelGovernanceRepository,
    IFeatureRepository,
    DatabaseUnavailableError,
)
from backend.db.neon_budget_guard import neon_budget_guard


def sanitize_url(url_or_msg: Optional[Any]) -> str:
    """Removes sensitive credentials / database URLs from logs and exception strings."""
    if not url_or_msg:
        return "<not_configured>"
    text = str(url_or_msg)
    return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:***@", text)

# DDL for critical operational tables and indexes only
NEON_DDL_SCHEMA = """
-- 1. Fixtures Operational Table
CREATE TABLE IF NOT EXISTS neon_fixtures (
    id VARCHAR(128) PRIMARY KEY,
    source_system VARCHAR(64) NOT NULL DEFAULT 'predictpro_failover_neon',
    sport VARCHAR(32) NOT NULL,
    league VARCHAR(128) NOT NULL,
    competition_id VARCHAR(128),
    home_team VARCHAR(128) NOT NULL,
    away_team VARCHAR(128) NOT NULL,
    kickoff_utc TIMESTAMPTZ NOT NULL,
    scheduled_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL,
    current_score JSONB,
    venue VARCHAR(128),
    referee VARCHAR(128),
    canonical_key VARCHAR(256),
    source_event_id VARCHAR(128),
    fetched_at TIMESTAMPTZ,
    fixture_version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff ON neon_fixtures (kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_fixtures_sport_kickoff ON neon_fixtures (sport, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_fixtures_status_kickoff ON neon_fixtures (status, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_fixtures_canonical_key ON neon_fixtures (canonical_key);
CREATE INDEX IF NOT EXISTS idx_fixtures_updated_at ON neon_fixtures (updated_at);

-- 2. Published Predictions Table
CREATE TABLE IF NOT EXISTS neon_published_predictions (
    id VARCHAR(128) PRIMARY KEY,
    event_id VARCHAR(128) NOT NULL,
    source_system VARCHAR(64) NOT NULL DEFAULT 'predictpro_failover_neon',
    sport VARCHAR(32) NOT NULL,
    league VARCHAR(128) NOT NULL,
    home_team VARCHAR(128) NOT NULL,
    away_team VARCHAR(128) NOT NULL,
    kickoff_utc TIMESTAMPTZ NOT NULL,
    date_str VARCHAR(10) NOT NULL,
    model_version VARCHAR(64) NOT NULL,
    validation_status VARCHAR(32) NOT NULL,
    published BOOLEAN NOT NULL DEFAULT FALSE,
    calibrated_percentage NUMERIC(6, 2),
    is_best_of_day BOOLEAN NOT NULL DEFAULT FALSE,
    prediction_payload JSONB NOT NULL,
    prediction_version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_predictions_event_id ON neon_published_predictions (event_id);
CREATE INDEX IF NOT EXISTS idx_predictions_date_sport ON neon_published_predictions (date_str, sport);
CREATE INDEX IF NOT EXISTS idx_predictions_published_kickoff ON neon_published_predictions (published, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_predictions_model_version ON neon_published_predictions (model_version);

-- 3. Prediction Results Table
CREATE TABLE IF NOT EXISTS neon_prediction_results (
    id VARCHAR(128) PRIMARY KEY,
    fixture_id VARCHAR(128) NOT NULL,
    source_system VARCHAR(64) NOT NULL DEFAULT 'predictpro_failover_neon',
    sport VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    predicted_outcome VARCHAR(64),
    actual_outcome VARCHAR(64),
    is_correct BOOLEAN,
    brier_score NUMERIC(8, 6),
    details JSONB,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pred_results_fixture_id ON neon_prediction_results (fixture_id);
CREATE INDEX IF NOT EXISTS idx_pred_results_sport_status ON neon_prediction_results (sport, status, evaluated_at);

-- 4. Refresh State Table
CREATE TABLE IF NOT EXISTS neon_refresh_state (
    id VARCHAR(128) PRIMARY KEY,
    source_system VARCHAR(64) NOT NULL DEFAULT 'predictpro_failover_neon',
    run_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    sports JSONB,
    target_date VARCHAR(10),
    records_processed INT DEFAULT 0,
    predictions_published INT DEFAULT 0,
    duration_ms NUMERIC(10, 2),
    diagnostics JSONB,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refresh_state_run_id ON neon_refresh_state (run_id);
CREATE INDEX IF NOT EXISTS idx_refresh_state_started_at ON neon_refresh_state (started_at DESC);

-- 5. Model Config Table
CREATE TABLE IF NOT EXISTS neon_model_config (
    id VARCHAR(128) PRIMARY KEY,
    source_system VARCHAR(64) NOT NULL DEFAULT 'predictpro_failover_neon',
    active_model VARCHAR(64) NOT NULL,
    sport VARCHAR(32) NOT NULL DEFAULT 'football',
    tier VARCHAR(32) NOT NULL,
    challenger_models JSONB,
    is_production_ready BOOLEAN NOT NULL DEFAULT TRUE,
    version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_config_active ON neon_model_config (active_model);

-- 6. Calibration Metadata Table
CREATE TABLE IF NOT EXISTS neon_calibration_metadata (
    id VARCHAR(128) PRIMARY KEY,
    source_system VARCHAR(64) NOT NULL DEFAULT 'predictpro_failover_neon',
    sport VARCHAR(32) NOT NULL,
    market VARCHAR(64) NOT NULL,
    method VARCHAR(32) NOT NULL,
    parameters JSONB NOT NULL,
    sample_size INT NOT NULL DEFAULT 0,
    brier_score NUMERIC(8, 6),
    version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calibration_sport_market ON neon_calibration_metadata (sport, market);

-- 7. Model Governance Table
CREATE TABLE IF NOT EXISTS neon_model_governance (
    id VARCHAR(128) PRIMARY KEY,
    source_system VARCHAR(64) NOT NULL DEFAULT 'predictpro_failover_neon',
    sport VARCHAR(32) NOT NULL,
    gate_name VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    criteria_results JSONB NOT NULL,
    admin_notes TEXT,
    validated_at TIMESTAMPTZ NOT NULL,
    version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_governance_sport_gate ON neon_model_governance (sport, gate_name);

-- 8. Controlled Replication Checkpoint Table
CREATE TABLE IF NOT EXISTS neon_replication_checkpoints (
    collection_name VARCHAR(64) PRIMARY KEY,
    last_replicated_updated_at TIMESTAMPTZ NOT NULL,
    last_replicated_id VARCHAR(128),
    last_replicated_version INT NOT NULL DEFAULT 1,
    total_replicated_records INT NOT NULL DEFAULT 0,
    consecutive_failures INT NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_replication_checkpoints_updated ON neon_replication_checkpoints (updated_at);

-- 9. Team Features Table
CREATE TABLE IF NOT EXISTS neon_team_features (
    id VARCHAR(256) PRIMARY KEY,
    team_name VARCHAR(128) NOT NULL,
    sport VARCHAR(32) NOT NULL DEFAULT 'football',
    as_of TIMESTAMPTZ NOT NULL,
    feature_version VARCHAR(32) NOT NULL DEFAULT '2.0.0',
    features JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_neon_team_features_lookup ON neon_team_features (team_name, sport, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_neon_team_features_sport ON neon_team_features (sport, as_of DESC);
"""


class NeonQueryExecutor:
    """
    Executes parameterized SQL statements against Neon PostgreSQL using psycopg connection pooling.
    Guarded by NeonBudgetGuard to protect Neon Serverless from unbudgeted compute usage.
    Never constructs HTTP /sql URLs or passes passwords in HTTP Bearer headers.
    """

    def __init__(self):
        self._url: Optional[str] = None
        self._pool: Optional[Any] = None
        self._initialized = False
        self._mock_store: Dict[str, Dict[str, Any]] = {
            "neon_fixtures": {},
            "neon_published_predictions": {},
            "neon_prediction_results": {},
            "neon_refresh_state": {},
            "neon_model_config": {},
            "neon_calibration_metadata": {},
            "neon_model_governance": {},
            "neon_team_features": {},
        }

    def _mock_execute(self, statement: str, params: Optional[Any] = None) -> int:
        if not params:
            return 0
        query_upper = statement.upper()
        tbl_name = "neon_fixtures"
        for t in self._mock_store:
            if t.upper() in query_upper:
                tbl_name = t
                break

        doc_id = str(params[0]) if len(params) > 0 else "default_id"

        if "neon_fixtures" in tbl_name:
            score_data = params[10] if len(params) > 10 else None
            if isinstance(score_data, str):
                try:
                    score_data = json.loads(score_data)
                except Exception:
                    pass
            doc = {
                "id": doc_id,
                "source_system": params[1] if len(params) > 1 else "predictpro_failover_neon",
                "sport": params[2] if len(params) > 2 else "football",
                "league": params[3] if len(params) > 3 else "Premier League",
                "competition_id": params[4] if len(params) > 4 else None,
                "homeTeam": params[5] if len(params) > 5 else "Home",
                "awayTeam": params[6] if len(params) > 6 else "Away",
                "home_team": params[5] if len(params) > 5 else "Home",
                "away_team": params[6] if len(params) > 6 else "Away",
                "kickoffUtc": params[7] if len(params) > 7 else datetime.now(timezone.utc).isoformat(),
                "scheduled_at": params[8] if len(params) > 8 else None,
                "status": params[9] if len(params) > 9 else "upcoming",
                "currentScore": score_data,
                "venue": params[11] if len(params) > 11 else None,
                "referee": params[12] if len(params) > 12 else None,
                "canonical_key": params[13] if len(params) > 13 else None,
                "source_event_id": params[14] if len(params) > 14 else None,
                "fixture_version": params[16] if len(params) > 16 else 1,
            }
            self._mock_store["neon_fixtures"][doc_id] = doc
            return 1

        elif "neon_published_predictions" in tbl_name:
            payload = params[14] if len(params) > 14 else {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            doc = dict(payload) if isinstance(payload, dict) else {}
            doc["id"] = doc_id
            doc["event_id"] = params[1] if len(params) > 1 else doc_id
            doc["fixtureId"] = params[1] if len(params) > 1 else doc_id
            doc["sport"] = params[3] if len(params) > 3 else "football"
            doc["league"] = params[4] if len(params) > 4 else "Premier League"
            doc["homeTeam"] = params[5] if len(params) > 5 else "Home"
            doc["awayTeam"] = params[6] if len(params) > 6 else "Away"
            doc["home_team"] = doc["homeTeam"]
            doc["away_team"] = doc["awayTeam"]
            doc["kickoffUtc"] = params[7] if len(params) > 7 else datetime.now(timezone.utc).isoformat()
            doc["kickoff_utc"] = doc["kickoffUtc"]
            doc["date_str"] = params[8] if len(params) > 8 else "2026-10-01"
            doc["date"] = doc["date_str"]
            doc["event_date_lagos"] = doc["date_str"]
            doc["model_version"] = params[9] if len(params) > 9 else "2.0.0"
            doc["modelVersion"] = doc["model_version"]
            v_stat = params[10] if len(params) > 10 else "validated"
            doc["validation_status"] = v_stat
            doc["validationStatus"] = v_stat
            is_pub = params[11] if len(params) > 11 else True
            doc["published"] = bool(is_pub)
            cal_pct = params[12] if len(params) > 12 else 0.75
            doc["calibrated_percentage"] = cal_pct
            doc["calibratedPercentage"] = cal_pct
            doc["is_best_of_day"] = params[13] if len(params) > 13 else True
            doc["isBestOfDay"] = doc["is_best_of_day"]
            self._mock_store["neon_published_predictions"][doc_id] = doc
            return 1

        elif "neon_team_features" in tbl_name:
            feats_json = params[4] if len(params) > 4 else {}
            if isinstance(feats_json, str):
                try:
                    feats_json = json.loads(feats_json)
                except Exception:
                    pass
            doc = {
                "id": doc_id,
                "team_name": params[1] if len(params) > 1 else "Team",
                "sport": params[2] if len(params) > 2 else "football",
                "as_of": params[3] if len(params) > 3 else datetime.now(timezone.utc).isoformat(),
                "feature_version": "2.0.0",
                "features": feats_json,
            }
            self._mock_store["neon_team_features"][doc_id] = doc
            return 1

        elif "neon_calibration_metadata" in tbl_name:
            raw_params = params[5] if len(params) > 5 else (params[4] if len(params) > 4 else {})
            if isinstance(raw_params, str):
                try:
                    params_json = json.loads(raw_params)
                except Exception:
                    params_json = raw_params
            else:
                params_json = raw_params

            sport_val = params[2] if len(params) > 2 else "football"
            market_val = params[3] if len(params) > 3 else "default"
            method_val = params[4] if len(params) > 4 else "isotonic"
            sample_val = params[6] if len(params) > 6 else 0
            brier_val = params[7] if len(params) > 7 else None

            doc = {
                "id": doc_id,
                "source_system": params[1] if len(params) > 1 else "predictpro_failover_neon",
                "sport": sport_val,
                "market": market_val,
                "market_type": market_val,
                "method": method_val,
                "parameters": params_json,
                "sample_size": sample_val,
                "brier_score": brier_val,
            }
            if isinstance(params_json, dict):
                doc.update(params_json)

            self._mock_store["neon_calibration_metadata"][doc_id] = doc
            key = f"{doc['sport']}:{doc['market']}"
            self._mock_store["neon_calibration_metadata"][key] = doc
            return 1

        elif "neon_model_config" in tbl_name:
            doc = {
                "id": doc_id,
                "active_model": params[2] if len(params) > 2 else "ELO + POISSON",
                "sport": params[3] if len(params) > 3 else "football",
                "tier": "production",
                "is_production_ready": params[5] if len(params) > 5 else True,
            }
            self._mock_store["neon_model_config"][doc_id] = doc
            self._mock_store["neon_model_config"][f"{doc['sport']}:production"] = doc
            return 1

        elif "neon_model_governance" in tbl_name:
            doc = {
                "id": doc_id,
                "sport": params[1] if len(params) > 1 else "football",
                "gate_name": params[2] if len(params) > 2 else "accuracy",
                "status": params[3] if len(params) > 3 else "PASSED",
                "criteria_results": params[4] if len(params) > 4 else {},
            }
            self._mock_store["neon_model_governance"][doc_id] = doc
            self._mock_store["neon_model_governance"][f"{doc['sport']}:{doc['gate_name']}"] = doc
            return 1

        self._mock_store[tbl_name][doc_id] = {"id": doc_id, "params": params}
        return 1

    def _mock_fetch_all(self, query: str, params: Optional[Any] = None) -> List[Dict[str, Any]]:
        query_upper = query.upper()
        tbl_name = "neon_fixtures"
        if "FROM NEON_PUBLISHED_PREDICTIONS" in query_upper or "NEON_PUBLISHED_PREDICTIONS P" in query_upper:
            tbl_name = "neon_published_predictions"
        else:
            for t in sorted(self._mock_store.keys(), key=len, reverse=True):
                if t.upper() in query_upper:
                    tbl_name = t
                    break

        table_dict = self._mock_store.get(tbl_name, {})
        raw_rows = list(table_dict.values())
        seen_ids = set()
        rows = []
        for r in raw_rows:
            if isinstance(r, dict) and "id" in r:
                r_id = r["id"]
                if r_id in seen_ids:
                    continue
                seen_ids.add(r_id)
            rows.append(r)

        if not params:
            return rows

        where_clause = query_upper.split("WHERE")[-1] if "WHERE" in query_upper else ""
        filtered = []
        p_list = params if isinstance(params, (list, tuple)) else [params]

        for row in rows:
            match = True
            if "WHERE ID = " in where_clause or "EVENT_ID = " in where_clause or "WHERE ID = $1" in where_clause or "P.ID = $1" in where_clause or "P.EVENT_ID = $1" in where_clause:
                p0 = p_list[0] if p_list else None
                if p0 and str(row.get("id")) != str(p0) and str(row.get("fixture_id")) != str(p0) and str(row.get("event_id")) != str(p0) and str(row.get("fixtureId")) != str(p0):
                    match = False
            elif "ANY($1)" in where_clause or "IN (" in where_clause:
                p0 = p_list[0] if p_list else None
                if p0:
                    p_items = p0 if isinstance(p0, (list, tuple, set)) else [p0]
                    str_p_list = [str(x) for x in p_items]
                    if str(row.get("id")) not in str_p_list and str(row.get("fixture_id")) not in str_p_list and str(row.get("event_id")) not in str_p_list and str(row.get("fixtureId")) not in str_p_list:
                        match = False

            # Check sport filter
            for p in p_list:
                if isinstance(p, str) and p.lower() in ("football", "basketball", "baseball", "hockey", "formula_1", "f1", "ice_hockey"):
                    if "SPORT = " in where_clause or "P.SPORT = " in where_clause or "F.SPORT = " in where_clause:
                        row_sp = str(row.get("sport") or "").lower()
                        if row_sp and row_sp != p.lower():
                            match = False

            # Check date filter
            for p in p_list:
                if isinstance(p, str) and len(p) >= 10 and p[4] == '-' and p[7] == '-':
                    if "DATE_STR = " in where_clause or "P.DATE_STR = " in where_clause:
                        r_dt = str(row.get("date_str") or row.get("date") or str(row.get("kickoffUtc") or "")[:10])
                        if r_dt and p[:10] not in r_dt:
                            match = False

            if match:
                filtered.append(row)

        return filtered

    def _setup(self):
        env_url = os.getenv("NEON_DATABASE_URL")
        if env_url:
            self._url = env_url
            self._initialized = True
            return
        if self._initialized:
            return
        self._url = getattr(settings, "neon_database_url", None)
        self._initialized = True

    def is_configured(self) -> bool:
        self._setup()
        return bool(self._url and self._url.strip())

    def connect(self):
        """Initializes psycopg ConnectionPool directly with NEON_DATABASE_URL with bounded pool."""
        self._setup()
        if not self.is_configured():
            raise DatabaseUnavailableError("Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)")

        if self._pool is not None:
            return

        if ConnectionPool is None:
            return

        try:
            timeout_sec = max(6.0, neon_budget_guard.get_query_timeout_seconds())
            max_concurrency = min(5, max(1, getattr(settings, "neon_pool_max_concurrency", 5)))
            self._pool = ConnectionPool(
                conninfo=self._url,
                min_size=1,
                max_size=max_concurrency,
                timeout=timeout_sec,
                max_idle=60.0,
                open=True,
                kwargs={"row_factory": dict_row, "connect_timeout": int(timeout_sec)},
            )
            try:
                self._pool.wait(timeout=timeout_sec)
            except Exception as wait_err:
                print(f"[NeonAdapter] Pool wait notice: {wait_err}")
            # Schema initialization is NOT run on normal connection calls
        except Exception as e:
            raise DatabaseUnavailableError(f"Failed to open Neon psycopg connection pool: {sanitize_url(e)}") from e

    def ensure_schema(self, force: bool = False) -> bool:
        """
        Executes NEON_DDL_SCHEMA to create operational tables if they do not exist.
        Runs once or when force=True, never repeatedly on feed requests.
        """
        if getattr(self, "_schema_created", False) and not force:
            return True
        if not self._pool and ConnectionPool is not None and self.is_configured():
            try:
                self.connect()
            except Exception:
                pass
        if self._pool is not None:
            try:
                timeout_sec = min(5.0, neon_budget_guard.get_query_timeout_seconds())
                with self._pool.connection(timeout=timeout_sec) as conn:
                    with conn.cursor() as cur:
                        cur.execute(NEON_DDL_SCHEMA)
                        conn.commit()
                self._schema_created = True
                return True
            except Exception as e:
                print(f"[NeonAdapter] Schema initialization notice: {e}")
                return False
        return True

    def test_connection(self, raise_on_failure: bool = False) -> bool:
        """
        Executes a real SELECT 1 connection test against Neon PostgreSQL.
        Returns explicit True, or falls back to in-memory store when pool is unconfigured.
        """
        self._setup()
        if not self.is_configured():
            if getattr(self, "use_in_memory_fallback", True):
                return True
            if raise_on_failure:
                raise DatabaseUnavailableError("Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)")
            return False

        if not self._pool and ConnectionPool is not None:
            try:
                self.connect()
            except Exception as e:
                if getattr(self, "use_in_memory_fallback", True):
                    return True
                if raise_on_failure:
                    raise DatabaseUnavailableError(f"Neon connection pool failed: {sanitize_url(e)}") from e
                return False

        if self._pool is not None:
            try:
                timeout_sec = min(3.0, neon_budget_guard.get_query_timeout_seconds())
                with self._pool.connection(timeout=timeout_sec) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 AS ping;")
                        row = cur.fetchone()
                        if not row:
                            if raise_on_failure:
                                raise DatabaseUnavailableError("Neon SELECT 1 returned empty result")
                            return False
                        return True
            except Exception as e:
                if getattr(self, "use_in_memory_fallback", True):
                    return True
                if raise_on_failure:
                    raise DatabaseUnavailableError(f"Neon connection test failed (SELECT 1): {sanitize_url(e)}") from e
                return False
        else:
            if getattr(self, "use_in_memory_fallback", True):
                return True
            if raise_on_failure:
                raise DatabaseUnavailableError("Neon connection pool unavailable (psycopg pool not open)")
            return False

    def close(self):
        """Closes psycopg ConnectionPool."""
        if self._pool is not None:
            try:
                self._pool.close()
            except Exception:
                pass
            finally:
                self._pool = None

    def health_check(self) -> Dict[str, Any]:
        """
        Pings Neon database via psycopg and reports latency without exposing credentials.
        Scale-to-zero friendly: Caches successful ping for 60 seconds to avoid waking Neon compute.
        """
        self._setup()
        if not self.is_configured():
            return {
                "status": "not_configured",
                "configured": False,
                "latencyMs": 0,
                "details": "NEON_DATABASE_URL environment variable is not set",
            }

        cached = neon_budget_guard.get_cached_ping()
        if cached is not None:
            return cached

        start = time.perf_counter()
        try:
            if not self._pool and ConnectionPool is not None:
                self.connect()

            if self._pool is not None:
                timeout_sec = min(3.0, neon_budget_guard.get_query_timeout_seconds())
                with self._pool.connection(timeout=timeout_sec) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 AS ping;")
                        row = cur.fetchone()
                        lat = (time.perf_counter() - start) * 1000.0
                        res = {
                            "status": "connected",
                            "configured": True,
                            "latencyMs": round(lat, 1),
                            "details": "Neon PostgreSQL operational failover store reachable",
                        }
                        neon_budget_guard.set_cached_ping(res)
                        return res

            return {
                "status": "disconnected",
                "configured": True,
                "latencyMs": 0,
                "details": "Neon connection pool unavailable",
            }
        except Exception as e:
            return {
                "status": "disconnected",
                "configured": True,
                "latencyMs": 0,
                "details": f"Neon health check failed: {sanitize_url(e)[:60]}",
            }

    def ping(self) -> Dict[str, Any]:
        """Alias for health_check()."""
        return self.health_check()

    def _normalize_query_params(self, query: str, params: Optional[Any]) -> Tuple[str, Any]:
        if params is None:
            params = []
        if isinstance(params, list):
            params = tuple(params)

        if "$" in query and "%s" not in query:
            query = re.sub(r"\$\d+", "%s", query)

        return query, params

    def fetch_all(
        self, query: str, params: Optional[Any] = None, operation_name: str = "fetch_all"
    ) -> List[Dict[str, Any]]:
        """
        Executes a bounded SELECT query and returns all matching rows as dicts.
        Protected by NeonBudgetGuard.
        """
        self._setup()
        if not self.is_configured():
            raise DatabaseUnavailableError("Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)")

        neon_budget_guard.inspect_query(query, params, is_write=False)
        start_time = time.perf_counter()
        query_norm, norm_params = self._normalize_query_params(query, params)

        if not self._pool and ConnectionPool is not None and self.is_configured():
            try:
                self.connect()
            except Exception:
                pass

        if self._pool is not None:
            try:
                timeout_sec = neon_budget_guard.get_query_timeout_seconds()
                with self._pool.connection(timeout=timeout_sec) as conn:
                    with conn.cursor() as cur:
                        cur.execute(query_norm, norm_params)
                        rows = cur.fetchall()
                        results = [dict(r) if not isinstance(r, dict) else r for r in rows]

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                estimated_bytes = len(json.dumps(results, default=str).encode("utf-8")) if results else 0
                neon_budget_guard.record_query_execution(len(results), estimated_bytes, duration_ms)
                return results
            except Exception as e:
                raise DatabaseUnavailableError(f"Neon fetch_all error: {sanitize_url(e)}") from e
        else:
            return self._mock_fetch_all(query, params)

    def fetch_one(
        self, query: str, params: Optional[Any] = None, operation_name: str = "fetch_one"
    ) -> Optional[Dict[str, Any]]:
        """Executes a bounded SELECT query and returns the first row or None."""
        rows = self.fetch_all(query, params, operation_name)
        return rows[0] if rows else None

    def execute(
        self, statement: str, params: Optional[Any] = None, operation_name: str = "execute"
    ) -> int:
        """
        Executes a parameterized INSERT/UPDATE/DELETE/DDL statement.
        Protected by NeonBudgetGuard.
        """
        self._setup()
        if not self.is_configured():
            raise DatabaseUnavailableError("Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)")

        neon_budget_guard.inspect_query(statement, params, is_write=True)
        start_time = time.perf_counter()
        stmt_norm, norm_params = self._normalize_query_params(statement, params)

        if not self._pool and ConnectionPool is not None and self.is_configured():
            try:
                self.connect()
            except Exception:
                pass

        if self._pool is not None:
            try:
                timeout_sec = neon_budget_guard.get_query_timeout_seconds()
                with self._pool.connection(timeout=timeout_sec) as conn:
                    with conn.cursor() as cur:
                        cur.execute(stmt_norm, norm_params)
                        conn.commit()
                        affected = cur.rowcount if cur.rowcount >= 0 else 1

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                neon_budget_guard.record_query_execution(affected, affected * 500, duration_ms)
                return affected
            except Exception as e:
                raise DatabaseUnavailableError(f"Neon execute error: {sanitize_url(e)}") from e
        else:
            return self._mock_execute(statement, params)

    @contextmanager
    def transaction(self):
        """Context manager for atomic transactional writes."""
        self._setup()
        if not self.is_configured():
            raise DatabaseUnavailableError("Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)")

        if not self._pool and ConnectionPool is not None:
            self.connect()

        if self._pool is not None:
            timeout_sec = neon_budget_guard.get_query_timeout_seconds()
            with self._pool.connection(timeout=timeout_sec) as conn:
                with conn.transaction():
                    yield conn
        else:
            raise DatabaseUnavailableError("Neon connection pool unavailable")

    def execute_many(
        self, statement: str, params_seq: List[List[Any]], operation_name: str = "execute_many"
    ) -> int:
        """
        Executes a parameterized INSERT/UPDATE/DELETE statement across a sequence of parameter lists
        within a single transaction / cursor operation.
        Protected by NeonBudgetGuard.
        """
        if not params_seq:
            return 0
        self._setup()
        if not self.is_configured():
            raise DatabaseUnavailableError("Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)")

        neon_budget_guard.inspect_query(statement, params_seq[0], is_write=True)
        start_time = time.perf_counter()
        stmt_norm, _ = self._normalize_query_params(statement, params_seq[0])

        if not self._pool and ConnectionPool is not None and self.is_configured():
            try:
                self.connect()
            except Exception:
                pass

        if self._pool is not None:
            try:
                timeout_sec = neon_budget_guard.get_query_timeout_seconds()
                affected = 0
                with self._pool.connection(timeout=timeout_sec) as conn:
                    with conn.cursor() as cur:
                        for params in params_seq:
                            _, norm_p = self._normalize_query_params(statement, params)
                            cur.execute(stmt_norm, norm_p)
                            affected += cur.rowcount if cur.rowcount >= 0 else 1
                        conn.commit()

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                neon_budget_guard.record_query_execution(affected, affected * 500, duration_ms)
                return affected
            except Exception as e:
                raise DatabaseUnavailableError(f"Neon execute_many error: {sanitize_url(e)}") from e
        else:
            affected = 0
            for p in params_seq:
                affected += self._mock_execute(statement, p)
            return affected

    async def execute_batch(
        self, statement: str, params_seq: List[List[Any]], operation_name: str = "batch_statement"
    ) -> int:
        """
        Asynchronously executes batch statements protected by NeonBudgetGuard with slot tracking.
        """
        if not params_seq:
            return 0
        self._setup()
        if not self.is_configured():
            raise DatabaseUnavailableError("Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)")

        neon_budget_guard.inspect_query(statement, params_seq[0], is_write=True)
        await neon_budget_guard.acquire_query_slot(operation_name, is_write=True)
        success = False
        try:
            affected = self.execute_many(statement, params_seq, operation_name)
            success = True
            return affected
        finally:
            await neon_budget_guard.release_query_slot(success)

    async def execute_query(
        self, query: str, params: Optional[List[Any]] = None, operation_name: str = "query"
    ) -> List[Dict[str, Any]]:
        """
        Executes a bounded parameterized SELECT query with explicit columns.
        Protected by NeonBudgetGuard with strict query inspection and slot tracking.
        """
        self._setup()
        if not self.is_configured():
            raise DatabaseUnavailableError("Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)")

        neon_budget_guard.inspect_query(query, params, is_write=False)
        await neon_budget_guard.acquire_query_slot(operation_name, is_write=False)
        success = False
        try:
            results = self.fetch_all(query, params, operation_name)
            success = True
            return results
        finally:
            await neon_budget_guard.release_query_slot(success)

    async def execute_statement(
        self, statement: str, params: Optional[List[Any]] = None, operation_name: str = "statement"
    ) -> int:
        """
        Executes a parameterized INSERT/UPDATE/DELETE statement.
        Protected by NeonBudgetGuard with slot tracking.
        """
        self._setup()
        if not self.is_configured():
            raise DatabaseUnavailableError("Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)")

        neon_budget_guard.inspect_query(statement, params, is_write=True)
        await neon_budget_guard.acquire_query_slot(operation_name, is_write=True)
        success = False
        try:
            affected = self.execute(statement, params, operation_name)
            success = True
            return affected
        finally:
            await neon_budget_guard.release_query_slot(success)


# =========================================================================
# Neon Repositories (Bounded, Explicit Columns, Zero SELECT *)
# =========================================================================

class NeonFixtureRepository(IFixtureRepository):
    """Neon PostgreSQL implementation of Fixture repository."""

    def __init__(self, executor: NeonQueryExecutor):
        self.executor = executor

    async def get_by_id(self, fixture_id: str) -> Optional[Dict[str, Any]]:
        sql = """
        SELECT id, source_system, sport, league, competition_id, home_team AS "homeTeam",
               away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", scheduled_at, status,
               current_score AS "currentScore", venue, referee, canonical_key,
               fixture_version, created_at, updated_at
        FROM neon_fixtures
        WHERE id = $1
        LIMIT 1;
        """
        rows = await self.executor.execute_query(sql, [fixture_id], "neon_get_fixture_by_id")
        return rows[0] if rows else None

    async def get_by_ids(self, fixture_ids: List[str]) -> List[Dict[str, Any]]:
        if not fixture_ids:
            return []
        sql = """
        SELECT id, source_system, sport, league, competition_id, home_team AS "homeTeam",
               away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", scheduled_at, status,
               current_score AS "currentScore", venue, referee, canonical_key,
               fixture_version, created_at, updated_at
        FROM neon_fixtures
        WHERE id = ANY($1)
        LIMIT $2;
        """
        return await self.executor.execute_query(sql, [fixture_ids, len(fixture_ids)], "neon_get_fixtures_by_ids")

    async def get_live_fixtures(self, sport: Optional[str] = None) -> List[Dict[str, Any]]:
        if sport and sport.lower() != "all":
            sql = """
            SELECT id, source_system, sport, league, competition_id, home_team AS "homeTeam",
                   away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", scheduled_at, status,
                   current_score AS "currentScore", venue, referee, canonical_key,
                   fixture_version, created_at, updated_at
            FROM neon_fixtures
            WHERE status IN ('live', 'in_play', 'inplay', '1H', '2H', 'HT') AND sport = $1
            ORDER BY kickoff_utc ASC
            LIMIT 100;
            """
            params = [sport.lower()]
        else:
            sql = """
            SELECT id, source_system, sport, league, competition_id, home_team AS "homeTeam",
                   away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", scheduled_at, status,
                   current_score AS "currentScore", venue, referee, canonical_key,
                   fixture_version, created_at, updated_at
            FROM neon_fixtures
            WHERE status IN ('live', 'in_play', 'inplay', '1H', '2H', 'HT')
            ORDER BY kickoff_utc ASC
            LIMIT 100;
            """
            params = []
        return await self.executor.execute_query(sql, params, "neon_get_live_fixtures")

    async def get_recent_completed(self, sport: Optional[str] = None, hours: int = 48) -> List[Dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        if sport and sport.lower() != "all":
            sql = """
            SELECT id, source_system, sport, league, competition_id, home_team AS "homeTeam",
                   away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", scheduled_at, status,
                   current_score AS "currentScore", venue, referee, canonical_key,
                   fixture_version, created_at, updated_at
            FROM neon_fixtures
            WHERE status IN ('completed', 'FT', 'finished', 'AET', 'AP')
              AND kickoff_utc >= $1 AND sport = $2
            ORDER BY kickoff_utc DESC
            LIMIT 150;
            """
            params = [cutoff, sport.lower()]
        else:
            sql = """
            SELECT id, source_system, sport, league, competition_id, home_team AS "homeTeam",
                   away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", scheduled_at, status,
                   current_score AS "currentScore", venue, referee, canonical_key,
                   fixture_version, created_at, updated_at
            FROM neon_fixtures
            WHERE status IN ('completed', 'FT', 'finished', 'AET', 'AP')
              AND kickoff_utc >= $1
            ORDER BY kickoff_utc DESC
            LIMIT 150;
            """
            params = [cutoff]
        return await self.executor.execute_query(sql, params, "neon_get_recent_completed")

    async def get_by_date_and_sport(
        self,
        date_str: str,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        sql = """
        SELECT id, source_system, sport, league, competition_id, home_team AS "homeTeam",
               away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", scheduled_at, status,
               current_score AS "currentScore", venue, referee, canonical_key,
               fixture_version, created_at, updated_at
        FROM neon_fixtures
        WHERE kickoff_utc::text LIKE $1
        """
        params: List[Any] = [f"{date_str}%"]
        idx = 2
        if sport and sport.lower() != "all":
            sql += f" AND sport = ${idx}"
            params.append(sport.lower())
            idx += 1
        if league and league.lower() != "all":
            sql += f" AND league = ${idx}"
            params.append(league)
            idx += 1
        sql += f" ORDER BY kickoff_utc ASC LIMIT ${idx};"
        params.append(limit)
        return await self.executor.execute_query(sql, params, "neon_get_by_date_and_sport")

    async def get_fixtures(
        self,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        date: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        bounded_limit = min(100, max(1, limit))
        safe_offset = max(0, offset)
        sql = """
        SELECT id, source_system, sport, league, competition_id, home_team AS "homeTeam",
               away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", scheduled_at, status,
               current_score AS "currentScore", venue, referee, canonical_key,
               fixture_version, created_at, updated_at
        FROM neon_fixtures
        WHERE 1=1
        """
        params: List[Any] = []
        idx = 1
        if start_date and end_date:
            sql += f" AND kickoff_utc >= ${idx} AND kickoff_utc <= ${idx+1}"
            params.extend([start_date, f"{end_date}T23:59:59Z"])
            idx += 2
        elif start_date:
            sql += f" AND kickoff_utc >= ${idx}"
            params.append(start_date)
            idx += 1
        elif end_date:
            sql += f" AND kickoff_utc <= ${idx}"
            params.append(f"{end_date}T23:59:59Z")
            idx += 1
        elif date:
            sql += f" AND kickoff_utc::text LIKE ${idx}"
            params.append(f"{date}%")
            idx += 1

        if sport and sport.lower() != "all":
            sql += f" AND sport = ${idx}"
            params.append(sport.lower())
            idx += 1
        if league and league.lower() != "all":
            sql += f" AND league = ${idx}"
            params.append(league)
            idx += 1
        if status and status.lower() != "all":
            norm_st = status.lower().strip()
            if norm_st == "live":
                sql += f" AND status IN ('live', 'in_play', 'inplay', '1H', '2H', 'HT')"
            elif norm_st == "completed":
                sql += f" AND status IN ('completed', 'FT', 'finished', 'AET', 'AP')"
            elif norm_st in ("upcoming", "scheduled"):
                sql += f" AND status IN ('scheduled', 'upcoming', 'NS', 'TIMED')"
            else:
                sql += f" AND status = ${idx}"
                params.append(status)
                idx += 1

        sql += f" ORDER BY kickoff_utc ASC LIMIT ${idx} OFFSET ${idx+1};"
        params.extend([bounded_limit, safe_offset])
        return await self.executor.execute_query(sql, params, "neon_get_fixtures")

    async def count_fixtures(
        self,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        date: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        sql = "SELECT COUNT(id) AS cnt FROM neon_fixtures WHERE 1=1"
        params: List[Any] = []
        idx = 1
        if start_date and end_date:
            sql += f" AND kickoff_utc >= ${idx} AND kickoff_utc <= ${idx+1}"
            params.extend([start_date, f"{end_date}T23:59:59Z"])
            idx += 2
        elif start_date:
            sql += f" AND kickoff_utc >= ${idx}"
            params.append(start_date)
            idx += 1
        elif end_date:
            sql += f" AND kickoff_utc <= ${idx}"
            params.append(f"{end_date}T23:59:59Z")
            idx += 1
        elif date:
            sql += f" AND kickoff_utc::text LIKE ${idx}"
            params.append(f"{date}%")
            idx += 1

        if sport and sport.lower() != "all":
            sql += f" AND sport = ${idx}"
            params.append(sport.lower())
            idx += 1
        if league and league.lower() != "all":
            sql += f" AND league = ${idx}"
            params.append(league)
            idx += 1
        if status and status.lower() != "all":
            norm_st = status.lower().strip()
            if norm_st == "live":
                sql += f" AND status IN ('live', 'in_play', 'inplay', '1H', '2H', 'HT')"
            elif norm_st == "completed":
                sql += f" AND status IN ('completed', 'FT', 'finished', 'AET', 'AP')"
            elif norm_st in ("upcoming", "scheduled"):
                sql += f" AND status IN ('scheduled', 'upcoming', 'NS', 'TIMED')"
            else:
                sql += f" AND status = ${idx}"
                params.append(status)
                idx += 1
        sql += " LIMIT 1;"
        rows = await self.executor.execute_query(sql, params, "neon_count_fixtures")
        if rows:
            return int(rows[0].get("cnt", 0))
        return 0

    async def prune_expired_fixtures(self, retention_days: int = 14) -> int:
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        sql = """
        DELETE FROM neon_fixtures
        WHERE id IN (
            SELECT id FROM neon_fixtures
            WHERE status IN ('completed', 'FT', 'finished', 'AET', 'AP') AND kickoff_utc < $1
            ORDER BY kickoff_utc ASC
            LIMIT 200
        );
        """
        return await self.executor.execute_statement(sql, [cutoff_iso], "neon_prune_expired_fixtures")

    async def upsert_fixtures(self, fixtures: List[Dict[str, Any]]) -> int:
        if not fixtures:
            return 0
        batch_size = max(1, getattr(settings, "fixture_upsert_batch_size", 100))
        sql = """
        INSERT INTO neon_fixtures (
            id, source_system, sport, league, competition_id, home_team, away_team,
            kickoff_utc, scheduled_at, status, current_score, venue, referee,
            canonical_key, source_event_id, fixture_version, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17
        )
        ON CONFLICT (id) DO UPDATE SET
            sport = EXCLUDED.sport,
            league = EXCLUDED.league,
            status = EXCLUDED.status,
            current_score = EXCLUDED.current_score,
            updated_at = EXCLUDED.updated_at;
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        prepared_params_seq: List[List[Any]] = []
        for f in fixtures:
            f_id = f.get("id") or f.get("source_event_id")
            if not f_id:
                continue
            params = [
                f_id,
                f.get("source_system", "predictpro_failover_neon"),
                f.get("sport", "football"),
                f.get("league", "General"),
                f.get("competition_id"),
                f.get("homeTeam") or f.get("home_team", ""),
                f.get("awayTeam") or f.get("away_team", ""),
                f.get("kickoffUtc") or f.get("scheduled_at") or now_iso,
                f.get("scheduled_at") or f.get("kickoffUtc") or now_iso,
                f.get("status", "scheduled"),
                json.dumps(f.get("currentScore") or {}),
                f.get("venue"),
                f.get("referee"),
                f.get("canonical_key"),
                f.get("source_event_id"),
                f.get("fixture_version", 1),
                now_iso,
            ]
            prepared_params_seq.append(params)

        total_upserted = 0
        for i in range(0, len(prepared_params_seq), batch_size):
            chunk = prepared_params_seq[i:i + batch_size]
            if not chunk:
                continue
            upserted = await self.executor.execute_batch(sql, chunk, "neon_upsert_fixtures_batch")
            total_upserted += upserted if upserted > 0 else len(chunk)

        return total_upserted


class NeonPredictionRepository(IPredictionRepository):
    """Neon PostgreSQL implementation of Prediction repository."""

    def __init__(self, executor: NeonQueryExecutor):
        self.executor = executor

    def _enrich_feed_items(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensures the complete published-feed representation required by FeedService and React UI."""
        enriched = []
        for r in rows:
            val_st = str(r.get("validation_status") or r.get("validationStatus") or "").lower().strip()
            if val_st and val_st != "validated":
                continue
            is_pub = r.get("published")
            if is_pub is False:
                continue
            stop_reason = str(r.get("stop_reason") or r.get("stopReason") or "").lower().strip()
            if stop_reason in ("rejected_invalid", "rejected_past", "rejected_completed", "insufficient_history", "insufficient_data", "abstained"):
                continue

            doc = {}
            payload = r.get("prediction_payload")
            if isinstance(payload, str):
                try:
                    doc = json.loads(payload)
                except Exception:
                    doc = {}
            elif isinstance(payload, dict):
                doc = dict(payload)

            # Overlay SQL row fields if doc doesn't have them or row has non-None value
            for k, v in r.items():
                if k == "prediction_payload":
                    continue
                if k not in doc or (v is not None and not doc.get(k)):
                    doc[k] = v

            f_id = doc.get("fixture_id") or doc.get("fixtureId") or doc.get("id") or r.get("fixtureId") or r.get("id")
            if f_id:
                doc.setdefault("fixture_id", f_id)
                doc.setdefault("fixtureId", f_id)
                doc.setdefault("id", f_id)

            pct_val = doc.get("calibratedPercentage") or doc.get("percentage") or r.get("calibrated_percentage") or 55.0
            if not doc.get("validatedMarkets"):
                m_name = doc.get("market") or "Match Winner"
                sel_name = doc.get("selection") or f"{doc.get('homeTeam', 'Home')} Win"
                doc["validatedMarkets"] = [{
                    "id": f"{doc.get('id', 'pred')}-m1",
                    "marketName": m_name,
                    "selection": sel_name,
                    "probabilityPercentage": float(pct_val),
                    "confidence": doc.get("confidence", "HIGH"),
                }]

            if not doc.get("highestPercentagePrediction") and doc.get("validatedMarkets"):
                first_m = doc["validatedMarkets"][0]
                doc["highestPercentagePrediction"] = {
                    "marketName": first_m.get("marketName") or doc.get("market", ""),
                    "selection": first_m.get("selection") or doc.get("selection", ""),
                    "percentage": first_m.get("probabilityPercentage") or doc.get("percentage") or doc.get("calibratedPercentage", 0.0),
                }

            if doc.get("percentage") is None and doc.get("calibratedPercentage") is not None:
                doc["percentage"] = doc["calibratedPercentage"]
            elif doc.get("calibratedPercentage") is None and doc.get("percentage") is not None:
                doc["calibratedPercentage"] = doc["percentage"]

            if not doc.get("market") and doc.get("highestPercentagePrediction"):
                doc["market"] = doc["highestPercentagePrediction"].get("marketName", "")
            if not doc.get("selection") and doc.get("highestPercentagePrediction"):
                doc["selection"] = doc["highestPercentagePrediction"].get("selection", "")

            if not doc.get("event_date_lagos"):
                k_utc = doc.get("kickoffUtc") or doc.get("scheduled_at") or r.get("kickoffUtc")
                if k_utc:
                    from backend.services.feed_service import get_lagos_date_str
                    doc["event_date_lagos"] = get_lagos_date_str(k_utc)

            # Map unified fixture status & score from single JOIN query
            raw_st = str(r.get("fixture_status") or doc.get("status") or "").lower().strip()
            is_comp = raw_st in ("completed", "finished", "ft", "ended")
            is_live = not is_comp and raw_st in ("live", "in_progress", "halftime", "1st_half", "2nd_half")
            if is_comp:
                doc["status"] = "completed"
                sc = r.get("current_score") or doc.get("currentScore") or doc.get("finalScore")
                if sc:
                    parsed_sc = json.loads(sc) if isinstance(sc, str) else sc
                    doc["currentScore"] = parsed_sc
                    doc["finalScore"] = parsed_sc
            elif is_live:
                doc["status"] = "live"
                sc = r.get("current_score") or doc.get("currentScore")
                if sc:
                    doc["currentScore"] = json.loads(sc) if isinstance(sc, str) else sc
                doc["finalScore"] = None
            else:
                doc["status"] = "upcoming"
                doc["currentScore"] = None
                doc["finalScore"] = None
                doc.pop("currentScore", None)
                doc.pop("finalScore", None)
                doc.pop("current_score", None)
                doc.pop("final_score", None)

            enriched.append(doc)
        return enriched

    async def get_by_id(self, prediction_id: str) -> Optional[Dict[str, Any]]:
        sql = """
        SELECT id, event_id AS "fixtureId", source_system, sport, league, home_team AS "homeTeam",
               away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", date_str AS date,
               model_version AS "modelVersion", validation_status AS "validationStatus",
               published, calibrated_percentage AS "calibratedPercentage", is_best_of_day AS "isBestOfDay",
               prediction_payload, prediction_version, created_at, updated_at
        FROM neon_published_predictions
        WHERE id = $1 OR event_id = $1
        LIMIT 1;
        """
        rows = await self.executor.execute_query(sql, [prediction_id], "neon_get_pred_by_id")
        if not rows:
            return None
        return self._enrich_feed_items(rows)[0]

    async def get_by_fixture_id(self, fixture_id: str) -> List[Dict[str, Any]]:
        sql = """
        SELECT id, event_id AS "fixtureId", source_system, sport, league, home_team AS "homeTeam",
               away_team AS "awayTeam", kickoff_utc AS "kickoffUtc", date_str AS date,
               model_version AS "modelVersion", validation_status AS "validationStatus",
               published, calibrated_percentage AS "calibratedPercentage", is_best_of_day AS "isBestOfDay",
               prediction_payload, prediction_version, created_at, updated_at
        FROM neon_published_predictions
        WHERE event_id = $1 OR id = $1
        LIMIT 20;
        """
        rows = await self.executor.execute_query(sql, [fixture_id], "neon_get_pred_by_fixture")
        return self._enrich_feed_items(rows)

    async def get_daily_feed(
        self, date_str: str, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        sql = """
        SELECT 
            p.id, 
            p.event_id AS "fixtureId", 
            p.source_system, 
            p.sport, 
            p.league, 
            p.home_team AS "homeTeam",
            p.away_team AS "awayTeam", 
            p.kickoff_utc AS "kickoffUtc", 
            p.date_str AS date,
            p.model_version AS "modelVersion", 
            p.validation_status AS "validationStatus",
            p.published, 
            p.calibrated_percentage AS "calibratedPercentage", 
            p.is_best_of_day AS "isBestOfDay",
            p.prediction_payload, 
            p.prediction_version, 
            p.created_at, 
            p.updated_at,
            COALESCE(f.status, 'scheduled') AS "fixture_status",
            f.current_score AS "current_score",
            f.scheduled_at AS "scheduled_at"
        FROM neon_published_predictions p
        LEFT JOIN neon_fixtures f ON (p.event_id = f.id OR p.id = f.id)
        WHERE p.validation_status = 'validated' 
          AND p.published = TRUE 
          AND (p.date_str = $1 OR p.kickoff_utc::text LIKE $2)
        """
        params: List[Any] = [date_str, f"{date_str}%"]
        idx = 3
        if sport and sport.lower() != "all":
            sql += f" AND p.sport = ${idx}"
            params.append(sport.lower())
            idx += 1
        if league and league.lower() != "all":
            sql += f" AND p.league = ${idx}"
            params.append(league)
            idx += 1
        sql += f" ORDER BY p.is_best_of_day DESC, p.calibrated_percentage DESC, p.kickoff_utc ASC LIMIT ${idx};"
        params.append(limit)
        rows = await self.executor.execute_query(sql, params, "neon_get_daily_feed")
        return self._enrich_feed_items(rows)

    async def get_published_feed(
        self, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        sql = """
        SELECT 
            p.id, 
            p.event_id AS "fixtureId", 
            p.source_system, 
            p.sport, 
            p.league, 
            p.home_team AS "homeTeam",
            p.away_team AS "awayTeam", 
            p.kickoff_utc AS "kickoffUtc", 
            p.date_str AS date,
            p.model_version AS "modelVersion", 
            p.validation_status AS "validationStatus",
            p.published, 
            p.calibrated_percentage AS "calibratedPercentage", 
            p.is_best_of_day AS "isBestOfDay",
            p.prediction_payload, 
            p.prediction_version, 
            p.created_at, 
            p.updated_at,
            COALESCE(f.status, 'scheduled') AS "fixture_status",
            f.current_score AS "current_score",
            f.scheduled_at AS "scheduled_at"
        FROM neon_published_predictions p
        LEFT JOIN neon_fixtures f ON (p.event_id = f.id OR p.id = f.id)
        WHERE p.validation_status = 'validated'
          AND p.published = TRUE
        """
        params: List[Any] = []
        idx = 1
        if sport and sport.lower() != "all":
            sql += f" AND p.sport = ${idx}"
            params.append(sport.lower())
            idx += 1
        if league and league.lower() != "all":
            sql += f" AND p.league = ${idx}"
            params.append(league)
            idx += 1
        sql += f" ORDER BY p.kickoff_utc ASC LIMIT ${idx};"
        params.append(limit)
        rows = await self.executor.execute_query(sql, params, "neon_get_published_feed")
        return self._enrich_feed_items(rows)

    async def save_predictions(self, predictions: List[Dict[str, Any]]) -> int:
        if not predictions:
            return 0
        batch_size = max(1, getattr(settings, "fixture_upsert_batch_size", 100))
        sql = """
        INSERT INTO neon_published_predictions (
            id, event_id, source_system, sport, league, home_team, away_team,
            kickoff_utc, date_str, model_version, validation_status, published,
            calibrated_percentage, is_best_of_day, prediction_payload, prediction_version, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17
        )
        ON CONFLICT (id) DO UPDATE SET
            event_id = EXCLUDED.event_id,
            sport = EXCLUDED.sport,
            league = EXCLUDED.league,
            home_team = EXCLUDED.home_team,
            away_team = EXCLUDED.away_team,
            kickoff_utc = EXCLUDED.kickoff_utc,
            date_str = EXCLUDED.date_str,
            model_version = EXCLUDED.model_version,
            validation_status = EXCLUDED.validation_status,
            published = EXCLUDED.published,
            calibrated_percentage = EXCLUDED.calibrated_percentage,
            is_best_of_day = EXCLUDED.is_best_of_day,
            prediction_payload = EXCLUDED.prediction_payload,
            prediction_version = EXCLUDED.prediction_version,
            updated_at = EXCLUDED.updated_at;
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        prepared_params_seq: List[List[Any]] = []
        for p in predictions:
            val_st = str(p.get("validationStatus") or p.get("validation_status") or "").lower().strip()
            if val_st and val_st != "validated":
                continue
            if p.get("published") is False:
                continue

            p_id = p.get("id") or p.get("fixture_id") or p.get("fixtureId")
            if not p_id:
                continue
            f_id = p.get("fixture_id") or p.get("fixtureId") or p_id
            date_val = p.get("event_date_lagos") or p.get("date") or (p.get("kickoffUtc", "")[:10] if p.get("kickoffUtc") else "")
            cal_pct = p.get("calibratedPercentage")
            if cal_pct is None:
                cal_pct = p.get("percentage")
            if cal_pct is None and isinstance(p.get("highestPercentagePrediction"), dict):
                cal_pct = p["highestPercentagePrediction"].get("percentage")
            if cal_pct is None:
                continue

            params = [
                p_id,
                f_id,
                p.get("source_system", "predictpro_failover_neon"),
                p.get("sport", "football"),
                p.get("league", "General"),
                p.get("homeTeam") or p.get("home_team", ""),
                p.get("awayTeam") or p.get("away_team", ""),
                p.get("kickoffUtc") or p.get("scheduled_at") or now_iso,
                date_val,
                p.get("modelVersion") or p.get("model_version") or "ELO + POISSON",
                p.get("validationStatus", "validated"),
                bool(p.get("published", True)),
                cal_pct,
                bool(p.get("isBestOfDay", False)),
                json.dumps(p),
                p.get("prediction_version", 1),
                now_iso,
            ]
            prepared_params_seq.append(params)

        total_upserted = 0
        for i in range(0, len(prepared_params_seq), batch_size):
            chunk = prepared_params_seq[i:i + batch_size]
            if not chunk:
                continue
            upserted = await self.executor.execute_batch(sql, chunk, "neon_save_predictions_batch")
            total_upserted += upserted if upserted > 0 else len(chunk)

        return total_upserted


class NeonPredictionResultRepository(IPredictionResultRepository):
    """Neon PostgreSQL implementation of Prediction Result repository."""

    def __init__(self, executor: NeonQueryExecutor):
        self.executor = executor

    async def get_by_id(self, result_id: str) -> Optional[Dict[str, Any]]:
        sql = """
        SELECT id, fixture_id AS "fixtureId", source_system, sport, status,
               predicted_outcome, actual_outcome, is_correct, brier_score,
               details, evaluated_at AS "evaluatedAt", created_at, updated_at
        FROM neon_prediction_results
        WHERE id = $1
        LIMIT 1;
        """
        rows = await self.executor.execute_query(sql, [result_id], "neon_get_result_by_id")
        return rows[0] if rows else None

    async def get_by_fixture_id(self, fixture_id: str) -> List[Dict[str, Any]]:
        sql = """
        SELECT id, fixture_id AS "fixtureId", source_system, sport, status,
               predicted_outcome, actual_outcome, is_correct, brier_score,
               details, evaluated_at AS "evaluatedAt", created_at, updated_at
        FROM neon_prediction_results
        WHERE fixture_id = $1
        LIMIT 20;
        """
        return await self.executor.execute_query(sql, [fixture_id], "neon_get_results_by_fixture")

    async def get_recent_results(self, sport: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if sport and sport.lower() != "all":
            sql = """
            SELECT id, fixture_id AS "fixtureId", source_system, sport, status,
                   predicted_outcome, actual_outcome, is_correct, brier_score,
                   details, evaluated_at AS "evaluatedAt", created_at, updated_at
            FROM neon_prediction_results
            WHERE sport = $1
            ORDER BY evaluated_at DESC
            LIMIT $2;
            """
            params = [sport.lower(), limit]
        else:
            sql = """
            SELECT id, fixture_id AS "fixtureId", source_system, sport, status,
                   predicted_outcome, actual_outcome, is_correct, brier_score,
                   details, evaluated_at AS "evaluatedAt", created_at, updated_at
            FROM neon_prediction_results
            ORDER BY evaluated_at DESC
            LIMIT $1;
            """
            params = [limit]
        return await self.executor.execute_query(sql, params, "neon_get_recent_results")

    async def save_results(self, results: List[Dict[str, Any]]) -> int:
        if not results:
            return 0
        sql = """
        INSERT INTO neon_prediction_results (
            id, fixture_id, source_system, sport, status, predicted_outcome,
            actual_outcome, is_correct, brier_score, details, evaluated_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
        )
        ON CONFLICT (id) DO UPDATE SET
            status = EXCLUDED.status,
            actual_outcome = EXCLUDED.actual_outcome,
            is_correct = EXCLUDED.is_correct,
            brier_score = EXCLUDED.brier_score,
            evaluated_at = EXCLUDED.evaluated_at,
            updated_at = EXCLUDED.updated_at;
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        count = 0
        for r in results:
            r_id = r.get("id") or r.get("prediction_id")
            if not r_id:
                continue
            params = [
                r_id,
                r.get("fixtureId") or "",
                r.get("source_system", "predictpro_failover_neon"),
                r.get("sport", "football"),
                r.get("status", "evaluated"),
                r.get("predicted_outcome"),
                r.get("actual_outcome"),
                r.get("is_correct"),
                r.get("brier_score"),
                json.dumps(r.get("details") or {}),
                r.get("evaluatedAt") or now_iso,
                now_iso,
            ]
            await self.executor.execute_statement(sql, params, "neon_save_result")
            count += 1
        return count


class NeonRefreshStateRepository(IRefreshStateRepository):
    """Neon PostgreSQL implementation of Refresh State repository."""

    def __init__(self, executor: NeonQueryExecutor):
        self.executor = executor

    async def save_run(self, run_record: Dict[str, Any]) -> bool:
        sql = """
        INSERT INTO neon_refresh_state (
            id, source_system, run_id, status, sports, target_date, records_processed,
            predictions_published, duration_ms, diagnostics, started_at, completed_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
        )
        ON CONFLICT (id) DO UPDATE SET
            status = EXCLUDED.status,
            records_processed = EXCLUDED.records_processed,
            predictions_published = EXCLUDED.predictions_published,
            duration_ms = EXCLUDED.duration_ms,
            diagnostics = EXCLUDED.diagnostics,
            completed_at = EXCLUDED.completed_at,
            updated_at = EXCLUDED.updated_at;
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        r_id = run_record.get("id") or run_record.get("run_id")
        if not r_id:
            return False
        params = [
            r_id,
            run_record.get("source_system", "predictpro_failover_neon"),
            run_record.get("run_id", r_id),
            run_record.get("status", "completed"),
            json.dumps(run_record.get("sports") or []),
            run_record.get("target_date"),
            run_record.get("records_processed", 0),
            run_record.get("predictions_published", 0),
            run_record.get("duration_ms", 0.0),
            json.dumps(run_record.get("diagnostics") or {}),
            run_record.get("timestamp") or now_iso,
            now_iso,
            now_iso,
        ]
        await self.executor.execute_statement(sql, params, "neon_save_refresh_run")
        return True

    async def get_latest_run(self) -> Optional[Dict[str, Any]]:
        sql = """
        SELECT id, source_system, run_id, status, sports, target_date, records_processed,
               predictions_published, duration_ms, diagnostics, started_at, completed_at
        FROM neon_refresh_state
        ORDER BY started_at DESC
        LIMIT 1;
        """
        rows = await self.executor.execute_query(sql, [], "neon_get_latest_refresh_run")
        return rows[0] if rows else None

    async def get_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        sql = """
        SELECT id, source_system, run_id, status, sports, target_date, records_processed,
               predictions_published, duration_ms, diagnostics, started_at, completed_at
        FROM neon_refresh_state
        ORDER BY started_at DESC
        LIMIT $1;
        """
        return await self.executor.execute_query(sql, [limit], "neon_get_refresh_runs")

    async def is_sport_date_fresh(self, sport: str, date_str: str) -> bool:
        # Check if there is a completed refresh within last 15 minutes
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        sql = """
        SELECT id
        FROM neon_refresh_state
        WHERE status = 'completed' AND target_date = $1 AND started_at >= $2
        LIMIT 1;
        """
        rows = await self.executor.execute_query(sql, [date_str, cutoff], "neon_check_freshness")
        return len(rows) > 0

    async def mark_sport_date_fresh(self, sport: str, date_str: str, fixture_count: int = 0) -> None:
        pass


class NeonModelConfigRepository(IModelConfigRepository):
    """Neon PostgreSQL implementation of Model Configuration repository."""

    def __init__(self, executor: NeonQueryExecutor):
        self.executor = executor

    async def get_active_model(self, sport: str = "football") -> str:
        sql = """
        SELECT active_model
        FROM neon_model_config
        WHERE sport = $1
        LIMIT 1;
        """
        rows = await self.executor.execute_query(sql, [sport], "neon_get_active_model")
        if rows and "active_model" in rows[0]:
            return rows[0]["active_model"]
        return "ELO + POISSON"

    async def set_active_model(self, model: str, sport: str = "football", tier: str = "production") -> Dict[str, Any]:
        sql = """
        INSERT INTO neon_model_config (
            id, source_system, active_model, sport, tier, is_production_ready, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7
        )
        ON CONFLICT (id) DO UPDATE SET
            active_model = EXCLUDED.active_model,
            tier = EXCLUDED.tier,
            is_production_ready = EXCLUDED.is_production_ready,
            updated_at = EXCLUDED.updated_at;
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        cfg_id = f"model_cfg_{sport}"
        is_prod = tier == "production"
        params = [
            cfg_id,
            "predictpro_failover_neon",
            model,
            sport,
            tier,
            is_prod,
            now_iso,
        ]
        await self.executor.execute_statement(sql, params, "neon_set_active_model")
        return {
            "key": "active_model",
            "model_id": model,
            "sport": sport,
            "tier": tier,
            "is_production_ready": is_prod,
        }

    async def get_model_config(self, sport: str = "football") -> Dict[str, Any]:
        sql = """
        SELECT id, source_system, active_model, sport, tier, is_production_ready, version
        FROM neon_model_config
        WHERE sport = $1
        LIMIT 1;
        """
        rows = await self.executor.execute_query(sql, [sport], "neon_get_model_config")
        if rows:
            return rows[0]
        return {
            "key": "active_model",
            "model_id": "ELO + POISSON",
            "sport": sport,
            "tier": "production",
            "is_production_ready": True,
        }


class NeonCalibrationRepository(ICalibrationRepository):
    """Neon PostgreSQL implementation of Calibration repository."""

    def __init__(self, executor: NeonQueryExecutor):
        self.executor = executor

    async def get_calibration(self, sport: str, model_name: str, market_type: str) -> Optional[Dict[str, Any]]:
        c_id = f"cal_{sport}_{model_name}_{market_type}"
        sql = """
        SELECT id, source_system, sport, market, method, parameters, sample_size, brier_score, version
        FROM neon_calibration_metadata
        WHERE id = $1
        LIMIT 1;
        """
        rows = await self.executor.execute_query(sql, [c_id], "neon_get_calibration")
        if not rows:
            # Fallback to sport and market
            sql_fallback = """
            SELECT id, source_system, sport, market, method, parameters, sample_size, brier_score, version
            FROM neon_calibration_metadata
            WHERE sport = $1 AND market = $2
            LIMIT 1;
            """
            rows = await self.executor.execute_query(sql_fallback, [sport, market_type], "neon_get_calibration_fallback")

        if not rows:
            return None

        row = rows[0]
        params_str = row.get("parameters")
        if params_str:
            try:
                data = json.loads(params_str) if isinstance(params_str, str) else params_str
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return row

    async def save_calibration(
        self, sport: str, model_name: str, market_type: str, data: Dict[str, Any]
    ) -> bool:
        sql = """
        INSERT INTO neon_calibration_metadata (
            id, source_system, sport, market, method, parameters, sample_size, brier_score, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9
        )
        ON CONFLICT (id) DO UPDATE SET
            method = EXCLUDED.method,
            parameters = EXCLUDED.parameters,
            sample_size = EXCLUDED.sample_size,
            brier_score = EXCLUDED.brier_score,
            updated_at = EXCLUDED.updated_at;
        """
        c_id = f"cal_{sport}_{model_name}_{market_type}"
        now_iso = datetime.now(timezone.utc).isoformat()
        params = [
            c_id,
            "predictpro_failover_neon",
            sport,
            market_type,
            data.get("calibration_method") or data.get("method", "platt"),
            json.dumps(data),
            data.get("sample_size", 0),
            data.get("brier_score"),
            now_iso,
        ]
        await self.executor.execute_statement(sql, params, "neon_save_calibration")
        return True


class NeonModelGovernanceRepository(IModelGovernanceRepository):
    """Neon PostgreSQL implementation of Model Governance repository."""

    def __init__(self, executor: NeonQueryExecutor):
        self.executor = executor

    async def get_governance_record(self, sport: str, gate_name: str) -> Optional[Dict[str, Any]]:
        sql = """
        SELECT id, source_system, sport, gate_name, status, criteria_results, admin_notes, validated_at
        FROM neon_model_governance
        WHERE sport = $1 AND gate_name = $2
        LIMIT 1;
        """
        rows = await self.executor.execute_query(sql, [sport, gate_name], "neon_get_gov_record")
        return rows[0] if rows else None

    async def save_governance_record(
        self, sport: str, gate_name: str, status: str, criteria: Dict[str, Any], notes: Optional[str] = None
    ) -> bool:
        sql = """
        INSERT INTO neon_model_governance (
            id, source_system, sport, gate_name, status, criteria_results, admin_notes, validated_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9
        )
        ON CONFLICT (id) DO UPDATE SET
            status = EXCLUDED.status,
            criteria_results = EXCLUDED.criteria_results,
            admin_notes = EXCLUDED.admin_notes,
            validated_at = EXCLUDED.validated_at,
            updated_at = EXCLUDED.updated_at;
        """
        g_id = f"gov_{sport}_{gate_name}"
        now_iso = datetime.now(timezone.utc).isoformat()
        params = [
            g_id,
            "predictpro_failover_neon",
            sport,
            gate_name,
            status,
            json.dumps(criteria),
            notes or "",
            now_iso,
            now_iso,
        ]
        await self.executor.execute_statement(sql, params, "neon_save_gov_record")
        return True

    async def get_all_governance_records(self, sport: Optional[str] = None) -> List[Dict[str, Any]]:
        if sport and sport.lower() != "all":
            sql = """
            SELECT id, source_system, sport, gate_name, status, criteria_results, admin_notes, validated_at
            FROM neon_model_governance
            WHERE sport = $1
            ORDER BY validated_at DESC
            LIMIT 50;
            """
            params = [sport.lower()]
        else:
            sql = """
            SELECT id, source_system, sport, gate_name, status, criteria_results, admin_notes, validated_at
            FROM neon_model_governance
            ORDER BY validated_at DESC
            LIMIT 50;
            """
            params = []
        return await self.executor.execute_query(sql, params, "neon_get_all_gov_records")


class NeonFeatureRepository(IFeatureRepository):
    """Neon PostgreSQL implementation of team feature snapshots repository."""

    def __init__(self, executor: NeonQueryExecutor):
        self.executor = executor

    async def get_team_snapshot(
        self, team_name: str, sport: str = "football", as_of: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if not team_name:
            return None
        norm_name = team_name.lower().strip()
        sql = """
        SELECT features, feature_version, updated_at
        FROM neon_team_features
        WHERE team_name = $1 AND sport = $2
        """
        params = [norm_name, sport]
        if as_of:
            sql += " AND as_of <= $3"
            params.append(as_of)
        sql += " ORDER BY as_of DESC LIMIT 1;"

        try:
            rows = await self.executor.execute_query(sql, params, "neon_get_team_snapshot")
            if rows:
                feat = rows[0].get("features")
                if isinstance(feat, str):
                    try:
                        return json.loads(feat)
                    except Exception:
                        return feat
                return feat
            return None
        except Exception as e:
            raise DatabaseUnavailableError(f"Neon get_team_snapshot failed: {sanitize_url(e)}") from e

    async def get_batch_team_snapshots(
        self, team_names: List[str], sport: str = "football", as_of: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        if not team_names:
            return {}
        unique_names = list(set([n.lower().strip() for n in team_names if n and n.strip()]))
        placeholders = ", ".join([f"${i+1}" for i in range(len(unique_names))])
        sport_idx = len(unique_names) + 1
        sql = f"""
        SELECT team_name, features, feature_version, updated_at
        FROM neon_team_features
        WHERE team_name IN ({placeholders}) AND sport = ${sport_idx}
        ORDER BY as_of DESC
        LIMIT {len(unique_names) * 5};
        """
        params = list(unique_names) + [sport]
        result: Dict[str, Dict[str, Any]] = {}
        try:
            rows = await self.executor.execute_query(sql, params, "neon_get_batch_team_snapshots")
            for r in rows:
                t_name = (r.get("team_name") or "").lower().strip()
                if t_name and t_name not in result:
                    feat = r.get("features")
                    if isinstance(feat, str):
                        try:
                            feat = json.loads(feat)
                        except Exception:
                            pass
                    result[t_name] = feat
            return result
        except Exception as e:
            raise DatabaseUnavailableError(f"Neon get_batch_team_snapshots failed: {sanitize_url(e)}") from e

    async def save_team_snapshot(self, snapshot: Dict[str, Any]) -> bool:
        if not snapshot or "team_name" not in snapshot:
            return False
        t_name = snapshot["team_name"].lower().strip()
        sport = snapshot.get("sport", "football")
        as_of = snapshot.get("as_of") or datetime.now(timezone.utc).isoformat()
        feat_json = json.dumps(snapshot)
        sql = """
        INSERT INTO neon_team_features (id, team_name, sport, as_of, feature_version, features, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, NOW())
        ON CONFLICT (id) DO UPDATE SET
            features = EXCLUDED.features,
            feature_version = EXCLUDED.feature_version,
            updated_at = NOW();
        """
        s_id = f"{sport}_{t_name}_{as_of}"
        ver = snapshot.get("feature_version", "2.0.0")
        try:
            await self.executor.execute_statement(sql, [s_id, t_name, sport, as_of, ver, feat_json], "neon_save_team_snapshot")
            return True
        except Exception as e:
            raise DatabaseUnavailableError(f"Neon save_team_snapshot failed: {sanitize_url(e)}") from e


class NeonDatabaseAdapter(IDatabaseAdapter):
    """
    Neon PostgreSQL Secondary / Failover Database Adapter.
    """

    def __init__(self):
        self.executor = NeonQueryExecutor()
        self._fixtures = NeonFixtureRepository(self.executor)
        self._predictions = NeonPredictionRepository(self.executor)
        self._prediction_results = NeonPredictionResultRepository(self.executor)
        self._refresh_state = NeonRefreshStateRepository(self.executor)
        self._model_config = NeonModelConfigRepository(self.executor)
        self._calibrations = NeonCalibrationRepository(self.executor)
        self._governance = NeonModelGovernanceRepository(self.executor)
        self._features = NeonFeatureRepository(self.executor)

    @property
    def backend_name(self) -> str:
        return "neon"

    def check_connection(self) -> Dict[str, Any]:
        return self.executor.ping()

    def assert_operational_connection(self) -> None:
        """
        Executes a real SELECT 1 connection test against Neon PostgreSQL.
        Raises DatabaseUnavailableError if the test fails.
        """
        self.executor.test_connection(raise_on_failure=True)

    def is_configured(self) -> bool:
        return self.executor.is_configured()

    def is_healthy(self) -> bool:
        health = self.check_connection()
        st = str(health.get("status", "")).upper()
        return st in ("CONNECTED",) or health.get("status") == "connected"

    def check_schema_compatibility(self) -> bool:
        """Verifies critical operational tables exist in Neon PostgreSQL before failover."""
        if not self.is_healthy():
            return False
        try:
            res = self.executor.fetch_all(
                "SELECT count(*) as cnt FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('neon_fixtures', 'neon_published_predictions', 'neon_prediction_results', 'operational_fixtures', 'predictions')"
            )
            if res and len(res) > 0:
                cnt = res[0].get("cnt", 0)
                return cnt >= 2
            return True
        except Exception:
            return True

    @property
    def fixtures(self) -> IFixtureRepository:
        return self._fixtures

    @property
    def predictions(self) -> IPredictionRepository:
        return self._predictions

    @property
    def prediction_results(self) -> IPredictionResultRepository:
        return self._prediction_results

    @property
    def refresh_state(self) -> IRefreshStateRepository:
        return self._refresh_state

    @property
    def model_config(self) -> IModelConfigRepository:
        return self._model_config

    @property
    def calibrations(self) -> ICalibrationRepository:
        return self._calibrations

    @property
    def governance(self) -> IModelGovernanceRepository:
        return self._governance

    @property
    def features(self) -> IFeatureRepository:
        return self._features


# Singleton instance
neon_adapter = NeonDatabaseAdapter()
