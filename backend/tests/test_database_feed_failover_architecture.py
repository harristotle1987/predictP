"""
Targeted Verification Test Suite for PredictPro Database Feed Architecture & Failover.

Tests:
1. Mongo prediction feed (normal primary path, complete enriched fields, strict publication gate).
2. Neon prediction feed (secondary failover path, complete enriched fields, strict publication gate).
3. DatabaseRouter (single operational data access layer routing to active adapter).
4. Automatic failover (MongoDB failure triggers failover to Neon).
5. Redis cache failure (Redis down -> DatabaseRouter fallback returns complete predictions, never disappears).
6. Mongo -> Neon failover (feed requested during failover serves from Neon seamlessly).
7. Published prediction filtering (validationStatus == "validated", non-empty validatedMarkets, Lagos date filter, 20-limit cap).
8. IPredictionRepository contract equivalence (MongoDB and Neon implement equivalent interface and projection).
"""

import unittest
import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from backend.db.interfaces import (
    RoutingMode,
    FailoverState,
    IPredictionRepository,
    DatabaseUnavailableError,
)
from backend.db.mongodb import mongo_manager
from backend.db.mongodb_adapter import mongodb_adapter, MongoPredictionRepository, PREDICTION_PROJECTION
from backend.db.neon_adapter import neon_adapter, NeonPredictionRepository, NeonQueryExecutor
from backend.db.neon_budget_guard import SafetyState, neon_budget_guard
from backend.db.database_router import database_router, DatabaseRouter
from backend.db.failover_manager import failover_manager
from backend.db.redis_client import redis_client, RedisOperationResult, RedisState
from backend.services.feed_service import feed_service, get_current_lagos_today, LAGOS_TZ
from backend.services.data_access.prediction_repository import prediction_repository


