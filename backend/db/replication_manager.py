"""
Replication Manager.
Controls incremental, bounded, and idempotent replication from MongoDB Atlas (Primary)
to Neon PostgreSQL (Secondary/Failover).

Enforces:
1. Replicates ONLY small operational dataset:
   - Current/future fixtures, live fixtures, recent completed fixtures
   - Published predictions
   - Prediction results
   - Model configuration
   - Calibration metadata
   - Model governance
   - Refresh run states
2. DOES NOT REPLICATE:
   - Historical match archives
   - Raw SportsSkills payloads
   - Parquet datasets
   - DuckDB data
   - Model-training datasets
   - Telemetry streams / large logs
3. Strictly incremental using checkpoints (updated_at + stable ID/version).
4. Idempotent PostgreSQL UPSERT / ON CONFLICT logic.
5. All Neon writes pass through NeonBudgetGuard.
6. Fail-closed: Failures do not affect Mongo primary or activate failover.
7. Tracks replication lag, write volume, and storage estimates.
"""

import time
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

from backend.config import settings
from backend.db.interfaces import DatabaseUnavailableError, BudgetExceededError
from backend.db.mongodb import mongo_manager
from backend.db.neon_adapter import neon_adapter
from backend.db.neon_budget_guard import neon_budget_guard

logger = logging.getLogger("predictpro.replication")


class ReplicationCheckpoint:
    """Represents a persistent replication checkpoint for a specific operational entity."""

    def __init__(
        self,
        collection_name: str,
        last_replicated_updated_at: str = "1970-01-01T00:00:00Z",
        last_replicated_id: Optional[str] = None,
        last_replicated_version: int = 1,
        total_replicated_records: int = 0,
        consecutive_failures: int = 0,
        last_error: Optional[str] = None,
        updated_at: Optional[str] = None,
    ):
        self.collection_name = collection_name
        self.last_replicated_updated_at = last_replicated_updated_at
        self.last_replicated_id = last_replicated_id
        self.last_replicated_version = last_replicated_version
        self.total_replicated_records = total_replicated_records
        self.consecutive_failures = consecutive_failures
        self.last_error = last_error
        self.updated_at = updated_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "collection_name": self.collection_name,
            "last_replicated_updated_at": self.last_replicated_updated_at,
            "last_replicated_id": self.last_replicated_id,
            "last_replicated_version": self.last_replicated_version,
            "total_replicated_records": self.total_replicated_records,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


