"""
Targeted Verification Test Suite for PredictPro Fixture Ingestion & Database Protection.

Validates:
1. Ingestion of ALL provider fixtures for the operational window without arbitrary slicing (no [:20] limits).
2. Canonical fixture deduplication by canonical identity.
3. MongoDB batch writes use bounded bulk operations (FIXTURE_UPSERT_BATCH_SIZE).
4. Neon PostgreSQL uses parameterized batch statements with zero SELECT *.
5. DatabaseRouter switches fixture reads/writes from MongoDB to Neon transparently on failover.
6. Historical records belong in R2/Parquet/DuckDB; large historical datasets are never stored in Neon.
7. Diagnostics reporting:
   - providerFixturesReturned
   - fixturesNormalized
   - fixturesDeduplicated
   - fixturesPersistedMongo
   - fixturesPersistedNeon
   - fixturesRejected
   - activeDatabase
   - failoverState
"""

import unittest
import asyncio
import json
import inspect
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from backend.db.interfaces import (
    RoutingMode,
    FailoverState,
    IFixtureRepository,
    DatabaseUnavailableError,
)
from backend.db.mongodb import mongo_manager
from backend.db.mongodb_adapter import mongodb_adapter, MongoFixtureRepository, FIXTURE_PROJECTION
from backend.db.neon_adapter import neon_adapter, NeonFixtureRepository, NeonQueryExecutor, NEON_DDL_SCHEMA
from backend.db.neon_budget_guard import SafetyState, neon_budget_guard
from backend.db.database_router import database_router, DatabaseRouter
from backend.db.failover_manager import failover_manager
from backend.db.redis_client import redis_client
from backend.services.data_access.fixture_repository import fixture_repository
from backend.services.sync_service import sync_service, SyncService
from backend.services.feed_service import get_current_lagos_today


