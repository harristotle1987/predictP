"""
Unit tests for FIX 1 — RESTORE THE PRIMARY/FALLBACK DATABASE PATH.

Covers:
1. MongoDB healthy -> Mongo active
2. MongoDB unavailable + Neon healthy -> Neon active
3. MongoDB healthy + Neon unavailable -> Mongo remains active
4. MongoDB unavailable + Neon unavailable -> fail closed
5. FeatureRepository uses DatabaseRouter
6. Neon connection uses NEON_DATABASE_URL
7. No HTTP /sql Neon implementation remains
8. No password-as-Bearer authentication remains
"""

import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import inspect

from backend.db.interfaces import (
    RoutingMode,
    FailoverState,
    DatabaseUnavailableError,
    FailoverNotPermittedError,
    IFeatureRepository,
)
from backend.db.database_router import DatabaseRouter, database_router
from backend.db.mongodb_adapter import MongoDatabaseAdapter, mongodb_adapter
from backend.db.neon_adapter import NeonDatabaseAdapter, NeonQueryExecutor, neon_adapter
from backend.db.failover_manager import FailoverManager, failover_manager
from backend.services.data_access.feature_repository import feature_repository, FeatureRepository
from backend.services.health_service import health_service


class TestFix1DatabaseRoutingAndFailover(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Reset failover manager state
        failover_manager._active_backend = "mongodb"
        failover_manager._failover_active = False
        failover_manager._failover_state = FailoverState.MONGODB_PRIMARY
        failover_manager._consecutive_mongo_failures = 0
        failover_manager._consecutive_mongo_successes = 0
        failover_manager._last_transition_time = 0.0
        database_router.set_routing_mode(RoutingMode.AUTO)

    # 1. MongoDB healthy -> Mongo active
    def test_1_mongodb_healthy_mongo_active(self):
        with patch.object(mongodb_adapter, "is_healthy", return_value=True), \
             patch.object(neon_adapter, "is_healthy", return_value=True):
            resolved = failover_manager.resolve_backend("AUTO")
            self.assertEqual(resolved, "mongodb")
            adapter = database_router.get_read_adapter()
            self.assertEqual(adapter.backend_name, "mongodb")

    # 2. MongoDB unavailable + Neon healthy -> Neon active
    def test_2_mongodb_unavailable_neon_healthy_neon_active(self):
        with patch.object(mongodb_adapter, "is_healthy", return_value=False), \
             patch.object(neon_adapter, "is_healthy", return_value=True), \
             patch.object(neon_adapter, "check_connection", return_value={"status": "connected"}), \
             patch.object(neon_adapter, "check_schema_compatibility", return_value=True), \
             patch("backend.db.replication_manager.replication_manager.get_status", return_value={"lastSummary": {"replication_lag_seconds": 0.0}}):
            
            # Simulate failure threshold reaching trigger
            failover_manager._consecutive_mongo_failures = failover_manager.failure_threshold - 1
            failover_res = failover_manager.evaluate_and_execute_failover("MongoDB test failure")
            self.assertEqual(failover_res.get("status"), "failover_executed")
            self.assertEqual(failover_manager.active_backend, "neon")

            resolved = failover_manager.resolve_backend("AUTO")
            self.assertEqual(resolved, "neon")
            adapter = database_router.get_read_adapter()
            self.assertEqual(adapter.backend_name, "neon")

    # 3. MongoDB healthy + Neon unavailable -> Mongo remains active
    def test_3_mongodb_healthy_neon_unavailable_mongo_remains_active(self):
        with patch.object(mongodb_adapter, "is_healthy", return_value=True), \
             patch.object(neon_adapter, "is_healthy", return_value=False):
            resolved = failover_manager.resolve_backend("AUTO")
            self.assertEqual(resolved, "mongodb")
            self.assertEqual(failover_manager.active_backend, "mongodb")
            adapter = database_router.get_write_adapter()
            self.assertEqual(adapter.backend_name, "mongodb")

    # 4. MongoDB unavailable + Neon unavailable -> fail closed
    def test_4_both_unavailable_fail_closed(self):
        with patch.object(mongodb_adapter, "is_healthy", return_value=False), \
             patch.object(neon_adapter, "is_healthy", return_value=False), \
             patch.object(neon_adapter, "check_connection", return_value={"status": "disconnected"}):
            
            failover_manager._consecutive_mongo_failures = 0
            with self.assertRaises(DatabaseUnavailableError):
                failover_manager.resolve_backend("AUTO")

    # 5. FeatureRepository uses DatabaseRouter
    async def test_5_feature_repository_uses_database_router(self):
        mock_snapshot = {
            "team_name": "Arsenal",
            "sport": "football",
            "features": {"elo": 1820.5, "xG": 2.1},
            "as_of": "2026-10-01T12:00:00Z",
        }

        # Verify FeatureRepository does not import or call mongo_manager.db directly
        src = inspect.getsource(FeatureRepository)
        self.assertNotIn("mongo_manager.db", src)
        self.assertIn("database_router.features", src)

        with patch.object(database_router.features, "get_team_snapshot", new_callable=AsyncMock) as mock_get, \
             patch.object(database_router.features, "save_team_snapshot", new_callable=AsyncMock) as mock_save, \
             patch.object(database_router.features, "get_batch_team_snapshots", new_callable=AsyncMock) as mock_batch:
            
            mock_get.return_value = mock_snapshot
            mock_save.return_value = True
            mock_batch.return_value = {"arsenal": mock_snapshot}

            # get_team_snapshot
            res = await feature_repository.get_team_snapshot("Arsenal", "football")
            self.assertEqual(res, mock_snapshot)
            mock_get.assert_awaited_once_with("arsenal", "football", None)

            # save_team_snapshot
            saved = await feature_repository.save_team_snapshot(mock_snapshot)
            self.assertTrue(saved)
            mock_save.assert_awaited_once()

            # get_batch_team_snapshots
            batch_res = await feature_repository.get_batch_team_snapshots(["Arsenal"], "football")
            self.assertEqual(batch_res, {"arsenal": mock_snapshot})
            mock_batch.assert_awaited_once_with(["Arsenal"], "football", None)

    # 6. Neon connection uses NEON_DATABASE_URL
    def test_6_neon_connection_uses_neon_database_url(self):
        executor = NeonQueryExecutor()
        test_url = "postgresql://neon_user:neon_pass@ep-cool-cloud.neon.tech/neondb?sslmode=require"
        with patch.dict(os.environ, {"NEON_DATABASE_URL": test_url}):
            executor._setup()
            self.assertTrue(executor.is_configured())
            self.assertEqual(executor._url, test_url)

    # 7. No HTTP /sql Neon implementation remains
    def test_7_no_http_sql_neon_implementation_remains(self):
        import backend.db.neon_adapter as neon_mod
        src = inspect.getsource(neon_mod)
        # Check that no HTTP client or /sql endpoint execution is performed
        self.assertNotIn("httpx.AsyncClient", src)
        self.assertNotIn("requests.post", src)
        self.assertNotIn("requests.get", src)
        self.assertNotIn("/v1/sql", src)
        self.assertIn("ConnectionPool", src)
        self.assertIn("psycopg", src)

    # 8. No password-as-Bearer authentication remains
    def test_8_no_password_as_bearer_authentication_remains(self):
        import backend.db.neon_adapter as neon_mod
        src = inspect.getsource(neon_mod)
        self.assertNotIn('"Authorization": f"Bearer', src)
        self.assertNotIn('"Authorization": "Bearer', src)
        self.assertNotIn("'Authorization': f'Bearer", src)
        self.assertNotIn("'Authorization': 'Bearer", src)

    # Health API includes mongoDb, neonPostgres, activeDatabase, failoverState
    async def test_health_api_response_structure(self):
        with patch.object(health_service, "get_health_status", wraps=health_service.get_health_status):
            status = await health_service.get_health_status()
            self.assertIn("mongoDb", status)
            self.assertIn("neonPostgres", status)
            self.assertIn("activeDatabase", status)
            self.assertIn("failoverState", status)
            self.assertIn("redis", status)
            self.assertIn("r2Storage", status)
            self.assertIn("duckDb", status)
            self.assertIn("sportsSkills", status)

            self.assertIn("status", status["mongoDb"])
            self.assertIn("latencyMs", status["mongoDb"])
            self.assertIn("status", status["neonPostgres"])
            self.assertIn("latencyMs", status["neonPostgres"])
            self.assertIn("failoverReady", status["neonPostgres"])


if __name__ == "__main__":
    unittest.main()
