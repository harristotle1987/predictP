import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

from backend.services.feed_service import FeedService, feed_service
from backend.services.sync_service import SyncService
from backend.db.database_router import DatabaseRouter


class TestFix6DatabasePersistenceAuthoritative(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sample_predictions = [
            {
                "id": f"pred_{i}",
                "fixture_id": f"fix_{i}",
                "sport": "football",
                "league": "Premier League",
                "validationStatus": "validated",
                "validatedMarkets": ["home_win"],
                "calibratedPercentage": 0.75 - (i * 0.01),
                "kickoffUtc": "2026-10-02T15:00:00Z",
                "homeTeam": f"Home {i}",
                "awayTeam": f"Away {i}",
            }
            for i in range(5)
        ]

    async def test_feed_service_redis_working(self):
        """1. FeedService reads from Redis cache when available."""
        mock_redis = AsyncMock()
        mock_redis.get_json = AsyncMock(return_value=self.sample_predictions)

        with patch("backend.services.feed_service.redis_client", mock_redis):
            service = FeedService()
            feed = await service.get_feed(date="2026-10-02")
            self.assertEqual(len(feed), 5)
            mock_redis.get_json.assert_called_with("predictpro:feed:2026-10-02")

    async def test_feed_service_redis_down_mongo_working(self):
        """2. FeedService falls back to DatabaseRouter (Mongo primary) when Redis fails."""
        mock_redis = AsyncMock()
        mock_redis.get_json = AsyncMock(side_effect=Exception("Redis connection refused"))

        mock_router = MagicMock()
        mock_router.predictions.get_daily_feed = AsyncMock(return_value=self.sample_predictions)
        mock_router.fixtures.get_by_ids = AsyncMock(return_value=[])

        with patch("backend.services.feed_service.redis_client", mock_redis), \
             patch("backend.services.feed_service.database_router", mock_router):
            service = FeedService()
            feed = await service.get_feed(date="2026-10-02")
            self.assertEqual(len(feed), 5)
            mock_router.predictions.get_daily_feed.assert_called_once()

    async def test_feed_service_filters_unvalidated(self):
        """3. FeedService excludes unvalidated or empty validatedMarkets entries."""
        raw_preds = [
            {
                "id": "valid_1",
                "fixture_id": "f1",
                "sport": "football",
                "validationStatus": "validated",
                "validatedMarkets": ["home_win"],
                "kickoffUtc": "2026-10-02T15:00:00Z",
            },
            {
                "id": "invalid_status",
                "fixture_id": "f2",
                "sport": "football",
                "validationStatus": "rejected",
                "validatedMarkets": ["home_win"],
                "kickoffUtc": "2026-10-02T15:00:00Z",
            },
            {
                "id": "empty_markets",
                "fixture_id": "f3",
                "sport": "football",
                "validationStatus": "validated",
                "validatedMarkets": [],
                "kickoffUtc": "2026-10-02T15:00:00Z",
            },
        ]

        mock_redis = AsyncMock()
        mock_redis.get_json = AsyncMock(return_value=raw_preds)

        with patch("backend.services.feed_service.redis_client", mock_redis):
            service = FeedService()
            feed = await service.get_feed(date="2026-10-02")
            self.assertEqual(len(feed), 1)
            self.assertEqual(feed[0]["id"], "valid_1")

    async def test_feed_service_max_limit_20(self):
        """4. FeedService caps returned predictions at maximum 20."""
        large_preds = [
            {
                "id": f"pred_{i}",
                "fixture_id": f"fix_{i}",
                "sport": "football",
                "validationStatus": "validated",
                "validatedMarkets": ["home_win"],
                "calibratedPercentage": 0.8,
                "kickoffUtc": "2026-10-02T15:00:00Z",
            }
            for i in range(30)
        ]

        mock_redis = AsyncMock()
        mock_redis.get_json = AsyncMock(return_value=large_preds)

        with patch("backend.services.feed_service.redis_client", mock_redis):
            service = FeedService()
            feed = await service.get_feed(date="2026-10-02", limit=50)
            self.assertEqual(len(feed), 20)

    async def test_sync_publication_mongo_and_redis_working(self):
        """5. Mongo + Redis working: databasePublicationStatus = published, predictions published > 0."""
        sync = SyncService()

        mock_router = MagicMock()
        mock_router.get_active_database_name.return_value = "mongodb"
        mock_router.fixtures.upsert_fixtures = AsyncMock(return_value=5)
        mock_router.predictions.save_predictions = AsyncMock(return_value=5)

        mock_redis = AsyncMock()
        mock_redis.set_json = AsyncMock(return_value=True)

        pipeline_mock = {
            "publishedFeed": self.sample_predictions,
            "allResults": [],
            "diagnostics": {
                "fixturesEligible": 5,
                "fixturesModelExecuted": 5,
                "ensembleSuccessful": 5,
                "validatedFixtures": 5,
            },
        }

        with patch("backend.services.sync_service.database_router", mock_router), \
             patch("backend.services.sync_service.redis_client", mock_redis), \
             patch("backend.services.sync_service.execute_prediction_pipeline", return_value=pipeline_mock), \
             patch.object(sync, "refresh_football", AsyncMock(return_value={"fixtures": [], "errors": [], "fetched": 5})):

            record = await sync.execute_refresh(sports=["football"], force=True)

            self.assertEqual(record.predictions_published, 5)
            self.assertEqual(record.diagnostics.get("databasePublicationStatus"), "published")
            self.assertEqual(record.diagnostics.get("activeDatabase"), "mongodb")

    async def test_sync_publication_mongo_working_redis_down(self):
        """6. Mongo working + Redis down: published_predictions_count stays 5, status remains published."""
        sync = SyncService()

        mock_router = MagicMock()
        mock_router.get_active_database_name.return_value = "mongodb"
        mock_router.fixtures.upsert_fixtures = AsyncMock(return_value=5)
        mock_router.predictions.save_predictions = AsyncMock(return_value=5)

        mock_redis = AsyncMock()
        mock_redis.set_json = AsyncMock(side_effect=Exception("Redis connection refused"))

        pipeline_mock = {
            "publishedFeed": self.sample_predictions,
            "allResults": [],
            "diagnostics": {
                "fixturesEligible": 5,
                "fixturesModelExecuted": 5,
                "ensembleSuccessful": 5,
                "validatedFixtures": 5,
            },
        }

        with patch("backend.services.sync_service.database_router", mock_router), \
             patch("backend.services.sync_service.redis_client", mock_redis), \
             patch("backend.services.sync_service.execute_prediction_pipeline", return_value=pipeline_mock), \
             patch.object(sync, "refresh_football", AsyncMock(return_value={"fixtures": [], "errors": [], "fetched": 5})):

            record = await sync.execute_refresh(sports=["football"], force=True)

            # Crucial requirement: published_predictions MUST remain 5, NOT set to 0
            self.assertEqual(record.predictions_published, 5)
            self.assertEqual(record.diagnostics.get("databasePublicationStatus"), "published")

    async def test_sync_publication_mongo_down_neon_working(self):
        """7. Mongo down + Neon working: DatabaseRouter uses Neon, publication succeeds."""
        sync = SyncService()

        mock_router = MagicMock()
        mock_router.get_active_database_name.return_value = "neon"
        mock_router.fixtures.upsert_fixtures = AsyncMock(return_value=5)
        mock_router.predictions.save_predictions = AsyncMock(return_value=5)

        mock_redis = AsyncMock()
        mock_redis.set_json = AsyncMock(return_value=True)

        pipeline_mock = {
            "publishedFeed": self.sample_predictions,
            "allResults": [],
            "diagnostics": {
                "fixturesEligible": 5,
                "fixturesModelExecuted": 5,
                "ensembleSuccessful": 5,
                "validatedFixtures": 5,
            },
        }

        with patch("backend.services.sync_service.database_router", mock_router), \
             patch("backend.services.sync_service.redis_client", mock_redis), \
             patch("backend.services.sync_service.execute_prediction_pipeline", return_value=pipeline_mock), \
             patch.object(sync, "refresh_football", AsyncMock(return_value={"fixtures": [], "errors": [], "fetched": 5})):

            record = await sync.execute_refresh(sports=["football"], force=True)

            self.assertEqual(record.predictions_published, 5)
            self.assertEqual(record.diagnostics.get("databasePublicationStatus"), "published")
            self.assertEqual(record.diagnostics.get("activeDatabase"), "neon")

    async def test_sync_publication_mongo_down_redis_down_neon_working(self):
        """8. Mongo down + Redis down + Neon working: activeDatabase = neon, published_predictions = 5."""
        sync = SyncService()

        mock_router = MagicMock()
        mock_router.get_active_database_name.return_value = "neon"
        mock_router.fixtures.upsert_fixtures = AsyncMock(return_value=5)
        mock_router.predictions.save_predictions = AsyncMock(return_value=5)

        mock_redis = AsyncMock()
        mock_redis.set_json = AsyncMock(side_effect=Exception("Redis connection timeout"))

        pipeline_mock = {
            "publishedFeed": self.sample_predictions,
            "allResults": [],
            "diagnostics": {
                "fixturesEligible": 5,
                "fixturesModelExecuted": 5,
                "ensembleSuccessful": 5,
                "validatedFixtures": 5,
            },
        }

        with patch("backend.services.sync_service.database_router", mock_router), \
             patch("backend.services.sync_service.redis_client", mock_redis), \
             patch("backend.services.sync_service.execute_prediction_pipeline", return_value=pipeline_mock), \
             patch.object(sync, "refresh_football", AsyncMock(return_value={"fixtures": [], "errors": [], "fetched": 5})):

            record = await sync.execute_refresh(sports=["football"], force=True)

            self.assertEqual(record.predictions_published, 5)
            self.assertEqual(record.diagnostics.get("databasePublicationStatus"), "published")
            self.assertEqual(record.diagnostics.get("activeDatabase"), "neon")

    async def test_sync_publication_mongo_down_neon_down(self):
        """9. Mongo down + Neon down: databasePublicationStatus = failed."""
        sync = SyncService()

        mock_router = MagicMock()
        mock_router.get_active_database_name.return_value = "none"
        mock_router.fixtures.upsert_fixtures = AsyncMock(return_value=0)
        mock_router.predictions.save_predictions = AsyncMock(side_effect=Exception("All database backends failed"))

        mock_redis = AsyncMock()

        pipeline_mock = {
            "publishedFeed": self.sample_predictions,
            "allResults": [],
            "diagnostics": {
                "fixturesEligible": 5,
                "fixturesModelExecuted": 5,
                "ensembleSuccessful": 5,
                "validatedFixtures": 5,
            },
        }

        with patch("backend.services.sync_service.database_router", mock_router), \
             patch("backend.services.sync_service.redis_client", mock_redis), \
             patch("backend.services.sync_service.execute_prediction_pipeline", return_value=pipeline_mock), \
             patch.object(sync, "refresh_football", AsyncMock(return_value={"fixtures": [], "errors": [], "fetched": 5})):

            record = await sync.execute_refresh(sports=["football"], force=True)

            self.assertEqual(record.predictions_published, 0)
            self.assertEqual(record.diagnostics.get("databasePublicationStatus"), "failed")


if __name__ == "__main__":
    unittest.main()
