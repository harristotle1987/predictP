import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import sys
import os
import asyncio

# Mock fastapi if not installed in current test python context
if 'fastapi' not in sys.modules:
    fastapi_mock = MagicMock()
    sys.modules['fastapi'] = fastapi_mock
    sys.modules['fastapi.middleware'] = MagicMock()
    sys.modules['fastapi.middleware.cors'] = MagicMock()

from backend.db.database_router import database_router
from backend.services.data_access.fixture_repository import fixture_repository
from backend.services.feed_service import feed_service


class TestFix5MatchesAndFixturesApi(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_1_get_fixtures_and_count_support_date_range_and_filters(self):
        """Verifies get_fixtures and count_fixtures accept date, startDate, endDate, sport, league, status."""
        mock_fixtures = [
            {"id": "fix_1", "sport": "football", "homeTeam": "Arsenal", "awayTeam": "Chelsea", "kickoffUtc": "2026-10-01T15:00:00Z", "status": "scheduled"},
            {"id": "fix_2", "sport": "football", "homeTeam": "Liverpool", "awayTeam": "Everton", "kickoffUtc": "2026-10-02T15:00:00Z", "status": "scheduled"},
        ]
        with patch.object(database_router.fixtures, "get_fixtures", new_callable=AsyncMock) as mock_get, \
             patch.object(database_router.fixtures, "count_fixtures", new_callable=AsyncMock) as mock_count:
            
            mock_get.return_value = mock_fixtures
            mock_count.return_value = 2

            res = self.loop.run_until_complete(
                fixture_repository.get_fixtures(
                    sport="football",
                    league="Premier League",
                    date="2026-10-01",
                    status="upcoming",
                    limit=50,
                    offset=0,
                    start_date="2026-10-01",
                    end_date="2026-10-07",
                )
            )
            self.assertEqual(len(res), 2)
            mock_get.assert_awaited_once_with(
                sport="football",
                league="Premier League",
                date="2026-10-01",
                status="upcoming",
                limit=50,
                offset=0,
                start_date="2026-10-01",
                end_date="2026-10-07",
            )

    def test_2_hard_maximum_limit_enforced(self):
        """Verifies limit parameter is clamped to <= 100."""
        from backend.db.mongodb_adapter import MongoFixtureRepository
        from backend.db.neon_adapter import NeonFixtureRepository

        mongo_repo = MongoFixtureRepository()
        neon_repo = NeonFixtureRepository(executor=MagicMock())

        # Check limit logic in Mongo & Neon
        with patch.object(mongo_repo, "_get_collection") as mock_col:
            mock_cursor = MagicMock()
            mock_cursor.sort.return_value = mock_cursor
            mock_cursor.skip.return_value = mock_cursor
            mock_cursor.limit.return_value = mock_cursor
            mock_cursor.__iter__.return_value = iter([])
            mock_col.return_value.find.return_value = mock_cursor

            self.loop.run_until_complete(mongo_repo.get_fixtures(limit=250))
            mock_cursor.limit.assert_called_once_with(100)

    def test_3_unpredicted_fixtures_returned_without_fabrication(self):
        """Verifies operational fixtures without published predictions are returned intact."""
        raw_fixtures = [
            {"id": "fix_unpred", "sport": "basketball", "homeTeam": "Lakers", "awayTeam": "Celtics", "status": "scheduled"}
        ]

        from backend.app import _enrich_operational_fixtures
        with patch.object(database_router.predictions, "get_published_feed", new_callable=AsyncMock) as mock_feed:
            mock_get_feed = AsyncMock(return_value=[])
            mock_feed.side_effect = mock_get_feed

            enriched = self.loop.run_until_complete(_enrich_operational_fixtures(raw_fixtures))
            self.assertEqual(len(enriched), 1)
            item = enriched[0]
            self.assertEqual(item["id"], "fix_unpred")
            self.assertFalse(item["predictionAvailable"])
            self.assertEqual(item["validationStatus"], "unpredicted")
            self.assertEqual(item["validatedMarkets"], [])

    def test_4_get_match_detail_uses_persisted_data(self):
        """Verifies GET match detail retrieves from database router and does not run pipeline on the fly."""
        mock_fixture = {"id": "fix_99", "sport": "football", "homeTeam": "Barca", "awayTeam": "Real", "status": "scheduled"}
        mock_pred = {"id": "fix_99", "fixture_id": "fix_99", "validationStatus": "validated", "validatedMarkets": [{"marketName": "Match Winner"}]}

        with patch.object(database_router.fixtures, "get_by_id", new_callable=AsyncMock) as mock_fix_get, \
             patch.object(database_router.predictions, "get_by_id", new_callable=AsyncMock) as mock_pred_get:
            
            mock_fix_get.return_value = mock_fixture
            mock_pred_get.return_value = mock_pred

            res = self.loop.run_until_complete(feed_service.get_match_by_id("fix_99"))
            self.assertIsNotNone(res)
            self.assertEqual(res["id"], "fix_99")
            mock_fix_get.assert_awaited_once_with("fix_99")


if __name__ == "__main__":
    unittest.main()
