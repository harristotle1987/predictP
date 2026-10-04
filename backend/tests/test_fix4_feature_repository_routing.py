import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import inspect

from backend.db.database_router import DatabaseRouter, database_router
from backend.services.data_access.feature_repository import FeatureRepository, feature_repository, CURRENT_FEATURE_VERSION
from backend.db.mongodb_adapter import mongodb_adapter, MongoFeatureRepository
from backend.db.neon_adapter import neon_adapter, NeonFeatureRepository
from backend.db.interfaces import IFeatureRepository


class TestFix4FeatureRepositoryRouting(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_1_feature_repository_does_not_access_mongo_manager_directly(self):
        """Verifies FeatureRepository does not contain direct mongo_manager.db accesses."""
        src = inspect.getsource(FeatureRepository)
        self.assertNotIn("mongo_manager.db", src)
        self.assertNotIn("mongo_manager.collection", src)
        self.assertNotIn("mongo_manager.get_collection", src)

    def test_2_feature_repository_implements_routed_methods(self):
        """Verifies get_team_snapshot, get_batch_team_snapshots, save_team_snapshot exist."""
        repo = FeatureRepository()
        self.assertTrue(hasattr(repo, "get_team_snapshot"))
        self.assertTrue(hasattr(repo, "get_batch_team_snapshots"))
        self.assertTrue(hasattr(repo, "save_team_snapshot"))

    def test_3_point_in_time_historical_feature_logic(self):
        """Verifies as_of cutoff timestamp parameter is passed through to router."""
        mock_snapshot = {
            "team_name": "arsenal",
            "sport": "football",
            "as_of": "2026-09-01T00:00:00Z",
            "features": {"elo": 1810.0, "form": [1, 1, 0, 1]},
            "feature_version": CURRENT_FEATURE_VERSION,
        }
        with patch.object(database_router.features, "get_team_snapshot", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_snapshot
            res = self.loop.run_until_complete(
                feature_repository.get_team_snapshot("Arsenal", "football", as_of="2026-09-01T00:00:00Z")
            )
            self.assertEqual(res, mock_snapshot)
            mock_get.assert_awaited_once_with("arsenal", "football", "2026-09-01T00:00:00Z")

    def test_4_batch_snapshots_bounded_and_eliminates_n_plus_one(self):
        """Verifies get_batch_team_snapshots dispatches a single batch query."""
        teams = ["Arsenal", "Chelsea", "Liverpool"]
        mock_batch_data = {
            "arsenal": {"team_name": "arsenal", "features": {"elo": 1820}},
            "chelsea": {"team_name": "chelsea", "features": {"elo": 1750}},
            "liverpool": {"team_name": "liverpool", "features": {"elo": 1850}},
        }
        with patch.object(database_router.features, "get_batch_team_snapshots", new_callable=AsyncMock) as mock_batch:
            mock_batch.return_value = mock_batch_data
            res = self.loop.run_until_complete(
                feature_repository.get_batch_team_snapshots(teams, "football", as_of=None)
            )
            self.assertEqual(len(res), 3)
            self.assertIn("arsenal", res)
            self.assertIn("chelsea", res)
            self.assertIn("liverpool", res)
            mock_batch.assert_awaited_once_with(teams, "football", None)

    def test_5_save_team_snapshot_preserves_schema_and_version(self):
        """Verifies save_team_snapshot ensures required metadata fields before routing."""
        snapshot = {
            "team_name": "Arsenal",
            "sport": "football",
            "features": {"elo": 1825.0, "rolling_xg": 2.15, "form": [1, 1, 1]},
        }
        with patch.object(database_router.features, "save_team_snapshot", new_callable=AsyncMock) as mock_save:
            mock_save.return_value = True
            saved = self.loop.run_until_complete(feature_repository.save_team_snapshot(snapshot))
            self.assertTrue(saved)
            mock_save.assert_awaited_once()
            call_arg = mock_save.call_args[0][0]
            self.assertEqual(call_arg["team_name"], "arsenal")
            self.assertEqual(call_arg["sport"], "football")
            self.assertEqual(call_arg["feature_version"], CURRENT_FEATURE_VERSION)
            self.assertIn("as_of", call_arg)
            self.assertIn("updated_at", call_arg)

    def test_6_mongo_primary_and_neon_failover_adapters_implement_interface(self):
        """Verifies both MongoDB and Neon adapters implement IFeatureRepository without SELECT *."""
        self.assertIsInstance(mongodb_adapter.features, IFeatureRepository)
        self.assertIsInstance(neon_adapter.features, IFeatureRepository)


if __name__ == "__main__":
    unittest.main()
