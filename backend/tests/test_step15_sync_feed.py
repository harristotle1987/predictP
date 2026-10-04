import unittest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone

from backend.db.mongodb import mongo_manager
from backend.services.sync_service import sync_service
from backend.services.feed_service import feed_service

class TestStep15SyncFeed(unittest.TestCase):
    def setUp(self):
        from backend.db.failover_manager import failover_manager
        from backend.db.database_router import database_router, RoutingMode
        failover_manager.reset_state_for_tests()
        database_router.set_routing_mode(RoutingMode.MONGODB_ONLY)
        # Clean collection states
        mongo_manager.operational_events.delete_many({})
        mongo_manager.predictions.delete_many({})
        mongo_manager.prediction_results.delete_many({})

    def tearDown(self):
        from backend.db.database_router import database_router, RoutingMode
        database_router.set_routing_mode(RoutingMode.NEON_ONLY)
        # Clean collection states
        mongo_manager.operational_events.delete_many({})
        mongo_manager.predictions.delete_many({})
        mongo_manager.prediction_results.delete_many({})

    @patch("backend.providers.football_adapter.football_adapter.fetch_fixtures")
    @patch("backend.providers.basketball_adapter.basketball_adapter.fetch_fixtures")
    @patch("backend.providers.baseball_adapter.baseball_adapter.fetch_fixtures")
    @patch("backend.providers.sports_skills_hockey_provider.sports_skills_hockey_provider.fetch_fixtures")
    @patch("backend.providers.sports_skills_f1_provider.sports_skills_f1_provider.fetch_fixtures")
    def test_sync_feed_reconciles_and_evaluates_accurately(
        self, mock_f1, mock_hk, mock_bb, mock_bk, mock_fb
    ):
        """Verify the dedicated Sync Feed workflow accurately updates scores and evaluates results."""
        from backend.services.feed_service import get_current_lagos_today
        today_lagos = get_current_lagos_today()
        kickoff_iso = f"{today_lagos}T15:00:00Z"

        # 1. Seed historical operational events in MongoDB
        mongo_manager.operational_events.insert_one({
            "id": "fb_123",
            "source_event_id": "123",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "status": "scheduled",
            "kickoffUtc": kickoff_iso,
            "scheduled_at": kickoff_iso,
            "currentScore": {"home": 0, "away": 0, "display": "0 - 0"},
        })

        # 2. Seed a published prediction for this operational event
        mongo_manager.predictions.insert_one({
            "id": "fb_123",
            "fixture_id": "fb_123",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": kickoff_iso,
            "scheduled_at": kickoff_iso,
            "market": "Win / Draw / Loss (1X2)",
            "selection": "Arsenal Win",
            "percentage": 75.0,
            "validationStatus": "validated",
            "validatedMarkets": [
                {
                    "id": "fb_123-m1",
                    "marketName": "Win / Draw / Loss (1X2)",
                    "selection": "Arsenal Win",
                    "probabilityPercentage": 75.0,
                }
            ],
            "modelVersion": "ELO + POISSON",
        })

        # 3. Mock provider adapters to return the updated completed score
        mock_fb.return_value = [
            {
                "id": "fb_123",
                "source_event_id": "123",
                "sport": "football",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": "2026-10-01T15:00:00Z",
                "scheduled_at": "2026-10-01T15:00:00Z",
                "status": "completed",
                "currentScore": {"home": 3, "away": 1, "display": "3 - 1"},
            }
        ]
        mock_bk.return_value = []
        mock_bb.return_value = []
        mock_hk.return_value = []
        mock_f1.return_value = []

        # 4. Execute the dedicated Sync Feed workflow
        import asyncio
        diagnostics = asyncio.run(sync_service.execute_sync_feed())

        # 5. Assertions
        self.assertEqual(diagnostics["completed_matches_updated"], 1)
        self.assertEqual(diagnostics["predictions_evaluated"], 1)
        self.assertEqual(diagnostics["hits"], 1)
        self.assertEqual(diagnostics["misses"], 0)

        # 6. Verify the operational_event was updated with the score in MongoDB
        updated_event = mongo_manager.operational_events.find_one({"id": "fb_123"})
        self.assertIsNotNone(updated_event)
        self.assertEqual(updated_event["status"], "completed")
        self.assertEqual(updated_event["currentScore"]["display"], "3 - 1")

        # 7. Verify the prediction_result record was correctly logged
        res_rec = mongo_manager.prediction_results.find_one({"prediction_id": "fb_123-m1"})
        self.assertIsNotNone(res_rec)
        self.assertEqual(res_rec["hit_or_miss"], "hit")
        self.assertEqual(res_rec["actual_outcome"], "Arsenal Win")

        # 8. Verify the evaluation metrics computed correctly
        metrics = diagnostics["metrics_updated"]
        self.assertEqual(metrics["global_accuracy"], 1.0)
        self.assertEqual(metrics["total_evaluated"], 1)

        # 9. Verify get_feed merges live/completed scores and statuses correctly (Rule 16)
        feed = asyncio.run(feed_service.get_feed(sport="football"))
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["status"], "completed")
        self.assertEqual(feed[0]["currentScore"]["display"], "3 - 1")

if __name__ == "__main__":
    unittest.main()
