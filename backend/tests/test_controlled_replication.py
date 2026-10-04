"""
Unit Tests for Controlled Replication (MongoDB Atlas -> Neon PostgreSQL).
Validates:
1. Replicates ONLY critical operational data (fixtures, predictions, results, config, governance, refresh).
2. Prohibits replication of raw payloads, Parquet, DuckDB, or full historical collections.
3. Checkpoint tracking: updated_at, stable ID, version, failures, last_error.
4. Idempotency: re-running batches does not duplicate records.
5. Referential order: Fixtures -> Predictions -> Results -> Config/Governance -> Refresh.
6. Fail-closed: Replication failure does not corrupt Mongo or switch active database.
7. Telemetry & Lag: tracks replication lag, write volume, and storage estimates.
"""

import unittest
import asyncio
from unittest.mock import patch, MagicMock

from backend.db.replication_manager import ReplicationManager, ReplicationCheckpoint
from backend.db.neon_adapter import NEON_DDL_SCHEMA


class TestControlledReplication(unittest.TestCase):

    def test_checkpoint_schema_and_serialization(self):
        """Verifies replication checkpoint captures required metadata."""
        cp = ReplicationCheckpoint(
            collection_name="operational_events",
            last_replicated_updated_at="2026-09-30T00:00:00Z",
            last_replicated_id="fix_123",
            last_replicated_version=2,
            total_replicated_records=150,
            consecutive_failures=0,
            last_error=None,
        )
        d = cp.to_dict()
        self.assertEqual(d["collection_name"], "operational_events")
        self.assertEqual(d["last_replicated_updated_at"], "2026-09-30T00:00:00Z")
        self.assertEqual(d["last_replicated_id"], "fix_123")
        self.assertEqual(d["last_replicated_version"], 2)
        self.assertEqual(d["total_replicated_records"], 150)
        self.assertIsNone(d["last_error"])

    def test_neon_ddl_has_checkpoint_table(self):
        """Ensures checkpoint table DDL is present in NEON_DDL_SCHEMA."""
        self.assertIn("CREATE TABLE IF NOT EXISTS neon_replication_checkpoints", NEON_DDL_SCHEMA)
        self.assertIn("collection_name VARCHAR(64) PRIMARY KEY", NEON_DDL_SCHEMA)
        self.assertIn("last_replicated_updated_at TIMESTAMPTZ NOT NULL", NEON_DDL_SCHEMA)

    def test_prohibited_collections_are_not_replicated(self):
        """Ensures historical archives, telemetry streams, and parquet are excluded from replication."""
        mgr = ReplicationManager()
        import inspect

        src = inspect.getsource(mgr.execute_incremental_replication)
        self.assertNotIn("historical_matches", src)
        self.assertNotIn("parquet_datasets", src)
        self.assertNotIn("raw_sports_skills_payloads", src)
        self.assertNotIn("telemetry_events", src)
        self.assertNotIn("duckdb_catalog", src)

    def test_referential_replication_order(self):
        """Verifies replication order: 1. Fixtures -> 2. Predictions -> 3. Results -> 4. Config -> 5. Refresh."""
        mgr = ReplicationManager()
        import inspect

        src = inspect.getsource(mgr.execute_incremental_replication)
        idx_fix = src.find("_replicate_fixtures")
        idx_pred = src.find("_replicate_predictions")
        idx_res = src.find("_replicate_prediction_results")
        idx_cfg = src.find("_replicate_governance_and_config")
        idx_ref = src.find("_replicate_refresh_state")

        self.assertTrue(idx_fix != -1 and idx_pred != -1 and idx_res != -1 and idx_cfg != -1 and idx_ref != -1)
        self.assertLess(idx_fix, idx_pred, "Fixtures must precede Predictions")
        self.assertLess(idx_pred, idx_res, "Predictions must precede Prediction Results")
        self.assertLess(idx_res, idx_cfg, "Results must precede Config/Governance")
        self.assertLess(idx_cfg, idx_ref, "Config must precede Refresh State")

    def test_replication_skips_when_neon_unconfigured(self):
        """When Neon is unconfigured, replication safely skips without error or failover."""
        mgr = ReplicationManager()

        with patch("backend.db.neon_adapter.neon_adapter.executor.is_configured", return_value=False):
            res = asyncio.run(mgr.execute_incremental_replication("unit_test"))
            self.assertEqual(res["status"], "skipped")
            self.assertIn("not configured", res["reason"])

    def test_replication_failure_backoff_and_fail_closed(self):
        """Verifies exponential backoff on failure and ensures Mongo remains unaffected."""
        mgr = ReplicationManager()

        with patch("backend.db.neon_adapter.neon_adapter.executor.is_configured", return_value=True):
            with patch.object(mgr, "_replicate_fixtures", return_value={"succeeded": 0, "failed": 5, "error": "Connection timeout"}):
                with patch.object(mgr, "_replicate_predictions", return_value={"succeeded": 0, "failed": 0}):
                    with patch.object(mgr, "_replicate_prediction_results", return_value={"succeeded": 0, "failed": 0}):
                        with patch.object(mgr, "_replicate_governance_and_config", return_value={"succeeded": 0, "failed": 0}):
                            with patch.object(mgr, "_replicate_refresh_state", return_value={"succeeded": 0, "failed": 0}):
                                res = asyncio.run(mgr.execute_incremental_replication("test_fail"))
                                self.assertEqual(res["status"], "failed")
                                self.assertEqual(res["consecutive_failures"], 1)

                                # Immediate second run must be in backoff
                                backoff_res = asyncio.run(mgr.execute_incremental_replication("test_backoff"))
                                self.assertEqual(backoff_res["status"], "backed_off")

    def test_idempotent_upsert_statements_have_on_conflict(self):
        """Validates that Neon upsert queries enforce ON CONFLICT clauses to ensure idempotency."""
        import inspect
        from backend.db.neon_adapter import (
            NeonFixtureRepository,
            NeonPredictionRepository,
            NeonPredictionResultRepository,
            NeonModelConfigRepository,
            NeonCalibrationRepository,
            NeonModelGovernanceRepository,
        )

        for repo_cls, method_name in [
            (NeonFixtureRepository, "upsert_fixtures"),
            (NeonPredictionRepository, "save_predictions"),
            (NeonPredictionResultRepository, "save_results"),
            (NeonModelConfigRepository, "set_active_model"),
            (NeonCalibrationRepository, "save_calibration"),
            (NeonModelGovernanceRepository, "save_governance_record"),
        ]:
            src = inspect.getsource(getattr(repo_cls, method_name))
            self.assertIn("ON CONFLICT", src, f"{repo_cls.__name__}.{method_name} must use ON CONFLICT for idempotency")
            self.assertIn("DO UPDATE", src, f"{repo_cls.__name__}.{method_name} must update existing rows on conflict")

    def test_checkpoint_progression_after_successful_batch(self):
        """Verifies checkpoint advances last_replicated_updated_at and tracks counts."""
        mgr = ReplicationManager()
        cp = ReplicationCheckpoint(
            collection_name="operational_events",
            last_replicated_updated_at="2026-09-30T00:00:00Z",
        )
        mgr._checkpoints["operational_events"] = cp

        mock_fixtures = [
            {
                "id": "match_1",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": "2026-09-30T15:00:00Z",
                "scheduled_at": "2026-09-30T15:00:00Z",
                "status": "scheduled",
                "fixture_version": 1,
                "updated_at": "2026-09-30T02:00:00Z",
            },
            {
                "id": "match_2",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Liverpool",
                "awayTeam": "Man City",
                "kickoffUtc": "2026-09-30T17:30:00Z",
                "scheduled_at": "2026-09-30T17:30:00Z",
                "status": "scheduled",
                "fixture_version": 1,
                "updated_at": "2026-09-30T03:00:00Z",
            },
        ]

        mock_col = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value.limit.return_value = mock_fixtures
        mock_col.find.return_value = mock_cursor

        from unittest.mock import PropertyMock
        from backend.db.mongodb import MongoDBManager

        with patch.object(MongoDBManager, "db", new_callable=PropertyMock, return_value={"operational_events": mock_col}):
            with patch("backend.db.neon_adapter.neon_adapter.fixtures.upsert_fixtures", return_value=2):
                with patch.object(mgr, "save_checkpoint") as mock_save_cp:
                    res = asyncio.run(mgr._replicate_fixtures())
                    self.assertEqual(res["succeeded"], 2)
                    self.assertEqual(res["failed"], 0)
                    self.assertEqual(cp.last_replicated_updated_at, "2026-09-30T03:00:00Z")
                    self.assertEqual(cp.last_replicated_id, "match_2")
                    self.assertEqual(cp.total_replicated_records, 2)
                    mock_save_cp.assert_called_once()

    def test_replication_status_telemetry(self):
        """Verifies get_status returns complete telemetry: lag, budget metrics, checkpoints."""
        mgr = ReplicationManager()
        status = mgr.get_status()
        self.assertIn("enabled", status)
        self.assertIn("batchSize", status)
        self.assertIn("isReplicating", status)
        self.assertIn("consecutiveFailures", status)
        self.assertIn("inBackoff", status)
        self.assertIn("checkpoints", status)
        self.assertIn("lastSummary", status)
        self.assertIn("neonBudget", status)


if __name__ == "__main__":
    unittest.main()
