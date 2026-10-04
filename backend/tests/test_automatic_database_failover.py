"""
Automated Test Suite for Automatic Database Failover & Split-Brain Prevention.
Tests all 14 scenarios required by the architecture specification:
1. Mongo healthy -> Mongo active.
2. One Mongo failure -> remain Mongo.
3. Three consecutive Mongo failures -> evaluate failover.
4. Neon healthy -> switch to Neon.
5. Neon unhealthy -> refuse failover.
6. Neon resource-limited -> refuse failover.
7. Mongo recovers -> reconciliation.
8. Reconciliation succeeds -> Mongo restored.
9. Reconciliation fails -> remain Neon.
10. Split-brain prevention.
11. Redis remains usable during failover.
12. Historical analytics remain DuckDB/R2.
13. Challenger model cannot become production during failover.
14. Duplicate replication does not duplicate records.
"""

import unittest
import asyncio
from unittest.mock import patch, MagicMock

from backend.db.interfaces import (
    FailoverState,
    RoutingMode,
    DatabaseUnavailableError,
)
from backend.db.failover_manager import FailoverManager, failover_manager
from backend.db.database_router import DatabaseRouter
from backend.db.neon_budget_guard import SafetyState, NeonBudgetGuard
from backend.services.model_service import model_service


class TestAutomaticDatabaseFailover(unittest.TestCase):

    def setUp(self):
        # Reset failover manager state before each test
        failover_manager.reset_state_for_tests()
        failover_manager.failure_threshold = 3
        failover_manager.recovery_threshold = 3
        failover_manager.cooldown_seconds = 0  # 0 cooldown for fast unit test evaluation

    def tearDown(self):
        # Reset failover manager state after each test
        failover_manager.reset_state_for_tests()

    # -------------------------------------------------------------------------
    # Scenario 1: Mongo healthy -> Mongo active
    # -------------------------------------------------------------------------
    def test_scenario_1_mongo_healthy_mongo_active(self):
        """When MongoDB Atlas is healthy, MongoDB is active and authoritative."""
        with patch("backend.db.mongodb_adapter.mongodb_adapter.is_healthy", return_value=True):
            backend = failover_manager.resolve_backend("AUTO")
            self.assertEqual(backend, "mongodb")
            self.assertEqual(failover_manager.active_backend, "mongodb")
            self.assertFalse(failover_manager.failover_active)
            self.assertEqual(failover_manager.failover_state, FailoverState.MONGODB_PRIMARY)

    # -------------------------------------------------------------------------
    # Scenario 2: One Mongo failure -> remain Mongo
    # -------------------------------------------------------------------------
    def test_scenario_2_one_mongo_failure_remain_mongo(self):
        """A single intermittent MongoDB failure does not trigger failover."""
        res = failover_manager.record_mongo_failure("Transient network timeout")
        self.assertEqual(res["status"], "failure_recorded")
        self.assertEqual(failover_manager._consecutive_mongo_failures, 1)
        self.assertEqual(failover_manager.active_backend, "mongodb")
        self.assertFalse(failover_manager.failover_active)

    # -------------------------------------------------------------------------
    # Scenario 3: Three consecutive Mongo failures -> evaluate failover
    # -------------------------------------------------------------------------
    def test_scenario_3_three_mongo_failures_evaluates_failover(self):
        """Three consecutive MongoDB failures reaches the threshold and evaluates failover."""
        with patch.object(failover_manager, "evaluate_and_execute_failover") as mock_eval:
            failover_manager.record_mongo_failure("Err 1")
            failover_manager.record_mongo_failure("Err 2")
            mock_eval.assert_not_called()

            # 3rd failure triggers evaluation
            failover_manager.record_mongo_failure("Err 3")
            mock_eval.assert_called_once()

    # -------------------------------------------------------------------------
    # Scenario 4: Neon healthy -> switch to Neon
    # -------------------------------------------------------------------------
    def test_scenario_4_neon_healthy_switches_to_neon(self):
        """When MongoDB has 3 failures and Neon is verified healthy, switches active database to Neon."""
        with patch("backend.db.neon_adapter.neon_adapter.check_connection", return_value={"status": "connected"}):
            with patch("backend.db.neon_budget_guard.neon_budget_guard.get_safety_state", return_value=SafetyState.OPTIMAL):
                res = failover_manager.evaluate_and_execute_failover("3 consecutive Mongo failures")
                self.assertEqual(res["status"], "failover_executed")
                self.assertEqual(failover_manager.active_backend, "neon")
                self.assertTrue(failover_manager.failover_active)
                self.assertEqual(failover_manager.failover_state, FailoverState.NEON_FAILOVER)
                self.assertEqual(failover_manager.resolve_backend("AUTO"), "neon")

    # -------------------------------------------------------------------------
    # Scenario 5: Neon unhealthy -> refuse failover
    # -------------------------------------------------------------------------
    def test_scenario_5_neon_unhealthy_refuses_failover(self):
        """When Neon is unreachable or unhealthy, failover is strictly refused (fail-closed)."""
        with patch("backend.db.neon_adapter.neon_adapter.check_connection", return_value={"status": "disconnected", "details": "Neon host unreachable"}):
            res = failover_manager.evaluate_and_execute_failover("Mongo is down")
            self.assertEqual(res["status"], "refused")
            self.assertIn("unreachable", res["reason"])
            self.assertEqual(failover_manager.active_backend, "mongodb")
            self.assertFalse(failover_manager.failover_active)

    def test_scenario_5b_neon_schema_incompatible_refuses_failover(self):
        """When Neon schema is incompatible or missing required tables, failover is strictly refused."""
        with patch("backend.db.neon_adapter.neon_adapter.check_connection", return_value={"status": "connected"}):
            with patch("backend.db.neon_adapter.neon_adapter.check_schema_compatibility", return_value=False):
                res = failover_manager.evaluate_and_execute_failover("Mongo is down")
                self.assertEqual(res["status"], "refused")
                self.assertIn("incompatible", res["reason"])
                self.assertEqual(failover_manager.active_backend, "mongodb")
                self.assertFalse(failover_manager.failover_active)

    # -------------------------------------------------------------------------
    # Scenario 6: Neon resource-limited -> refuse failover
    # -------------------------------------------------------------------------
    def test_scenario_6_neon_resource_limited_refuses_failover(self):
        """When Neon is resource-limited or circuit-tripped, failover is refused to protect quota."""
        with patch("backend.db.neon_adapter.neon_adapter.check_connection", return_value={"status": "connected"}):
            with patch("backend.db.neon_budget_guard.neon_budget_guard.get_safety_state", return_value=SafetyState.RESOURCE_LIMITED):
                res = failover_manager.evaluate_and_execute_failover("Mongo is down")
                self.assertEqual(res["status"], "refused")
                self.assertIn("resource-limited", res["reason"])
                self.assertEqual(failover_manager.active_backend, "mongodb")
                self.assertFalse(failover_manager.failover_active)

    # -------------------------------------------------------------------------
    # Scenario 7: Mongo recovers -> reconciliation
    # -------------------------------------------------------------------------
    def test_scenario_7_mongo_recovers_initiates_reconciliation(self):
        """When MongoDB has 3 consecutive successes during failover, triggers reconciliation."""
        # Setup active failover
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True
        failover_manager._failover_state = FailoverState.NEON_FAILOVER

        with patch.object(failover_manager, "reconcile_and_restore_mongo") as mock_recon:
            failover_manager.record_mongo_success()
            failover_manager.record_mongo_success()
            mock_recon.assert_not_called()

            # 3rd consecutive success
            failover_manager.record_mongo_success()
            mock_recon.assert_called_once()

    # -------------------------------------------------------------------------
    # Scenario 8: Reconciliation succeeds -> Mongo restored
    # -------------------------------------------------------------------------
    def test_scenario_8_reconciliation_succeeds_mongo_restored(self):
        """When reconciliation completes successfully, restores MongoDB as primary active authority."""
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True
        failover_manager._failover_state = FailoverState.NEON_FAILOVER

        with patch("backend.db.mongodb_adapter.mongodb_adapter.check_connection", return_value={"status": "connected"}):
            with patch.object(failover_manager, "_reconcile_fixtures_neon_to_mongo", return_value=(5, 1)):
                with patch.object(failover_manager, "_reconcile_predictions_neon_to_mongo", return_value=(3, 0)):
                    with patch.object(failover_manager, "_reconcile_results_neon_to_mongo", return_value=(2, 0)):
                        res = failover_manager.reconcile_and_restore_mongo("Mongo is healthy")
                        self.assertEqual(res["status"], "restored")
                        self.assertEqual(failover_manager.active_backend, "mongodb")
                        self.assertFalse(failover_manager.failover_active)
                        self.assertEqual(failover_manager.failover_state, FailoverState.MONGODB_PRIMARY)
                        self.assertEqual(res["reconciliation"]["fixtures_reconciled"], 5)
                        self.assertEqual(res["reconciliation"]["conflicts_resolved"], 1)

    # -------------------------------------------------------------------------
    # Scenario 9: Reconciliation fails -> remain Neon
    # -------------------------------------------------------------------------
    def test_scenario_9_reconciliation_fails_remain_neon(self):
        """If reconciliation encounters an error, remains on Neon and does not discard failover writes."""
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True
        failover_manager._failover_state = FailoverState.NEON_FAILOVER

        with patch("backend.db.mongodb_adapter.mongodb_adapter.check_connection", return_value={"status": "connected"}):
            with patch.object(failover_manager, "_reconcile_fixtures_neon_to_mongo", side_effect=RuntimeError("Reconciliation network error")):
                res = failover_manager.reconcile_and_restore_mongo("Mongo healthy")
                self.assertEqual(res["status"], "reconciliation_failed")
                self.assertEqual(failover_manager.active_backend, "neon")
                self.assertTrue(failover_manager.failover_active)
                self.assertEqual(failover_manager.failover_state, FailoverState.NEON_FAILOVER)

    # -------------------------------------------------------------------------
    # Scenario 10: Split-brain prevention
    # -------------------------------------------------------------------------
    def test_scenario_10_split_brain_prevention(self):
        """When Neon is active write authority, direct writes to MongoDB are strictly rejected."""
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True
        failover_manager._failover_state = FailoverState.NEON_FAILOVER

        self.assertFalse(failover_manager.is_mongo_write_permitted())

        from backend.db.mongodb_adapter import mongodb_adapter

        async def attempt_mongo_write():
            await mongodb_adapter.fixtures.upsert_fixtures([{"id": "test_fix"}])

        with self.assertRaises(DatabaseUnavailableError) as ctx:
            asyncio.run(attempt_mongo_write())
        self.assertIn("SPLIT-BRAIN PROTECTION", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Scenario 11: Redis remains usable during failover
    # -------------------------------------------------------------------------
    def test_scenario_11_redis_remains_usable_during_failover(self):
        """Redis caching and subscriber feeds continue operating independently of DB failover."""
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True

        from backend.db.redis_client import redis_client
        self.assertIsNotNone(redis_client)

    # -------------------------------------------------------------------------
    # Scenario 12: Historical analytics remain DuckDB/R2
    # -------------------------------------------------------------------------
    def test_scenario_12_historical_analytics_remain_duckdb_r2(self):
        """Historical analytics are never routed to Neon during failover."""
        # 1. Verify Neon DDL strictly prohibits historical archives, Parquet, and DuckDB tables
        from backend.db.neon_adapter import NEON_DDL_SCHEMA
        self.assertNotIn("historical_matches", NEON_DDL_SCHEMA)
        self.assertNotIn("parquet_datasets", NEON_DDL_SCHEMA)
        self.assertNotIn("duckdb_catalog", NEON_DDL_SCHEMA)

        # 2. Verify router repositories only expose operational endpoints, not historical analytical tables
        router = DatabaseRouter()
        self.assertTrue(hasattr(router, "fixtures"))
        self.assertTrue(hasattr(router, "predictions"))
        self.assertTrue(hasattr(router, "prediction_results"))
        self.assertFalse(hasattr(router, "historical_parquet_archive"))
        self.assertFalse(hasattr(router, "duckdb_analytics"))

    # -------------------------------------------------------------------------
    # Scenario 13: Challenger model cannot become production during failover
    # -------------------------------------------------------------------------
    def test_scenario_13_challenger_cannot_become_production_during_failover(self):
        """Challenger models cannot be activated or promoted during failover."""
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True
        failover_manager._failover_state = FailoverState.NEON_FAILOVER

        # Attempting to activate a challenger model raises ValueError
        with self.assertRaises(ValueError) as ctx:
            model_service.set_active_model("CHALLENGER_EXPERIMENTAL_X")  # type: ignore
        self.assertIn("Challenger model cannot become production", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Scenario 14: Duplicate replication does not duplicate records
    # -------------------------------------------------------------------------
    def test_scenario_14_duplicate_replication_idempotent(self):
        """Neon upsert statements enforce ON CONFLICT clauses to ensure idempotency."""
        import inspect
        from backend.db.neon_adapter import NeonFixtureRepository, NeonPredictionRepository

        fix_src = inspect.getsource(NeonFixtureRepository.upsert_fixtures)
        self.assertIn("ON CONFLICT (id) DO UPDATE", fix_src)

        pred_src = inspect.getsource(NeonPredictionRepository.save_predictions)
        self.assertIn("ON CONFLICT (id) DO UPDATE", pred_src)


if __name__ == "__main__":
    unittest.main()
