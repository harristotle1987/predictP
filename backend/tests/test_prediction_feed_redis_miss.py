import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from backend.db.mongodb import mongo_manager
from backend.db.mongodb_adapter import PREDICTION_PROJECTION, MongoPredictionRepository
from backend.db.redis_client import redis_client
from backend.services.feed_service import (
    feed_service,
    get_current_lagos_today,
    get_lagos_date_str,
    LAGOS_TZ,
)
from backend.db.database_router import database_router


class TestPredictionFeedRedisMissAndProjection(unittest.IsolatedAsyncioTestCase):
    """
    Validates the complete PredictPro prediction-feed path on Redis miss,
    the PREDICTION_PROJECTION schema fields, the FeedService validation gates,
    the Lagos timezone date filtering, and the 20-prediction limit.
    """

    def setUp(self):
        from backend.db.failover_manager import failover_manager
        from backend.db.database_router import database_router, RoutingMode
        database_router.set_routing_mode(RoutingMode.MONGODB_ONLY)
        failover_manager.reset_state_for_tests()
        # Save and disable live remote redis url for isolated miss testing
        self._orig_redis_url = redis_client.url
        self._orig_redis_token = redis_client.token
        redis_client.url = None
        redis_client.token = None
        # Clear mock MongoDB collections via db access
        try:
            mongo_manager.db["operational_events"].delete_many({})
            mongo_manager.db["predictions"].delete_many({})
            mongo_manager.db["prediction_results"].delete_many({})
        except Exception:
            pass
        # Clear Redis cache
        redis_client._dev_test_cache.clear()
        self.today_lagos = get_current_lagos_today()
        now_lagos = datetime.now(LAGOS_TZ)
        self.future_date = (now_lagos + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        from backend.db.failover_manager import failover_manager
        from backend.db.database_router import database_router, RoutingMode
        database_router.set_routing_mode(RoutingMode.NEON_ONLY)
        failover_manager.reset_state_for_tests()
        try:
            mongo_manager.db["operational_events"].delete_many({})
            mongo_manager.db["predictions"].delete_many({})
            mongo_manager.db["prediction_results"].delete_many({})
        except Exception:
            pass
        redis_client._dev_test_cache.clear()
        redis_client.url = self._orig_redis_url
        redis_client.token = self._orig_redis_token

    def test_prediction_projection_has_all_required_fields(self):
        """Verify PREDICTION_PROJECTION contains all fields required by feed_service and React UI."""
        self.assertEqual(PREDICTION_PROJECTION.get("_id"), 0)

        required_fields = [
            "validatedMarkets",
            "fixture_id",
            "percentage",
            "selection",
            "market",
            "raw_probability",
            "calibrated_probability",
            "highestPercentagePrediction",
            "sportStats",
            "metadata",
            "modelMetadata",
            "training_window",
            "training_match_count",
            "feature_timestamp",
            "prediction_timestamp",
            "event_date_lagos",
        ]
        for field in required_fields:
            self.assertIn(
                field,
                PREDICTION_PROJECTION,
                f"Field '{field}' must be present in PREDICTION_PROJECTION",
            )
            self.assertEqual(PREDICTION_PROJECTION[field], 1)

    async def test_redis_miss_routes_to_mongodb_and_returns_complete_representation(self):
        """
        Verify the Redis-miss path:
        Redis miss -> MongoDB -> complete published prediction -> FeedService validation -> Lagos date filter -> UI
        """
        # Ensure Redis has no cached feed
        cached = await redis_client.get_json(f"predictpro:feed:{self.today_lagos}")
        self.assertIsNone(cached)

        # Seed complete validated prediction in MongoDB
        kickoff_utc = f"{self.today_lagos}T17:30:00Z"
        pred_doc = {
            "id": "pred_full_1",
            "fixture_id": "fix_full_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": kickoff_utc,
            "scheduled_at": kickoff_utc,
            "event_date_lagos": self.today_lagos,
            "status": "scheduled",
            "market": "Match Winner",
            "selection": "Arsenal",
            "percentage": 72.5,
            "raw_probability": 0.70,
            "calibrated_probability": 0.725,
            "highestPercentagePrediction": {
                "marketName": "Match Winner",
                "selection": "Arsenal",
                "percentage": 72.5,
            },
            "validatedMarkets": [
                {
                    "id": "fix_full_1-m1",
                    "marketName": "Match Winner",
                    "selection": "Arsenal",
                    "probabilityPercentage": 72.5,
                    "confidenceRating": "HIGH",
                    "sportSpecificCategory": "1X2",
                    "isValidated": True,
                }
            ],
            "sportStats": {
                "xGRecentHome": 2.1,
                "xGRecentAway": 1.1,
                "homeGoalsScoredAvg": 2.2,
                "awayGoalsScoredAvg": 1.0,
            },
            "validationStatus": "validated",
            "modelVersion": "ELO + POISSON",
            "modelMetadata": {"model_name": "ELO_POISSON_V1", "training_window": "5_seasons"},
            "metadata": {"model_name": "ELO_POISSON_V1"},
            "training_window": "5_seasons",
            "training_match_count": 50,
            "feature_timestamp": kickoff_utc,
            "prediction_timestamp": kickoff_utc,
            "isBestOfDay": True,
            "published": True,
        }
        mongo_manager.predictions.insert_one(pred_doc)

        # Seed corresponding operational event for live/score state
        mongo_manager.operational_events.insert_one({
            "id": "fix_full_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": kickoff_utc,
            "scheduled_at": kickoff_utc,
            "status": "live",
            "currentScore": {"home": 2, "away": 0, "display": "2 - 0"},
        })

        # Fetch feed through feed_service for today
        feed = await feed_service.get_feed(date=self.today_lagos)

        # Assertions
        self.assertEqual(len(feed), 1)
        item = feed[0]
        self.assertEqual(item["id"], "pred_full_1")
        self.assertEqual(item["fixture_id"], "fix_full_1")
        self.assertEqual(item["sport"], "football")
        self.assertEqual(item["league"], "Premier League")
        self.assertEqual(item["homeTeam"], "Arsenal")
        self.assertEqual(item["awayTeam"], "Chelsea")
        self.assertEqual(item["validationStatus"], "validated")
        self.assertIsNotNone(item.get("validatedMarkets"))
        self.assertEqual(len(item["validatedMarkets"]), 1)
        self.assertEqual(item["percentage"], 72.5)
        self.assertEqual(item["selection"], "Arsenal")
        self.assertEqual(item["market"], "Match Winner")
        self.assertEqual(item["highestPercentagePrediction"]["selection"], "Arsenal")
        self.assertIsNotNone(item.get("sportStats"))
        self.assertEqual(item["status"], "live")
        self.assertEqual(item["currentScore"]["display"], "2 - 0")

    async def test_feed_service_safety_gates_enforced(self):
        """
        Verify the two critical safety gates in FeedService:
        1. if m.get("validationStatus") != "validated": continue
        2. if not m.get("validatedMarkets"): continue
        Items failing either gate are never published.
        """
        kickoff_utc = f"{self.today_lagos}T19:00:00Z"

        # Candidate 1: abstained item (must be rejected)
        mongo_manager.predictions.insert_one({
            "id": "pred_abstained",
            "fixture_id": "fix_abstained",
            "sport": "football",
            "validationStatus": "abstained",
            "kickoffUtc": kickoff_utc,
            "validatedMarkets": [{"marketName": "1X2", "selection": "Home", "probabilityPercentage": 60.0}],
        })

        # Candidate 2: validated status but empty validatedMarkets (must be rejected)
        mongo_manager.predictions.insert_one({
            "id": "pred_empty_markets",
            "fixture_id": "fix_empty_markets",
            "sport": "football",
            "validationStatus": "validated",
            "kickoffUtc": kickoff_utc,
            "validatedMarkets": [],
        })

        # Candidate 3: validated status but missing validatedMarkets (must be rejected)
        mongo_manager.predictions.insert_one({
            "id": "pred_missing_markets",
            "fixture_id": "fix_missing_markets",
            "sport": "football",
            "validationStatus": "validated",
            "kickoffUtc": kickoff_utc,
        })

        # Candidate 4: fully validated with validatedMarkets (must pass)
        mongo_manager.predictions.insert_one({
            "id": "pred_valid",
            "fixture_id": "fix_valid",
            "sport": "football",
            "validationStatus": "validated",
            "kickoffUtc": kickoff_utc,
            "event_date_lagos": self.today_lagos,
            "percentage": 80.0,
            "selection": "Arsenal",
            "market": "1X2",
            "validatedMarkets": [{"marketName": "1X2", "selection": "Arsenal", "probabilityPercentage": 80.0}],
        })

        feed = await feed_service.get_feed(date=self.today_lagos)
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["id"], "pred_valid")

    async def test_available_prediction_dates_fallback_when_redis_empty(self):
        """
        Verify get_available_prediction_dates() works when Redis is completely empty
        and MongoDB serves as fallback via DatabaseRouter.
        """
        # Ensure Redis has no keys
        redis_client._local_cache.clear()

        # Seed two future predictions in MongoDB for different dates
        today_iso = f"{self.today_lagos}T14:00:00Z"
        future_iso = f"{self.future_date}T20:00:00Z"

        mongo_manager.predictions.insert_one({
            "id": "pred_d1",
            "fixture_id": "fix_d1",
            "sport": "football",
            "kickoffUtc": today_iso,
            "event_date_lagos": self.today_lagos,
            "validationStatus": "validated",
            "validatedMarkets": [{"marketName": "1X2", "selection": "Home", "probabilityPercentage": 65.0}],
        })

        mongo_manager.predictions.insert_one({
            "id": "pred_d2",
            "fixture_id": "fix_d2",
            "sport": "basketball",
            "kickoffUtc": future_iso,
            "event_date_lagos": self.future_date,
            "validationStatus": "validated",
            "validatedMarkets": [{"marketName": "Moneyline", "selection": "Away", "probabilityPercentage": 58.0}],
        })

        available_dates = await feed_service.get_available_prediction_dates()
        self.assertIn(self.today_lagos, available_dates)
        self.assertIn(self.future_date, available_dates)
        self.assertEqual(available_dates, sorted(list(set(available_dates))))

    async def test_twenty_prediction_publication_limit(self):
        """Verify the 20-prediction publication limit is strictly capped."""
        kickoff_utc = f"{self.today_lagos}T12:00:00Z"

        # Insert 30 validated predictions
        for i in range(30):
            mongo_manager.predictions.insert_one({
                "id": f"pred_limit_{i}",
                "fixture_id": f"fix_limit_{i}",
                "sport": "football",
                "validationStatus": "validated",
                "kickoffUtc": kickoff_utc,
                "event_date_lagos": self.today_lagos,
                "percentage": 60.0 + (i * 0.5),
                "selection": f"Team {i}",
                "market": "1X2",
                "validatedMarkets": [
                    {"marketName": "1X2", "selection": f"Team {i}", "probabilityPercentage": 60.0 + (i * 0.5)}
                ],
            })

        feed = await feed_service.get_feed(date=self.today_lagos, limit=50)
        self.assertEqual(len(feed), 20, "Feed must be strictly capped at 20 published predictions")

    async def test_database_fallback_sorting_order(self):
        """
        Verify Requirement 10:
        Database fallback must be strictly sorted by:
        1. best-of-day first
        2. highest calibrated probability
        3. kickoff time
        """
        # Clear Redis to force database fallback
        redis_client._local_cache.clear()

        # Seed items with different bestOfDay, probabilities, and kickoff times
        items = [
            {
                "id": "pred_c",
                "fixture_id": "fix_c",
                "sport": "football",
                "validationStatus": "validated",
                "isBestOfDay": False,
                "calibratedPercentage": 85.0,
                "kickoffUtc": f"{self.today_lagos}T20:00:00Z",
                "event_date_lagos": self.today_lagos,
                "validatedMarkets": [{"marketName": "1X2", "selection": "Team C", "probabilityPercentage": 85.0}],
            },
            {
                "id": "pred_a",
                "fixture_id": "fix_a",
                "sport": "football",
                "validationStatus": "validated",
                "isBestOfDay": False,
                "calibratedPercentage": 75.0,
                "kickoffUtc": f"{self.today_lagos}T15:00:00Z",
                "event_date_lagos": self.today_lagos,
                "validatedMarkets": [{"marketName": "1X2", "selection": "Team A", "probabilityPercentage": 75.0}],
            },
            {
                "id": "pred_b",
                "fixture_id": "fix_b",
                "sport": "football",
                "validationStatus": "validated",
                "isBestOfDay": True,
                "calibratedPercentage": 65.0,
                "kickoffUtc": f"{self.today_lagos}T18:00:00Z",
                "event_date_lagos": self.today_lagos,
                "validatedMarkets": [{"marketName": "1X2", "selection": "Team B", "probabilityPercentage": 65.0}],
            },
            {
                "id": "pred_e",
                "fixture_id": "fix_e",
                "sport": "football",
                "validationStatus": "validated",
                "isBestOfDay": False,
                "calibratedPercentage": 75.0,
                "kickoffUtc": f"{self.today_lagos}T12:00:00Z",
                "event_date_lagos": self.today_lagos,
                "validatedMarkets": [{"marketName": "1X2", "selection": "Team E", "probabilityPercentage": 75.0}],
            },
            {
                "id": "pred_d",
                "fixture_id": "fix_d",
                "sport": "football",
                "validationStatus": "validated",
                "isBestOfDay": True,
                "calibratedPercentage": 80.0,
                "kickoffUtc": f"{self.today_lagos}T14:00:00Z",
                "event_date_lagos": self.today_lagos,
                "validatedMarkets": [{"marketName": "1X2", "selection": "Team D", "probabilityPercentage": 80.0}],
            },
        ]
        mongo_manager.predictions.insert_many(items)

        feed = await feed_service.get_feed(date=self.today_lagos)
        self.assertEqual(len(feed), 5)

        # Expected order:
        # 1. pred_d: isBestOfDay=True, prob=80.0
        # 2. pred_b: isBestOfDay=True, prob=65.0
        # 3. pred_c: isBestOfDay=False, prob=85.0
        # 4. pred_e: isBestOfDay=False, prob=75.0, kickoff=12:00
        # 5. pred_a: isBestOfDay=False, prob=75.0, kickoff=15:00
        feed_ids = [f["id"] for f in feed]
        self.assertEqual(feed_ids, ["pred_d", "pred_b", "pred_c", "pred_e", "pred_a"])

    async def test_refresh_persists_database_first_and_handles_redis_failure(self):
        """
        Verify Requirements 1, 2, 3, 11:
        1. Refresh saves validated published predictions to active operational database FIRST.
        2. Only after successful persistence should the system publish them to Redis.
        3. If Redis fails:
           - do NOT delete the database predictions
           - do NOT change validationStatus
           - do NOT report that the prediction itself failed
           - mark only Redis publication as degraded
        4. Diagnostics must include 'redisFeedState' and predictions_published must NOT be zero.
        """
        from unittest.mock import patch, MagicMock
        from backend.services.sync_service import sync_service
        from backend.db.redis_client import RedisOperationResult, RedisState

        mock_pred = {
            "id": "pred_refresh_1",
            "fixture_id": "fix_refresh_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": f"{self.today_lagos}T15:00:00Z",
            "scheduled_at": f"{self.today_lagos}T15:00:00Z",
            "event_date_lagos": self.today_lagos,
            "status": "scheduled",
            "validationStatus": "validated",
            "modelVersion": "ELO + POISSON",
            "percentage": 78.0,
            "calibratedPercentage": 78.0,
            "selection": "Arsenal",
            "market": "Match Winner",
            "highestPercentagePrediction": {"marketName": "Match Winner", "selection": "Arsenal", "percentage": 78.0},
            "validatedMarkets": [{"id": "m1", "marketName": "Match Winner", "selection": "Arsenal", "probabilityPercentage": 78.0, "isValidated": True}],
        }

        # Simulate pipeline producing 1 validated prediction
        mock_pipeline_res = {
            "modelVersion": "ELO + POISSON",
            "publishedFeed": [mock_pred],
            "allResults": [{"fixtureId": "fix_refresh_1", "sport": "football", "validationStatus": "validated", "markets": [{"marketName": "Match Winner", "selection": "Arsenal", "calibratedPercentage": 78.0}]}],
            "diagnostics": {"fixturesEligible": 1, "validatedFixtures": 1},
        }

        # Mock Redis failing with DEGRADED state
        redis_fail_result = RedisOperationResult(
            success=False,
            state=RedisState.DEGRADED,
            error="Connection timeout to Redis",
        )

        with patch("backend.services.sync_service.execute_prediction_pipeline", return_value=mock_pipeline_res), \
             patch.object(redis_client, "set_json", return_value=redis_fail_result):

            record = await sync_service.execute_refresh(sports=["football"], date=self.today_lagos, force=True)

            # 1. Predictions must be preserved in active operational database
            db_doc = mongo_manager.predictions.find_one({"id": "pred_refresh_1"})
            self.assertIsNotNone(db_doc, "Prediction must be persisted in database despite Redis failure")
            self.assertEqual(db_doc["validationStatus"], "validated")

            # 2. predictions_published must NOT be marked zero
            self.assertEqual(record.predictions_published, 1)

            # 3. Diagnostics must contain redisFeedState
            self.assertIn("redisFeedState", record.diagnostics)
            self.assertIn(str(record.diagnostics["redisFeedState"]).upper(), ("DEGRADED", "REDISSTATE.DEGRADED"))
            self.assertTrue(record.diagnostics.get("redisPublicationDegraded", False))

            # 4. Status must NOT be failed; prediction refresh itself succeeded
            self.assertIn(record.status, ("completed", "partial"))

            # 5. Database fallback serving path still works seamlessly for clients
            feed = await feed_service.get_feed(date=self.today_lagos)
            self.assertEqual(len(feed), 1)
            self.assertEqual(feed[0]["id"], "pred_refresh_1")
            self.assertEqual(feed[0]["validationStatus"], "validated")

    async def test_prediction_repository_publish_and_fallback(self):
        """
        Verify PredictionRepository.publish_daily_feed persists to database first
        and get_daily_feed sorts database fallback.
        """
        from backend.services.data_access.prediction_repository import prediction_repository
        from backend.db.redis_client import RedisOperationResult, RedisState

        test_item = {
            "id": "pred_repo_1",
            "fixture_id": "fix_repo_1",
            "sport": "football",
            "validationStatus": "validated",
            "kickoffUtc": f"{self.today_lagos}T16:00:00Z",
            "event_date_lagos": self.today_lagos,
            "percentage": 82.0,
            "calibratedPercentage": 82.0,
            "validatedMarkets": [{"marketName": "1X2", "selection": "Home", "probabilityPercentage": 82.0}],
        }

        # Mock Redis failing during publish_daily_feed
        with patch.object(redis_client, "set_json", return_value=RedisOperationResult(success=False, state=RedisState.DEGRADED)):
            success = await prediction_repository.publish_daily_feed(self.today_lagos, [test_item])
            self.assertTrue(success, "publish_daily_feed should succeed because operational database succeeded")

            # Check database has the item
            saved = mongo_manager.predictions.find_one({"id": "pred_repo_1"})
            self.assertIsNotNone(saved)

        # Clear Redis and verify get_daily_feed retrieves from database fallback
        redis_client._dev_test_cache.clear()
        feed = await prediction_repository.get_daily_feed(self.today_lagos)
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["id"], "pred_repo_1")
        redis_client._dev_test_cache.clear()


if __name__ == "__main__":
    unittest.main()