class ReplicationManager:
    """
    Coordinates controlled, incremental, and bounded replication from MongoDB Atlas
    to Neon PostgreSQL standby operational store.
    """

    def __init__(self):
        self.batch_size = getattr(settings, "neon_replication_batch_size", 50)
        self.enabled = getattr(settings, "neon_replication_enabled", True)
        self._checkpoints: Dict[str, ReplicationCheckpoint] = {}
        self._last_replication_summary: Dict[str, Any] = {
            "last_run_at": None,
            "status": "idle",
            "total_replicated": 0,
            "total_failed": 0,
            "replication_lag_seconds": 0.0,
            "neon_storage_estimate_kb": 0.0,
            "neon_write_volume_ops": 0,
            "errors": [],
        }
        self._is_replicating = False
        self._failure_backoff_until: float = 0.0
        self._consecutive_failures: int = 0

    def _get_checkpoint_collection(self):
        """MongoDB collection storing replication checkpoints for persistence."""
        try:
            return mongo_manager.db["replication_checkpoints"]
        except Exception:
            return None

    async def get_checkpoint(self, collection_name: str) -> ReplicationCheckpoint:
        """Loads checkpoint from memory or MongoDB Atlas."""
        if collection_name in self._checkpoints:
            return self._checkpoints[collection_name]

        col = self._get_checkpoint_collection()
        if col is not None:
            try:
                doc = col.find_one({"collection_name": collection_name}, {"_id": 0})
                if doc:
                    cp = ReplicationCheckpoint(**doc)
                    self._checkpoints[collection_name] = cp
                    return cp
            except Exception as e:
                logger.warning(f"Error reading replication checkpoint for {collection_name}: {e}")

        # Default checkpoint
        cp = ReplicationCheckpoint(collection_name=collection_name)
        self._checkpoints[collection_name] = cp
        return cp

    async def save_checkpoint(self, checkpoint: ReplicationCheckpoint) -> None:
        """Persists checkpoint to memory, MongoDB, and Neon checkpoint table."""
        self._checkpoints[checkpoint.collection_name] = checkpoint
        checkpoint.updated_at = datetime.now(timezone.utc).isoformat()

        # 1. Save to MongoDB
        col = self._get_checkpoint_collection()
        if col is not None:
            try:
                col.update_one(
                    {"collection_name": checkpoint.collection_name},
                    {"$set": checkpoint.to_dict()},
                    upsert=True,
                )
            except Exception as e:
                logger.warning(f"Error saving checkpoint to MongoDB: {e}")

        # 2. Save to Neon PostgreSQL
        if neon_adapter.executor.is_configured():
            sql = """
            INSERT INTO neon_replication_checkpoints (
                collection_name, last_replicated_updated_at, last_replicated_id,
                last_replicated_version, total_replicated_records, consecutive_failures,
                last_error, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (collection_name) DO UPDATE SET
                last_replicated_updated_at = EXCLUDED.last_replicated_updated_at,
                last_replicated_id = EXCLUDED.last_replicated_id,
                last_replicated_version = EXCLUDED.last_replicated_version,
                total_replicated_records = EXCLUDED.total_replicated_records,
                consecutive_failures = EXCLUDED.consecutive_failures,
                last_error = EXCLUDED.last_error,
                updated_at = EXCLUDED.updated_at;
            """
            try:
                await neon_adapter.executor.execute_statement(
                    sql,
                    [
                        checkpoint.collection_name,
                        checkpoint.last_replicated_updated_at,
                        checkpoint.last_replicated_id,
                        checkpoint.last_replicated_version,
                        checkpoint.total_replicated_records,
                        checkpoint.consecutive_failures,
                        checkpoint.last_error,
                        checkpoint.updated_at,
                    ],
                    "save_replication_checkpoint",
                )
            except Exception as e:
                logger.debug(f"Notice saving checkpoint to Neon: {e}")

    async def execute_incremental_replication(self, trigger_source: str = "lifecycle") -> Dict[str, Any]:
        """
        Executes a controlled incremental replication batch in strict referential order:
        1. Operational Fixtures (live, upcoming, recent completed)
        2. Predictions (published only)
        3. Prediction Results (evaluated only)
        4. Model Configuration & Governance
        5. Refresh State

        Idempotent and protected by NeonBudgetGuard.
        Fails closed without affecting MongoDB or activating failover.
        """
        if not self.enabled:
            return {"status": "skipped", "reason": "Replication is disabled in settings"}

        now_ts = time.time()
        # Exponential backoff check on consecutive errors (max 300s)
        if now_ts < self._failure_backoff_until:
            wait_rem = int(self._failure_backoff_until - now_ts)
            return {
                "status": "backed_off",
                "reason": f"In backoff due to previous failure. Retry in {wait_rem}s",
                "consecutive_failures": self._consecutive_failures,
            }

        # Check if Neon is configured
        if not neon_adapter.executor.is_configured():
            return {
                "status": "skipped",
                "reason": "Neon PostgreSQL is not configured (NEON_DATABASE_URL missing)",
            }

        # Check safety state: halt bulk replication if Resource-Limited
        if not neon_budget_guard.is_bulk_sync_permitted():
            safety_state = neon_budget_guard.get_safety_state()
            return {
                "status": "resource_limited",
                "reason": f"Neon safety state is {safety_state}. Bulk replication halted to preserve quota.",
                "safetyState": safety_state,
            }

        # Lock to avoid concurrent replication jobs
        if self._is_replicating:
            return {"status": "in_progress", "reason": "Another replication run is currently active"}

        self._is_replicating = True
        start_time = time.perf_counter()
        now_iso = datetime.now(timezone.utc).isoformat()
        errors: List[str] = []
        total_succeeded = 0
        total_failed = 0
        step_metrics: Dict[str, Any] = {}

        try:
            # 1. Replicate Operational Fixtures
            fix_res = await self._replicate_fixtures()
            step_metrics["fixtures"] = fix_res
            total_succeeded += fix_res.get("succeeded", 0)
            total_failed += fix_res.get("failed", 0)
            if fix_res.get("error"):
                errors.append(f"Fixtures: {fix_res['error']}")

            # 2. Replicate Published Predictions
            pred_res = await self._replicate_predictions()
            step_metrics["predictions"] = pred_res
            total_succeeded += pred_res.get("succeeded", 0)
            total_failed += pred_res.get("failed", 0)
            if pred_res.get("error"):
                errors.append(f"Predictions: {pred_res['error']}")

            # 3. Replicate Prediction Results
            res_res = await self._replicate_prediction_results()
            step_metrics["prediction_results"] = res_res
            total_succeeded += res_res.get("succeeded", 0)
            total_failed += res_res.get("failed", 0)
            if res_res.get("error"):
                errors.append(f"Prediction Results: {res_res['error']}")

            # 4. Replicate Configuration, Calibration & Governance
            cfg_res = await self._replicate_governance_and_config()
            step_metrics["configuration"] = cfg_res
            total_succeeded += cfg_res.get("succeeded", 0)
            total_failed += cfg_res.get("failed", 0)
            if cfg_res.get("error"):
                errors.append(f"Configuration: {cfg_res['error']}")

            # 5. Replicate Refresh State
            ref_res = await self._replicate_refresh_state()
            step_metrics["refresh_state"] = ref_res
            total_succeeded += ref_res.get("succeeded", 0)
            total_failed += ref_res.get("failed", 0)
            if ref_res.get("error"):
                errors.append(f"Refresh State: {ref_res['error']}")

            # 6. Safe retention pruning to keep Neon storage small and bounded
            try:
                prune_res = await neon_budget_guard.prune_expired_failover_data(neon_adapter.executor)
                step_metrics["storage_pruning"] = prune_res
            except Exception as pe:
                logger.debug(f"Retention pruning notice: {pe}")

            # Update estimated storage in budget guard
            neon_budget_guard.record_storage_estimate(int(total_succeeded * 2400))

            # Handle outcome and backoff
            if errors:
                self._consecutive_failures += 1
                backoff_delay = min(300, (2 ** min(self._consecutive_failures, 6)) * 5)
                self._failure_backoff_until = time.time() + backoff_delay
                overall_status = "partial" if total_succeeded > 0 else "failed"
            else:
                self._consecutive_failures = 0
                self._failure_backoff_until = 0.0
                overall_status = "success"

        except Exception as e:
            errors.append(f"Fatal replication error: {str(e)}")
            self._consecutive_failures += 1
            self._failure_backoff_until = time.time() + 30
            overall_status = "failed"
        finally:
            self._is_replicating = False

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        lag_seconds = await self._calculate_max_replication_lag()

        # Update telemetry summary
        self._last_replication_summary = {
            "last_run_at": now_iso,
            "trigger_source": trigger_source,
            "status": overall_status,
            "duration_ms": duration_ms,
            "total_replicated": total_succeeded,
            "total_failed": total_failed,
            "consecutive_failures": self._consecutive_failures,
            "replication_lag_seconds": lag_seconds,
            "neon_storage_estimate_kb": round((total_succeeded * 2.4), 2),
            "neon_write_volume_ops": total_succeeded,
            "step_metrics": step_metrics,
            "errors": errors,
        }

        # Structured operational log
        log_payload = {
            "event": "neon_controlled_replication",
            "trigger": trigger_source,
            "status": overall_status,
            "duration_ms": duration_ms,
            "replicated_records": total_succeeded,
            "failed_records": total_failed,
            "replication_lag_seconds": lag_seconds,
            "errors_count": len(errors),
        }
        logger.info(f"[Replication] {json.dumps(log_payload)}")

        return self._last_replication_summary

    async def _replicate_fixtures(self) -> Dict[str, Any]:
        """
        Replicates current/future, live, and recent completed fixtures.
        Excludes full historical match archives. Bounded by batch_size.
        """
        cp = await self.get_checkpoint("operational_events")
        col = mongo_manager.db["operational_events"]
        if col is None:
            return {"succeeded": 0, "failed": 0, "error": "MongoDB operational_events collection unavailable"}

        # Bounded query: only operational status, scheduled >= 48 hours ago, updated since checkpoint
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        query: Dict[str, Any] = {
            "updated_at": {"$gt": cp.last_replicated_updated_at},
            "scheduled_at": {"$gte": cutoff_iso},
            "status": {"$in": ["scheduled", "live", "in_play", "inplay", "completed", "FT", "1H", "2H", "HT"]},
        }

        projection = {
            "_id": 0,
            "id": 1,
            "sport": 1,
            "league": 1,
            "competition_id": 1,
            "homeTeam": 1,
            "awayTeam": 1,
            "kickoffUtc": 1,
            "scheduled_at": 1,
            "status": 1,
            "currentScore": 1,
            "venue": 1,
            "referee": 1,
            "canonical_key": 1,
            "source_event_id": 1,
            "fixture_version": 1,
            "updated_at": 1,
            "created_at": 1,
        }

        try:
            records = list(col.find(query, projection).sort("updated_at", 1).limit(self.batch_size))
        except Exception as e:
            return {"succeeded": 0, "failed": 0, "error": f"MongoDB fixture read failed: {e}"}

        if not records:
            return {"succeeded": 0, "failed": 0, "processed": 0}

        try:
            succeeded = await neon_adapter.fixtures.upsert_fixtures(records)
            latest_rec = records[-1]
            cp.last_replicated_updated_at = latest_rec.get("updated_at", cp.last_replicated_updated_at)
            cp.last_replicated_id = latest_rec.get("id")
            cp.last_replicated_version = latest_rec.get("fixture_version", 1)
            cp.total_replicated_records += succeeded
            cp.last_error = None
            cp.consecutive_failures = 0
            await self.save_checkpoint(cp)
            return {"succeeded": succeeded, "failed": len(records) - succeeded, "processed": len(records)}
        except Exception as e:
            cp.consecutive_failures += 1
            cp.last_error = str(e)
            await self.save_checkpoint(cp)
            return {"succeeded": 0, "failed": len(records), "error": str(e)}

    async def _replicate_predictions(self) -> Dict[str, Any]:
        """
        Replicates validated published predictions. Bounded by batch_size.
        """
        cp = await self.get_checkpoint("predictions")
        col = mongo_manager.db["predictions"]
        if col is None:
            return {"succeeded": 0, "failed": 0, "error": "MongoDB predictions collection unavailable"}

        query: Dict[str, Any] = {
            "updated_at": {"$gt": cp.last_replicated_updated_at},
            "validationStatus": "validated",
        }

        projection = {
            "_id": 0,
            "id": 1,
            "fixtureId": 1,
            "sport": 1,
            "league": 1,
            "homeTeam": 1,
            "awayTeam": 1,
            "kickoffUtc": 1,
            "date": 1,
            "modelVersion": 1,
            "validationStatus": 1,
            "published": 1,
            "calibratedPercentage": 1,
            "isBestOfDay": 1,
            "prediction_version": 1,
            "markets": 1,
            "updated_at": 1,
            "created_at": 1,
        }

        try:
            records = list(col.find(query, projection).sort("updated_at", 1).limit(self.batch_size))
        except Exception as e:
            return {"succeeded": 0, "failed": 0, "error": f"MongoDB prediction read failed: {e}"}

        if not records:
            return {"succeeded": 0, "failed": 0, "processed": 0}

        try:
            succeeded = await neon_adapter.predictions.save_predictions(records)
            latest_rec = records[-1]
            cp.last_replicated_updated_at = latest_rec.get("updated_at", cp.last_replicated_updated_at)
            cp.last_replicated_id = latest_rec.get("id")
            cp.last_replicated_version = latest_rec.get("prediction_version", 1)
            cp.total_replicated_records += succeeded
            cp.last_error = None
            cp.consecutive_failures = 0
            await self.save_checkpoint(cp)
            return {"succeeded": succeeded, "failed": len(records) - succeeded, "processed": len(records)}
        except Exception as e:
            cp.consecutive_failures += 1
            cp.last_error = str(e)
            await self.save_checkpoint(cp)
            return {"succeeded": 0, "failed": len(records), "error": str(e)}

    async def _replicate_prediction_results(self) -> Dict[str, Any]:
        """
        Replicates evaluated prediction results. Bounded by batch_size.
        """
        cp = await self.get_checkpoint("prediction_results")
        col = mongo_manager.db["prediction_results"]
        if col is None:
            return {"succeeded": 0, "failed": 0, "error": "MongoDB prediction_results collection unavailable"}

        query: Dict[str, Any] = {
            "updated_at": {"$gt": cp.last_replicated_updated_at},
        }

        projection = {
            "_id": 0,
            "id": 1,
            "prediction_id": 1,
            "fixtureId": 1,
            "sport": 1,
            "status": 1,
            "predicted_outcome": 1,
            "actual_outcome": 1,
            "is_correct": 1,
            "brier_score": 1,
            "details": 1,
            "evaluatedAt": 1,
            "updated_at": 1,
            "created_at": 1,
        }

        try:
            records = list(col.find(query, projection).sort("updated_at", 1).limit(self.batch_size))
        except Exception as e:
            return {"succeeded": 0, "failed": 0, "error": f"MongoDB results read failed: {e}"}

        if not records:
            return {"succeeded": 0, "failed": 0, "processed": 0}

        try:
            succeeded = await neon_adapter.prediction_results.save_results(records)
            latest_rec = records[-1]
            cp.last_replicated_updated_at = latest_rec.get("updated_at", cp.last_replicated_updated_at)
            cp.last_replicated_id = latest_rec.get("id") or latest_rec.get("prediction_id")
            cp.total_replicated_records += succeeded
            cp.last_error = None
            cp.consecutive_failures = 0
            await self.save_checkpoint(cp)
            return {"succeeded": succeeded, "failed": len(records) - succeeded, "processed": len(records)}
        except Exception as e:
            cp.consecutive_failures += 1
            cp.last_error = str(e)
            await self.save_checkpoint(cp)
            return {"succeeded": 0, "failed": len(records), "error": str(e)}

    async def _replicate_governance_and_config(self) -> Dict[str, Any]:
        """
        Replicates active model configs, calibration metadata, and model governance records.
        Uses checkpoints, explicit projections, and bounded queries.
        """
        succeeded = 0
        failed = 0

        # 1. Model configs (only active configuration or records newer than checkpoint)
        cp_cfg = await self.get_checkpoint("model_versions")
        cfg_col = mongo_manager.db["model_versions"]
        if cfg_col is not None:
            try:
                cfg_query = {
                    "$or": [
                        {"key": "active_model"},
                        {"updated_at": {"$gt": cp_cfg.last_replicated_updated_at}},
                    ]
                } if cp_cfg.last_replicated_updated_at != "1970-01-01T00:00:00Z" else {"key": "active_model"}
                cfg_projection = {
                    "_id": 0,
                    "key": 1,
                    "model_id": 1,
                    "sport": 1,
                    "tier": 1,
                    "is_production_ready": 1,
                    "updated_at": 1,
                }
                records = list(cfg_col.find(cfg_query, cfg_projection).sort("updated_at", 1).limit(self.batch_size))
                for doc in records:
                    m_id = doc.get("model_id") or "ELO + POISSON"
                    sport = doc.get("sport", "football")
                    tier = doc.get("tier", "production")
                    await neon_adapter.model_config.set_active_model(m_id, sport=sport, tier=tier)
                    succeeded += 1
                if records:
                    latest_rec = records[-1]
                    cp_cfg.last_replicated_updated_at = latest_rec.get("updated_at", cp_cfg.last_replicated_updated_at)
                    await self.save_checkpoint(cp_cfg)
            except Exception as e:
                failed += 1
                logger.warning(f"Model config replication notice: {e}")

        # 2. Calibration metadata (bounded by active/PRODUCTION or updated_at watermark)
        cp_cal = await self.get_checkpoint("calibrations")
        cal_col = mongo_manager.db["calibrations"]
        if cal_col is not None:
            try:
                cal_query = {
                    "$or": [
                        {"status": "PRODUCTION"},
                        {"updated_at": {"$gt": cp_cal.last_replicated_updated_at}},
                    ]
                } if cp_cal.last_replicated_updated_at != "1970-01-01T00:00:00Z" else {"status": "PRODUCTION"}
                cal_projection = {
                    "_id": 0,
                    "sport": 1,
                    "model": 1,
                    "market": 1,
                    "calibration_id": 1,
                    "calibration_method": 1,
                    "training_dataset": 1,
                    "training_period": 1,
                    "validation_period": 1,
                    "created_at": 1,
                    "updated_at": 1,
                    "metrics": 1,
                    "calibration_error": 1,
                    "status": 1,
                    "approved_at": 1,
                    "approved_by": 1,
                    "feature_schema_version": 1,
                    "thresholds_x": 1,
                    "thresholds_y": 1,
                }
                records = list(cal_col.find(cal_query, cal_projection).sort("updated_at", 1).limit(self.batch_size))
                for doc in records:
                    sport = doc.get("sport", "football")
                    model = doc.get("model", "ELO + POISSON")
                    market = doc.get("market", "1X2")
                    await neon_adapter.calibrations.save_calibration(sport, model, market, doc)
                    succeeded += 1
                if records:
                    latest_rec = records[-1]
                    cp_cal.last_replicated_updated_at = latest_rec.get("updated_at", cp_cal.last_replicated_updated_at)
                    await self.save_checkpoint(cp_cal)
            except Exception as e:
                failed += 1
                logger.warning(f"Calibration replication notice: {e}")

        # 3. Governance records (bounded query using updated_at watermark)
        cp_gov = await self.get_checkpoint("model_governance")
        gov_col = mongo_manager.db["model_governance"]
        if gov_col is not None:
            try:
                gov_query = {"updated_at": {"$gt": cp_gov.last_replicated_updated_at}} if cp_gov.last_replicated_updated_at != "1970-01-01T00:00:00Z" else {}
                gov_projection = {
                    "_id": 0,
                    "sport": 1,
                    "gate_name": 1,
                    "status": 1,
                    "criteria_results": 1,
                    "admin_notes": 1,
                    "validated_at": 1,
                    "updated_at": 1,
                }
                records = list(gov_col.find(gov_query, gov_projection).sort("updated_at", 1).limit(self.batch_size))
                for doc in records:
                    sport = doc.get("sport", "football")
                    gate = doc.get("gate_name", "challenger_gate")
                    status = doc.get("status", "pending")
                    crit = doc.get("criteria_results", {})
                    notes = doc.get("admin_notes", "")
                    await neon_adapter.governance.save_governance_record(sport, gate, status, crit, notes)
                    succeeded += 1
                if records:
                    latest_rec = records[-1]
                    cp_gov.last_replicated_updated_at = latest_rec.get("updated_at", cp_gov.last_replicated_updated_at)
                    await self.save_checkpoint(cp_gov)
            except Exception as e:
                failed += 1
                logger.warning(f"Governance replication notice: {e}")

        return {"succeeded": succeeded, "failed": failed}

    async def _replicate_refresh_state(self) -> Dict[str, Any]:
        """
        Replicates recent refresh run audit records.
        """
        cp = await self.get_checkpoint("refresh_runs")
        col = mongo_manager.db["refresh_runs"]
        if col is None:
            return {"succeeded": 0, "failed": 0}

        query: Dict[str, Any] = {
            "updated_at": {"$gt": cp.last_replicated_updated_at},
        }

        try:
            records = list(col.find(query, {"_id": 0}).sort("updated_at", 1).limit(20))
        except Exception:
            records = []

        if not records:
            return {"succeeded": 0, "failed": 0}

        succeeded = 0
        failed = 0
        for rec in records:
            try:
                ok = await neon_adapter.refresh_state.save_run(rec)
                if ok:
                    succeeded += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        if records:
            latest = records[-1]
            cp.last_replicated_updated_at = latest.get("updated_at", cp.last_replicated_updated_at)
            cp.last_replicated_id = latest.get("id")
            cp.total_replicated_records += succeeded
            await self.save_checkpoint(cp)

        return {"succeeded": succeeded, "failed": failed}

    async def _calculate_max_replication_lag(self) -> float:
        """
        Estimates the maximum replication lag in seconds across replicated entities.
        Compares latest record updated_at in MongoDB against last replicated checkpoint.
        """
        try:
            if mongo_manager.db is None:
                return 0.0
            col = mongo_manager.db["operational_events"]
            if col is None:
                return 0.0

            latest_doc = col.find_one({}, {"_id": 0, "updated_at": 1}, sort=[("updated_at", -1)])
            if not latest_doc or "updated_at" not in latest_doc:
                return 0.0

            mongo_latest_str = latest_doc["updated_at"]
            cp = await self.get_checkpoint("operational_events")
            cp_str = cp.last_replicated_updated_at

            mongo_dt = datetime.fromisoformat(mongo_latest_str.replace("Z", "+00:00"))
            cp_dt = datetime.fromisoformat(cp_str.replace("Z", "+00:00"))
            diff = (mongo_dt - cp_dt).total_seconds()
            return max(0.0, round(diff, 1))
        except Exception:
            return 0.0

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive replication telemetry for monitoring and admin dashboards."""
        checkpoints_summary = {k: v.to_dict() for k, v in self._checkpoints.items()}
        return {
            "enabled": self.enabled,
            "batchSize": self.batch_size,
            "isReplicating": self._is_replicating,
            "consecutiveFailures": self._consecutive_failures,
            "inBackoff": time.time() < self._failure_backoff_until,
            "backoffRemainingSeconds": max(0, int(self._failure_backoff_until - time.time())),
            "checkpoints": checkpoints_summary,
            "lastSummary": self._last_replication_summary,
            "neonBudget": neon_budget_guard.get_metrics(),
        }


replication_manager = ReplicationManager()
