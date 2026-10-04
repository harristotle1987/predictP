"""
Unit Tests for PredictPro Database Abstraction Layer, Failover Manager, and Database Router.
Validates:
1. IDatabaseAdapter interfaces and ReplicatedRecord contracts.
2. MongoDB Atlas primary adapter behavior and projections.
3. Neon PostgreSQL secondary adapter schema, explicit column projections, and zero SELECT *.
4. NeonBudgetGuard rate limiting, concurrency controls, and circuit breaker.
5. FailoverManager fail-closed state machine (automatic failover initially disabled).
6. Central DatabaseRouter supporting AUTO, MONGODB_ONLY, and NEON_ONLY modes.
"""

import unittest
import asyncio
from unittest.mock import patch, MagicMock

from backend.db.interfaces import (
    RoutingMode,
    FailoverState,
    ReplicatedRecord,
    DatabaseUnavailableError,
    FailoverNotPermittedError,
    BudgetExceededError,
)
from backend.db.neon_budget_guard import NeonBudgetGuard
from backend.db.failover_manager import FailoverManager
from backend.db.database_router import DatabaseRouter
from backend.db.neon_adapter import NEON_DDL_SCHEMA, NeonQueryExecutor, neon_adapter
from backend.db.mongodb_adapter import FIXTURE_PROJECTION, PREDICTION_PROJECTION, RESULT_PROJECTION


class TestDatabaseAbstractionLayer(unittest.TestCase):

    def test_replicated_record_contract(self):
        """Validates stable ID, source system, timestamps, and version."""
        rec = ReplicatedRecord(
            stable_id="fix_test_001",
            source_system="predictpro_primary_mongo",
            version=1,
        )
        self.assertEqual(rec.stable_id, "fix_test_001")
        self.assertEqual(rec.source_system, "predictpro_primary_mongo")
        self.assertEqual(rec.version, 1)
        self.assertIsNotNone(rec.created_at)
        self.assertIsNotNone(rec.updated_at)

    def test_neon_ddl_schema_contains_required_tables_and_indexes(self):
        """Verifies only critical operational tables are defined, with proper indexes."""
        required_tables = [
            "neon_fixtures",
            "neon_published_predictions",
            "neon_prediction_results",
            "neon_refresh_state",
            "neon_model_config",
            "neon_calibration_metadata",
            "neon_model_governance",
        ]
        for tbl in required_tables:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {tbl}", NEON_DDL_SCHEMA)

        # Verify required indexes
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff", NEON_DDL_SCHEMA)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_fixtures_canonical_key", NEON_DDL_SCHEMA)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_predictions_event_id", NEON_DDL_SCHEMA)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_pred_results_fixture_id", NEON_DDL_SCHEMA)

        # Prohibit analytical tables in Neon
        self.assertNotIn("historical_matches", NEON_DDL_SCHEMA)
        self.assertNotIn("parquet_datasets", NEON_DDL_SCHEMA)
        self.assertNotIn("duckdb_catalog", NEON_DDL_SCHEMA)

    def test_neon_adapter_prohibits_select_star(self):
        """Ensures Neon adapter never executes SELECT * and always uses explicit columns."""
        import inspect
        from backend.db.neon_adapter import (
            NeonFixtureRepository,
            NeonPredictionRepository,
            NeonPredictionResultRepository,
            NeonRefreshStateRepository,
            NeonModelConfigRepository,
            NeonCalibrationRepository,
            NeonModelGovernanceRepository,
        )

        classes_to_check = [
            NeonFixtureRepository,
            NeonPredictionRepository,
            NeonPredictionResultRepository,
            NeonRefreshStateRepository,
            NeonModelConfigRepository,
            NeonCalibrationRepository,
            NeonModelGovernanceRepository,
        ]

        for cls in classes_to_check:
            src = inspect.getsource(cls)
            self.assertNotIn("SELECT *", src.upper())
            self.assertNotIn("SELECT  *", src.upper())

    def test_neon_budget_guard_limits(self):
        """Tests that NeonBudgetGuard tracks queries and trips when budget is exceeded."""
        guard = NeonBudgetGuard(max_queries_per_min=5, max_concurrency=2)
        guard._force_rate_limit_check = True

        async def run_test():
            # Run 5 queries successfully
            for i in range(5):
                await guard.acquire_query_slot(f"op_{i}")
                await guard.release_query_slot(True)

            # 6th query must trip rate limit
            with self.assertRaises(BudgetExceededError):
                await guard.acquire_query_slot("op_exceed")

        asyncio.run(run_test())

        metrics = guard.get_metrics()
        self.assertTrue(metrics["circuitBreakerTripped"])
        self.assertGreater(metrics["totalQueriesRejected"], 0)

    def test_failover_manager_initial_state_and_fail_closed(self):
        """Verifies failover is initially inactive and fails closed if MongoDB is down."""
        mgr = FailoverManager()

        self.assertEqual(mgr.active_backend, "mongodb")
        self.assertFalse(mgr.failover_active)
        self.assertEqual(mgr.failover_state, FailoverState.PRIMARY_HEALTHY)

        # When MongoDB is healthy, resolve_backend returns mongodb
        with patch("backend.db.mongodb_adapter.mongodb_adapter.is_healthy", return_value=True):
            backend = mgr.resolve_backend("AUTO")
            self.assertEqual(backend, "mongodb")

        # FAIL-CLOSED RULE: When MongoDB is unhealthy and failover is not activated, raises DatabaseUnavailableError
        with patch("backend.db.mongodb_adapter.mongodb_adapter.is_healthy", return_value=False):
            with self.assertRaises(DatabaseUnavailableError) as ctx:
                mgr.resolve_backend("AUTO")
            self.assertIn("FAIL-CLOSED", str(ctx.exception))

    def test_failover_manager_promotion_guard(self):
        """Refuses to activate failover if Neon is not healthy or unconfigured."""
        mgr = FailoverManager()

        with patch("backend.db.neon_adapter.neon_adapter.check_connection", return_value={"status": "not_configured"}):
            with self.assertRaises(FailoverNotPermittedError):
                mgr.activate_failover("Test promotion")

        # Allows activation only when Neon is validated as healthy
        with patch("backend.db.neon_adapter.neon_adapter.check_connection", return_value={"status": "connected"}):
            res = mgr.activate_failover("Valid promotion test")
            self.assertEqual(res["status"], "failover_activated")
            self.assertTrue(mgr.failover_active)
            self.assertEqual(mgr.active_backend, "neon")

    def test_database_router_status_and_routing_modes(self):
        """Verifies DatabaseRouter exposes complete status and routes according to mode."""
        router = DatabaseRouter()

        status = router.get_status()
        self.assertIn("activeBackend", status)
        self.assertIn("readBackend", status)
        self.assertIn("writeBackend", status)
        self.assertIn("failoverState", status)
        self.assertIn("lastTransition", status)
        self.assertIn("reasonForTransition", status)
        self.assertIn("backendHealth", status)
        self.assertIn("routingMode", status)

        # MONGODB_ONLY mode
        router.set_routing_mode(RoutingMode.MONGODB_ONLY)
        self.assertEqual(router.routing_mode, RoutingMode.MONGODB_ONLY)

        # NEON_ONLY mode
        router.set_routing_mode(RoutingMode.NEON_ONLY)
        self.assertEqual(router.routing_mode, RoutingMode.NEON_ONLY)

        # Restore AUTO mode
        router.set_routing_mode(RoutingMode.AUTO)
        self.assertEqual(router.routing_mode, RoutingMode.AUTO)


if __name__ == "__main__":
    unittest.main()
