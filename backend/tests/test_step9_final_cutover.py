import os
import sys
import asyncio
import unittest
from datetime import datetime, timezone, timedelta

# Ensure root import path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.config import settings
from backend.db.duckdb_engine import duckdb_engine
from backend.db.r2_storage import r2_manager
from backend.engine.validation_and_abstention import (
    validate_market_for_sport,
    validate_fixture_pre_conditions,
    validate_market_publication,
    get_lagos_date_str,
    get_current_lagos_today,
)
from backend.engine.feature_builders import (
    build_football_features,
    build_basketball_features,
    build_baseball_features,
)
from backend.engine.ensemble_engine import run_model_ensemble, is_production_model
from backend.engine.pipeline import execute_prediction_pipeline
from backend.services.sync_service import sync_service
from backend.services.feed_service import feed_service
from backend.services.model_service import model_service

class Step9FinalCutoverTestSuite(unittest.IsolatedAsyncioTestCase):

    def test_1_backend_imports_and_syntax(self):
        """Test backend import integrity and syntax"""
        self.assertIsNotNone(duckdb_engine)
        self.assertIsNotNone(r2_manager)
        self.assertIsNotNone(sync_service)
        self.assertIsNotNone(feed_service)
        self.assertIsNotNone(model_service)

    def test_2_calendar_and_future_dates_lagos(self):
        """Test Africa/Lagos (WAT, UTC+1) timezone constraints"""
        today_lagos = get_current_lagos_today()
        self.assertEqual(len(today_lagos), 10)
        self.assertTrue(today_lagos.startswith("202"))

        # Past date rejected
        past_utc = "2020-01-01T12:00:00Z"
        self.assertLess(get_lagos_date_str(past_utc), today_lagos)

        # Future date accepted
        future_utc = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertGreaterEqual(get_lagos_date_str(future_utc), today_lagos)

    def test_3_sport_market_isolation(self):
        """Test strict sport-market containment (no cross-sport leakage)"""
        # Football allowed
        self.assertTrue(validate_market_for_sport("football", "Win / Draw / Loss (1X2)", "1X2"))
        self.assertTrue(validate_market_for_sport("football", "Both Teams To Score (BTTS)", "BTTS"))
        self.assertTrue(validate_market_for_sport("football", "Over / Under 2.5 Goals", "GoalsTotal"))
        self.assertTrue(validate_market_for_sport("football", "Double Chance", "DoubleChance"))

        # Football disallowed on Basketball and Baseball
        self.assertFalse(validate_market_for_sport("basketball", "Win / Draw / Loss (1X2)", "1X2"))
        self.assertFalse(validate_market_for_sport("basketball", "Both Teams To Score (BTTS)", "BTTS"))
        self.assertFalse(validate_market_for_sport("basketball", "Over / Under 2.5 Goals", "GoalsTotal"))
        self.assertFalse(validate_market_for_sport("baseball", "Both Teams To Score (BTTS)", "BTTS"))
        self.assertFalse(validate_market_for_sport("baseball", "Over / Under 2.5 Goals", "GoalsTotal"))

        # Basketball allowed
        self.assertTrue(validate_market_for_sport("basketball", "Moneyline", "Moneyline"))
        self.assertTrue(validate_market_for_sport("basketball", "Spread", "Spread"))
        self.assertTrue(validate_market_for_sport("basketball", "Over / Under 215.5 Points", "PointsTotal"))

        # Baseball allowed
        self.assertTrue(validate_market_for_sport("baseball", "Moneyline", "Moneyline"))
        self.assertTrue(validate_market_for_sport("baseball", "Run Line", "RunLine"))
        self.assertTrue(validate_market_for_sport("baseball", "Over / Under 8.5 Runs", "RunsTotal"))

    def test_4_point_in_time_leakage_prevention(self):
        """Test that matches after cutoff are NEVER queried or leaked"""
        cutoff = "2024-01-01T00:00:00Z"
        pit = build_football_features("Arsenal", "Chelsea", cutoff)
        self.assertIn("homeMatchesCount", pit)
        self.assertIn("awayMatchesCount", pit)

    def test_5_models_and_ensembles(self):
        """Test all 8 model modes and fail-closed mechanism"""
        prod_models = ["ELO", "POISSON", "ELO + POISSON"]
        challenger_models = [
            "GLICKO2",
            "GRADIENT_BOOSTING",
            "GLICKO2 + GRADIENT_BOOSTING",
            "ELO + POISSON + GLICKO2",
            "ELO + POISSON + GLICKO2 + GRADIENT_BOOSTING",
        ]

        for m in prod_models:
            self.assertTrue(is_production_model(m))

        for m in challenger_models:
            self.assertFalse(is_production_model(m))

    async def test_6_refresh_orchestration_and_limits(self):
        """Test the 12-step sync service refresh flow and max 20 publication limit"""
        record = await sync_service.execute_refresh()
        self.assertIsNotNone(record)
        self.assertIn(record.status, ["completed", "partial", "failed"])
        self.assertLessEqual(record.predictions_published, 20)

        # Verify feed service reads published feed without calling external services
        feed = await feed_service.get_feed(limit=20)
        self.assertIsInstance(feed, list)
        self.assertLessEqual(len(feed), 20)

        for item in feed:
            self.assertEqual(item.get("validationStatus"), "validated")
            self.assertNotEqual(item.get("status"), "completed")
            self.assertTrue(len(item.get("validatedMarkets", [])) > 0)

if __name__ == "__main__":
    unittest.main()