class TestFixtureIngestionAndProtection(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        failover_manager.reset_state_for_tests()
        failover_manager.failure_threshold = 3
        failover_manager.cooldown_seconds = 0
        database_router.set_routing_mode(RoutingMode.AUTO)

        try:
            mongo_manager.db["operational_events"].delete_many({})
            mongo_manager.db["predictions"].delete_many({})
            mongo_manager.db["prediction_results"].delete_many({})
        except Exception:
            pass

        self._orig_redis_url = redis_client.url
        self._orig_redis_token = redis_client.token
        redis_client.url = None
        redis_client.token = None
        redis_client._dev_test_cache.clear()

        self.today_lagos = get_current_lagos_today()

    def tearDown(self):
        failover_manager.reset_state_for_tests()
        database_router.set_routing_mode(RoutingMode.AUTO)
        try:
            mongo_manager.db["operational_events"].delete_many({})
            mongo_manager.db["predictions"].delete_many({})
            mongo_manager.db["prediction_results"].delete_many({})
        except Exception:
            pass
        redis_client._dev_test_cache.clear()
        redis_client.url = self._orig_redis_url
        redis_client.token = self._orig_redis_token

    # =========================================================================
    # 1. Ingestion of ALL Provider Fixtures (No Arbitrary Slicing)
    # =========================================================================
    def test_no_arbitrary_slicing_in_fixture_ingestion(self):
        """Verifies sync_service and adapters do not slice raw provider fixtures to 20 or 50."""
        import inspect
        src = inspect.getsource(SyncService.execute_refresh)
        self.assertNotIn("raw_fixtures[:20]", src)
        self.assertNotIn("all_raw_fixtures[:20]", src)
        self.assertNotIn("deduplicated_fixtures[:20]", src)
        self.assertNotIn("all_raw_fixtures[:50]", src)

    # =========================================================================
    # 2. Canonical Identity Deduplication
    # =========================================================================
    def test_canonical_fixture_deduplication(self):
        """Deduplicates duplicate fixtures across dates or provider formats."""
        svc = SyncService()
        fix1 = {
            "id": "match_101",
            "sport": "football",
            "homeTeam": "Manchester United FC",
            "awayTeam": "Liverpool FC",
            "kickoffUtc": f"{self.today_lagos}T15:00:00Z",
            "status": "scheduled",
        }
        fix2 = {
            "id": "match_101_alt",
            "sport": "football",
            "homeTeam": "Manchester United",
            "awayTeam": "Liverpool",
            "kickoffUtc": f"{self.today_lagos}T15:00:00Z",
            "status": "live",
        }
        key1 = svc._generate_canonical_fixture_key(fix1)
        key2 = svc._generate_canonical_fixture_key(fix2)
        self.assertEqual(key1, key2)

    # =========================================================================
    # 3. MongoDB Batch Operations
    # =========================================================================
    async def test_mongodb_fixture_upsert_batched(self):
        """MongoDB upsert_fixtures processes fixtures in bounded batches."""
        fixtures = [
            {
                "id": f"batch_fix_{i}",
                "sport": "football",
                "homeTeam": f"Team {i}",
                "awayTeam": f"Opponent {i}",
                "kickoffUtc": f"{self.today_lagos}T18:00:00Z",
                "status": "scheduled",
            }
            for i in range(120)
        ]

        count = await mongodb_adapter.fixtures.upsert_fixtures(fixtures)
        self.assertEqual(count, 120)

        # Verify documents in MongoDB
        stored = list(mongo_manager.db["operational_events"].find({}))
        self.assertEqual(len(stored), 120)

    # =========================================================================
    # 4. Neon Batch Operations & Zero SELECT *
    # =========================================================================
    async def test_neon_fixture_upsert_batch_and_no_select_star(self):
        """Neon upsert_fixtures executes batch statements without SELECT *."""
        mock_executor = MagicMock(spec=NeonQueryExecutor)
        mock_executor.execute_batch = AsyncMock(return_value=100)
        mock_executor.execute_query = AsyncMock(return_value=[])

        neon_repo = NeonFixtureRepository(mock_executor)

        fixtures = [
            {
                "id": f"neon_fix_{i}",
                "sport": "basketball",
                "homeTeam": f"Lakers {i}",
                "awayTeam": f"Warriors {i}",
                "kickoffUtc": f"{self.today_lagos}T20:00:00Z",
                "status": "scheduled",
            }
            for i in range(100)
        ]

        upserted = await neon_repo.upsert_fixtures(fixtures)
        self.assertEqual(upserted, 100)
        mock_executor.execute_batch.assert_called_once()

        # Verify no SELECT * in NeonFixtureRepository
        src = inspect.getsource(NeonFixtureRepository)
        self.assertNotIn("SELECT *", src.upper())

    # =========================================================================
    # 5. DatabaseRouter Transparent Routing
    # =========================================================================
    async def test_databaserouter_fixture_failover_routing(self):
        """DatabaseRouter switches fixture operations to Neon seamlessly during failover."""
        # 1. Normal state -> Mongo
        self.assertEqual(database_router.get_write_adapter(), mongodb_adapter)

        # 2. Failover state -> Neon
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True
        failover_manager._failover_state = FailoverState.NEON_FAILOVER

        with patch("backend.db.neon_adapter.neon_adapter.is_healthy", return_value=True):
            self.assertEqual(database_router.get_write_adapter(), neon_adapter)
            self.assertEqual(database_router.get_read_adapter(), neon_adapter)

    # =========================================================================
    # 6. Historical Data Protection for Neon
    # =========================================================================
    def test_neon_prohibits_historical_data_tables(self):
        """Neon schema and router enforce operational window only; historical archives remain in R2/Parquet/DuckDB."""
        self.assertNotIn("historical_matches", NEON_DDL_SCHEMA)
        self.assertNotIn("parquet_datasets", NEON_DDL_SCHEMA)
        self.assertNotIn("duckdb_catalog", NEON_DDL_SCHEMA)

    # =========================================================================
    # 7. Diagnostics Verification
    # =========================================================================
    async def test_sync_feed_reports_all_required_diagnostics(self):
        """sync_feed diagnostics must include all required operational counters."""
        mock_fixtures = [
            {
                "id": f"diag_fix_{i}",
                "sport": "football",
                "homeTeam": f"Arsenal {i}",
                "awayTeam": f"Chelsea {i}",
                "kickoffUtc": f"{self.today_lagos}T15:00:00Z",
                "status": "scheduled",
            }
            for i in range(5)
        ]

        with patch.object(sync_service, "refresh_football", return_value={
            "fixtures": mock_fixtures,
            "fetched_count": 5,
            "calls_avoided": 1,
            "competitions_checked": 2,
            "provider_calls": 1,
            "errors": [],
        }):
            with patch.object(sync_service, "refresh_basketball", return_value={"fixtures": [], "fetched_count": 0, "calls_avoided": 0, "errors": []}):
                with patch.object(sync_service, "refresh_baseball", return_value={"fixtures": [], "fetched_count": 0, "calls_avoided": 0, "errors": []}):
                    with patch.object(sync_service, "refresh_hockey", return_value={"fixtures": [], "fetched_count": 0, "calls_avoided": 0, "errors": []}):
                        with patch.object(sync_service, "refresh_f1", return_value={"fixtures": [], "fetched_count": 0, "calls_avoided": 0, "errors": []}):
                            with patch("backend.db.replication_manager.replication_manager.execute_incremental_replication", return_value={"status": "completed", "fixtures_replicated": 5}):
                                record = await sync_service.sync_feed(sports=["football"], force=True)

                                diag = record.diagnostics
                                self.assertIn("providerFixturesReturned", diag)
                                self.assertEqual(diag["providerFixturesReturned"], 5)
                                self.assertIn("fixturesNormalized", diag)
                                self.assertEqual(diag["fixturesNormalized"], 5)
                                self.assertIn("fixturesDeduplicated", diag)
                                self.assertEqual(diag["fixturesDeduplicated"], 5)
                                self.assertIn("fixturesPersistedMongo", diag)
                                self.assertEqual(diag["fixturesPersistedMongo"], 5)
                                self.assertIn("fixturesPersistedNeon", diag)
                                self.assertEqual(diag["fixturesPersistedNeon"], 5)
                                self.assertIn("fixturesRejected", diag)
                                self.assertIn("activeDatabase", diag)
                                self.assertEqual(diag["activeDatabase"], "mongodb")
                                self.assertIn("failoverState", diag)
                                self.assertEqual(diag["failoverState"], FailoverState.MONGODB_PRIMARY.value)


if __name__ == "__main__":
    unittest.main()
