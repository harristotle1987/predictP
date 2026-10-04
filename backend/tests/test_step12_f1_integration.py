import unittest
import asyncio
from datetime import datetime, timezone

try:
    import duckdb
    import pyarrow
    HAS_DEPS = duckdb is not None and pyarrow is not None
except ImportError:
    HAS_DEPS = False

from backend.providers.sports_skills_f1_provider import sports_skills_f1_provider
from backend.historical.f1_historical_sync import f1_historical_sync
from backend.db.duckdb_engine import duckdb_engine
from backend.features.f1_feature_builder import build_f1_features
from backend.engine.f1_rating_engine import compute_f1_ratings
from backend.engine.f1_probability_engine import run_f1_probability_engine
from backend.markets.f1_markets import build_f1_markets
from backend.engine.pipeline import execute_prediction_pipeline

@unittest.skipIf(not HAS_DEPS, "duckdb or pyarrow not installed")
class TestF1Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ingest real F1 historical data into Parquet / DuckDB
        asyncio.run(f1_historical_sync.ingest_f1("test_run_f1"))
        from backend.engine.calibration import seed_initial_production_calibrations
        seed_initial_production_calibrations()

    def test_1_f1_provider_returns_normalized_f1_fixtures(self):
        """1. F1 provider returns normalized race events/schedules."""
        fixtures = asyncio.run(sports_skills_f1_provider.fetch_fixtures())
        self.assertIsInstance(fixtures, list)
        self.assertGreater(len(fixtures), 0, "F1 provider should return at least 1 race fixture")
        
        for f in fixtures:
            self.assertEqual(f["sport"], "formula_1")
            self.assertIn("league", f)
            self.assertIn("status", f)
            self.assertIn(f["status"], ["scheduled", "live", "completed"])
            self.assertIn("currentScore", f)
            if f["status"] == "scheduled":
                self.assertEqual(f["currentScore"]["display"], "Upcoming")

    def test_2_point_in_time_f1_feature_generation_and_leakage_protection(self):
        """2. Point-in-time F1 feature generation: never leaks future race results."""
        # Cutoff before Australian GP (Round 3 on 2024-03-24)
        cutoff_early = "2024-03-15T00:00:00Z"
        early_feats = build_f1_features(cutoff_early)
        self.assertTrue(early_feats["hasSufficientData"])
        # At this early point, only Bahrain and Saudi Arabia (Rounds 1 & 2) took place
        verstappen_early = next(d for d in early_feats["drivers"] if d["driverId"] == "max_verstappen")
        self.assertGreaterEqual(verstappen_early["observationCount"], 2)

        # Cutoff after Miami GP (Round 6 on 2024-05-05)
        cutoff_later = "2024-05-10T00:00:00Z"
        later_feats = build_f1_features(cutoff_later)
        verstappen_later = next(d for d in later_feats["drivers"] if d["driverId"] == "max_verstappen")
        self.assertGreater(verstappen_later["observationCount"], verstappen_early["observationCount"])

    def test_3_driver_constructor_and_race_identity(self):
        """3. Verified driver identity, constructor identity, and circuit identity."""
        feats = build_f1_features("2024-06-01T00:00:00Z")
        self.assertTrue(feats["hasSufficientData"])
        driver_ids = {d["driverId"] for d in feats["drivers"]}
        constructor_ids = {c["constructorId"] for c in feats["constructors"]}

        self.assertIn("max_verstappen", driver_ids)
        self.assertIn("norris", driver_ids)
        self.assertIn("leclerc", driver_ids)
        self.assertIn("hamilton", driver_ids)

        self.assertIn("red_bull", constructor_ids)
        self.assertIn("mclaren", constructor_ids)
        self.assertIn("ferrari", constructor_ids)
        self.assertIn("mercedes", constructor_ids)

    def test_4_f1_engines_reject_other_sports(self):
        """4. F1 rating and probability engines reject other sports (fail closed)."""
        with self.assertRaises(ValueError):
            compute_f1_ratings("football", "2024-06-01T00:00:00Z")
        with self.assertRaises(ValueError):
            compute_f1_ratings("basketball", "2024-06-01T00:00:00Z")
        with self.assertRaises(ValueError):
            compute_f1_ratings("hockey", "2024-06-01T00:00:00Z")

        with self.assertRaises(ValueError):
            run_f1_probability_engine("football", "2024-06-01T00:00:00Z")
        with self.assertRaises(ValueError):
            run_f1_probability_engine("baseball", "2024-06-01T00:00:00Z")

    def test_5_f1_markets_generation(self):
        """5. F1 probability engine produces required F1 markets."""
        prob_res = run_f1_probability_engine("formula_1", "2024-06-01T00:00:00Z")
        self.assertTrue(prob_res["hasSufficientData"])
        markets = build_f1_markets(prob_res["drivers"])

        market_categories = {m["marketCategory"] for m in markets}
        self.assertIn("RaceWinner", market_categories)
        self.assertIn("Podium", market_categories)
        self.assertIn("Top10", market_categories)
        self.assertIn("H2H", market_categories)
        self.assertIn("FastestLap", market_categories)

    def test_6_only_validated_f1_predictions_reach_published_feed(self):
        """6. Only validated F1 predictions enter published feed."""
        test_fixtures = [
            {
                "id": "f1_test_grand_prix_2026",
                "sport": "formula_1",
                "league": "Formula 1",
                "homeTeam": "F1 Drivers Field",
                "awayTeam": "F1 Constructors Field",
                "kickoffUtc": "2026-10-05T13:00:00Z",
                "status": "scheduled",
                "currentScore": {"home": 0, "away": 0, "display": "Upcoming"},
            },
            {
                "id": "f1_test_completed_race",
                "sport": "formula_1",
                "league": "Formula 1",
                "homeTeam": "F1 Drivers Field",
                "awayTeam": "F1 Constructors Field",
                "kickoffUtc": "2024-03-02T15:00:00Z",
                "status": "completed",
                "currentScore": {"home": 0, "away": 0, "display": "Race Concluded"},
            }
        ]

        pipe_res = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=True,
            custom_fixtures=test_fixtures
        )

        published_feed = pipe_res["publishedFeed"]
        published_ids = [p["id"] for p in published_feed]

        # Valid upcoming F1 race should be published
        self.assertIn("f1_test_grand_prix_2026", published_ids)
        # Completed race must not be published as active prediction
        self.assertNotIn("f1_test_completed_race", published_ids)

        f1_pub = next(p for p in published_feed if p["id"] == "f1_test_grand_prix_2026")
        self.assertEqual(f1_pub["validationStatus"], "validated")
        self.assertGreater(len(f1_pub["validatedMarkets"]), 0)
        self.assertIn("f1Drivers", f1_pub["sportStats"])
        self.assertEqual(f1_pub["modelMetadata"]["sport"], "formula_1")

if __name__ == "__main__":
    unittest.main()