class TestDatabaseFeedFailoverArchitecture(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        failover_manager.reset_state_for_tests()
        failover_manager.failure_threshold = 3
        failover_manager.cooldown_seconds = 0
        database_router.set_routing_mode(RoutingMode.AUTO)

        # Clear mock collections
        try:
            mongo_manager.db["operational_events"].delete_many({})
            mongo_manager.db["predictions"].delete_many({})
            mongo_manager.db["prediction_results"].delete_many({})
        except Exception:
            pass

        # Disable remote redis during testing to isolate DB paths
        self._orig_redis_url = redis_client.url
        self._orig_redis_token = redis_client.token
        redis_client.url = None
        redis_client.token = None
        redis_client._dev_test_cache.clear()

        self.today_lagos = get_current_lagos_today()
        self.sample_prediction = {
            "id": "pred_test_100",
            "fixture_id": "fix_test_100",
            "fixtureId": "fix_test_100",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": f"{self.today_lagos}T17:30:00Z",
            "scheduled_at": f"{self.today_lagos}T17:30:00Z",
            "event_date_lagos": self.today_lagos,
            "status": "scheduled",
            "market": "1X2",
            "selection": "Home",
            "percentage": 78.5,
            "raw_probability": 0.76,
            "calibrated_probability": 0.785,
            "calibratedPercentage": 78.5,
            "highestPercentagePrediction": {
                "marketName": "1X2",
                "selection": "Home",
                "percentage": 78.5,
            },
            "validatedMarkets": [
                {
                    "id": "fix_test_100-m1",
                    "marketName": "1X2",
                    "selection": "Home",
                    "probabilityPercentage": 78.5,
                    "confidenceRating": "HIGH",
                    "sportSpecificCategory": "1X2",
                    "isValidated": True,
                }
            ],
            "sportStats": {
                "xGRecentHome": 2.3,
                "xGRecentAway": 0.9,
                "homeGoalsScoredAvg": 2.1,
                "awayGoalsScoredAvg": 0.8,
            },
            "validationStatus": "validated",
            "modelVersion": "ELO + POISSON",
            "modelMetadata": {"model_name": "ELO_POISSON_V1", "training_window": "5_seasons"},
            "metadata": {"source": "predictpro_core"},
            "training_window": "5_seasons",
            "training_match_count": 80,
            "feature_timestamp": f"{self.today_lagos}T17:00:00Z",
            "prediction_timestamp": f"{self.today_lagos}T17:00:00Z",
            "isBestOfDay": True,
            "published": True,
        }

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
    # 1. MongoDB Prediction Feed (Primary Operational Store)
    # =========================================================================
    async def test_mongo_prediction_feed_returns_complete_fields(self):
        """Verify MongoDB adapter returns complete enriched published prediction."""
        await mongodb_adapter.predictions.save_predictions([self.sample_prediction])

        feed = await mongodb_adapter.predictions.get_daily_feed(self.today_lagos)
        self.assertEqual(len(feed), 1)
        item = feed[0]

        # Verify all preserved fields
        self.assertEqual(item["fixture_id"], "fix_test_100")
        self.assertEqual(item["percentage"], 78.5)
        self.assertEqual(item["calibrated_probability"], 0.785)
        self.assertEqual(item["selection"], "Home")
        self.assertEqual(item["market"], "1X2")
        self.assertEqual(item["validationStatus"], "validated")
        self.assertTrue(len(item["validatedMarkets"]) > 0)
        self.assertIsNotNone(item["sportStats"])
        self.assertEqual(item["sportStats"]["xGRecentHome"], 2.3)
        self.assertEqual(item["modelMetadata"]["training_window"], "5_seasons")
        self.assertEqual(item["event_date_lagos"], self.today_lagos)

    # =========================================================================
    # 2. Neon Prediction Feed (Secondary Operational Store)
    # =========================================================================
    async def test_neon_prediction_feed_returns_complete_fields(self):
        """Verify Neon adapter returns complete enriched published prediction."""
        mock_executor = MagicMock(spec=NeonQueryExecutor)
        # Mock database row returned from Neon query
        row_data = {
            "id": "pred_test_100",
            "fixtureId": "fix_test_100",
            "source_system": "predictpro_failover_neon",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": f"{self.today_lagos}T17:30:00Z",
            "date": self.today_lagos,
            "modelVersion": "ELO + POISSON",
            "validationStatus": "validated",
            "published": True,
            "calibratedPercentage": 78.5,
            "isBestOfDay": True,
            "prediction_payload": json.dumps(self.sample_prediction),
            "prediction_version": 1,
            "created_at": f"{self.today_lagos}T17:00:00Z",
            "updated_at": f"{self.today_lagos}T17:00:00Z",
        }
        mock_executor.execute_query = AsyncMock(return_value=[row_data])
        mock_executor.execute_statement = AsyncMock(return_value=1)

        neon_repo = NeonPredictionRepository(mock_executor)
        feed = await neon_repo.get_daily_feed(self.today_lagos)
        self.assertEqual(len(feed), 1)
        item = feed[0]

        # Verify preserved fields in Neon response
        self.assertEqual(item["fixture_id"], "fix_test_100")
        self.assertEqual(item["percentage"], 78.5)
        self.assertEqual(item["calibrated_probability"], 0.785)
        self.assertEqual(item["selection"], "Home")
        self.assertEqual(item["market"], "1X2")
        self.assertEqual(item["validationStatus"], "validated")
        self.assertTrue(len(item["validatedMarkets"]) > 0)
        self.assertEqual(item["sportStats"]["xGRecentHome"], 2.3)
        self.assertEqual(item["modelMetadata"]["training_window"], "5_seasons")
        self.assertEqual(item["event_date_lagos"], self.today_lagos)

    # =========================================================================
    # 3. DatabaseRouter Single Access Layer
    # =========================================================================
    async def test_databaserouter_routes_to_active_adapter(self):
        """DatabaseRouter must dispatch queries to MongoDB when healthy and Neon when failover active."""
        # 1. Normal state: MongoDB primary
        self.assertEqual(database_router.get_read_adapter(), mongodb_adapter)
        self.assertEqual(database_router.get_write_adapter(), mongodb_adapter)

        # 2. Simulated Failover state: Neon secondary
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True
        failover_manager._failover_state = FailoverState.NEON_FAILOVER

        with patch("backend.db.neon_adapter.neon_adapter.is_healthy", return_value=True):
            self.assertEqual(database_router.get_read_adapter(), neon_adapter)
            self.assertEqual(database_router.get_write_adapter(), neon_adapter)

    # =========================================================================
    # 4. Automatic Failover Mechanism
    # =========================================================================
    def test_automatic_failover_trigger(self):
        """Three consecutive MongoDB failures trigger automatic failover to Neon."""
        with patch("backend.db.neon_adapter.neon_adapter.check_connection", return_value={"status": "connected"}):
            with patch("backend.db.neon_budget_guard.neon_budget_guard.get_safety_state", return_value=SafetyState.OPTIMAL):
                with patch("backend.db.replication_manager.replication_manager.get_status", return_value={"lastSummary": {"replication_lag_seconds": 0.0}}):
                    failover_manager.record_mongo_failure("Network timeout 1")
                    self.assertEqual(failover_manager.active_backend, "mongodb")
                    failover_manager.record_mongo_failure("Network timeout 2")
                    self.assertEqual(failover_manager.active_backend, "mongodb")

                    # 3rd failure executes failover
                    failover_manager.record_mongo_failure("Network timeout 3")
                    self.assertEqual(failover_manager.active_backend, "neon")
                    self.assertTrue(failover_manager.failover_active)
                    self.assertEqual(failover_manager.failover_state, FailoverState.NEON_FAILOVER)

    # =========================================================================
    # 5. Redis Cache Failure / Cache Miss Path
    # =========================================================================
    async def test_redis_failure_falls_back_to_databaserouter_without_data_loss(self):
        """When Redis fails or misses, FeedService retrieves full prediction from DatabaseRouter."""
        # Seed MongoDB
        await mongodb_adapter.predictions.save_predictions([self.sample_prediction])

        # Simulate Redis get_json throwing error
        with patch.object(redis_client, "get_json", side_effect=RuntimeError("Redis connection refused")):
            feed = await feed_service.get_feed(date=self.today_lagos)
            self.assertEqual(len(feed), 1)
            self.assertEqual(feed[0]["id"], "pred_test_100")
            self.assertEqual(feed[0]["selection"], "Home")
            self.assertEqual(feed[0]["percentage"], 78.5)

    # =========================================================================
    # 6. Mongo -> Neon Failover Serving
    # =========================================================================
    async def test_feed_service_serves_from_neon_during_failover(self):
        """During active failover, FeedService requests through DatabaseRouter to Neon."""
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True
        failover_manager._failover_state = FailoverState.NEON_FAILOVER

        # Mock Neon prediction repository response and Neon health
        with patch("backend.db.neon_adapter.neon_adapter.is_healthy", return_value=True):
            with patch.object(neon_adapter.predictions, "get_daily_feed", return_value=[self.sample_prediction]):
                feed = await feed_service.get_feed(date=self.today_lagos)
                self.assertEqual(len(feed), 1)
                self.assertEqual(feed[0]["fixture_id"], "fix_test_100")
                self.assertEqual(feed[0]["validationStatus"], "validated")

    # =========================================================================
    # 7. Strict Publication Filtering & Limit Gates
    # =========================================================================
    async def test_published_prediction_strict_filtering_and_20_limit(self):
        """
        FeedService must:
        - reject items with validationStatus != 'validated'
        - reject items with empty validatedMarkets
        - cap results at 20 items max
        """
        predictions_to_insert = []
        # Add 5 invalid predictions (unvalidated or empty markets)
        predictions_to_insert.append({
            "id": "pred_invalid_1",
            "fixture_id": "fix_inv_1",
            "validationStatus": "unvalidated",
            "event_date_lagos": self.today_lagos,
            "kickoffUtc": f"{self.today_lagos}T12:00:00Z",
            "validatedMarkets": [{"marketName": "1X2", "selection": "Home", "probabilityPercentage": 70.0}],
        })
        predictions_to_insert.append({
            "id": "pred_invalid_2",
            "fixture_id": "fix_inv_2",
            "validationStatus": "validated",
            "event_date_lagos": self.today_lagos,
            "kickoffUtc": f"{self.today_lagos}T12:00:00Z",
            "validatedMarkets": [],  # Empty markets -> must be rejected
        })

        # Add 25 valid predictions
        for i in range(25):
            predictions_to_insert.append({
                "id": f"pred_valid_{i}",
                "fixture_id": f"fix_valid_{i}",
                "validationStatus": "validated",
                "event_date_lagos": self.today_lagos,
                "kickoffUtc": f"{self.today_lagos}T12:00:00Z",
                "percentage": 60.0 + (i * 0.5),
                "calibratedPercentage": 60.0 + (i * 0.5),
                "selection": f"Team {i}",
                "market": "1X2",
                "validatedMarkets": [
                    {"marketName": "1X2", "selection": f"Team {i}", "probabilityPercentage": 60.0 + (i * 0.5)}
                ],
            })

        for p in predictions_to_insert:
            mongo_manager.predictions.insert_one(p)

        feed = await feed_service.get_feed(date=self.today_lagos, limit=50)

        # 1. Verify invalid predictions are filtered out
        feed_ids = [m["id"] for m in feed]
        self.assertNotIn("pred_invalid_1", feed_ids)
        self.assertNotIn("pred_invalid_2", feed_ids)

        # 2. Verify strict limit of 20
        self.assertEqual(len(feed), 20)

    # =========================================================================
    # 8. Interface Contract Validation
    # =========================================================================
    def test_repository_interface_equivalence(self):
        """Ensure MongoPredictionRepository, NeonPredictionRepository and RoutedPredictionRepository implement IPredictionRepository."""
        self.assertTrue(issubclass(MongoPredictionRepository, IPredictionRepository))
        self.assertTrue(issubclass(NeonPredictionRepository, IPredictionRepository))
        self.assertTrue(isinstance(database_router.predictions, IPredictionRepository))


if __name__ == "__main__":
    unittest.main()
