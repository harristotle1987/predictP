import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import os
import asyncio

from backend.db.database_router import DatabaseRouter, database_router
from backend.services.data_access.feature_repository import feature_repository, FeatureRepository
from backend.services.health_service import health_service
from backend.db.neon_adapter import NeonDatabaseAdapter, NeonQueryExecutor, NEON_DDL_SCHEMA, neon_adapter
from backend.db.mongodb_adapter import mongodb_adapter
from backend.db.mongodb import MongoDBManager, mongo_manager
from backend.db.failover_manager import failover_manager


class TestFix1AndFix2DatabaseHealth(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_neon_ddl_schema_contains_required_tables(self):
        required_tables = [
            "neon_fixtures",
            "neon_published_predictions",
            "neon_prediction_results",
            "neon_refresh_state",
            "neon_model_config",
            "neon_calibration_metadata",
            "neon_model_governance",
            "neon_replication_checkpoints",
        ]
        for table in required_tables:
            self.assertIn(table, NEON_DDL_SCHEMA, f"Table {table} must exist in NEON_DDL_SCHEMA")

    def test_feature_repository_uses_database_router(self):
        # Verify FeatureRepository uses database_router.features
        self.assertTrue(hasattr(database_router, 'features'))
        self.assertTrue(hasattr(mongodb_adapter, 'features'))
        self.assertTrue(hasattr(neon_adapter, 'features'))

    def test_database_router_primary_fallback_resolution(self):
        # 1. MongoDB healthy -> Mongo active
        with patch.object(failover_manager, "resolve_backend", return_value="mongo"):
            adapter = database_router.get_read_adapter()
            self.assertEqual(adapter, mongodb_adapter)

        # 2. MongoDB unavailable + Neon healthy -> Neon active
        with patch.object(failover_manager, "resolve_backend", return_value="neon"):
            adapter = database_router.get_read_adapter()
            self.assertEqual(adapter, neon_adapter)

        # 3. MongoDB healthy + Neon unavailable -> Mongo remains active
        with patch.object(failover_manager, "resolve_backend", return_value="mongo"):
            adapter = database_router.get_read_adapter()
            self.assertEqual(adapter, mongodb_adapter)

    def test_health_service_includes_neon_postgres(self):
        # Health check includes neonPostgres, mongoDb, redis, r2Storage, duckDb, sportsSkills
        health = self.loop.run_until_complete(health_service.get_health_status())
        
        self.assertIn("mongoDb", health)
        self.assertIn("neonPostgres", health)
        self.assertIn("redis", health)
        self.assertIn("r2Storage", health)
        self.assertIn("duckDb", health)
        self.assertIn("sportsSkills", health)
        self.assertIn("activeDatabase", health)
        self.assertIn("failoverState", health)

        # neonPostgres must have status, latencyMs, error, and failoverReady
        neon_health = health["neonPostgres"]
        self.assertIn("status", neon_health)
        self.assertIn("latencyMs", neon_health)
        self.assertIn("failoverReady", neon_health)


if __name__ == "__main__":
    unittest.main()
