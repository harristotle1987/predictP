import unittest
import asyncio
from datetime import datetime, timezone

try:
    import duckdb
    import pyarrow
    HAS_DEPS = duckdb is not None and pyarrow is not None
except ImportError:
    HAS_DEPS = False

from backend.providers.sports_skills_hockey_provider import sports_skills_hockey_provider
from backend.historical.hockey_historical_sync import hockey_historical_sync
from backend.db.duckdb_engine import duckdb_engine
from backend.engine.hockey_elo_engine import run_hockey_elo_engine
from backend.engine.hockey_poisson_engine import run_hockey_poisson_engine
from backend.engine.pipeline import execute_prediction_pipeline
from backend.features.hockey_feature_builder import build_hockey_features

@unittest.skipIf(not HAS_DEPS, "duckdb or pyarrow not installed")
class TestHockeyIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ingest hockey historical data into Parquet / DuckDB
        asyncio.run(hockey_historical_sync.ingest_hockey("test_run_hockey"))
        from backend.engine.calibration import seed_initial_production_calibrations
        seed_initial_production_calibrations()

    def test_1_nhl_provider_returns_normalized_hockey_fixtures(self):
        """1. NHL provider returns normalized hockey fixtures."""
        fixtures = asyncio.run(sports_skills_hockey_provider.fetch_fixtures())
        self.assertIsInstance(fixtures, list)
        self.assertGreater(len(fixtures), 0, "NHL provider should return at least 1 fixture")
        
        for f in fixtures:
            self.assertEqual(f["sport"], "hockey")
            self.assertIn("league", f)
            self.assertIn("homeTeam", f)
            self.assertIn("awayTeam", f)
            self.assertIn("status", f)
            self.assertIn(f["status"], ["scheduled", "live", "completed"])
            self.assertIn("currentScore", f)
            self.assertIn("display", f["currentScore"])
            if f["status"] in ("live", "completed"):
                self.assertIn("-", f["currentScore"]["display"])
            else:
                self.assertEqual(f["currentScore"]["display"], "Upcoming")

    def test_2_hockey_history_loaded_from_parquet_duckdb(self):
        """2. Hockey history is loaded from R2/DuckDB."""
        count = duckdb_engine.get_sport_history_count("hockey")
        self.assertGreater(count, 0, "DuckDB should have registered hockey historical rows")
        
        pit = duckdb_engine.get_point_in_time_matches("hockey", "2026-10-01T00:00:00Z")
        self.assertGreater(len(pit), 0)
        for row in pit:
            self.assertEqual(row.get("status"), "completed")
            self.assertIsNotNone(row.get("home_score"))
            self.assertIsNotNone(row.get("away_score"))

    def test_3_hockey_elo_receives_only_hockey_rows(self):
        """3. Hockey ELO receives only hockey rows."""
        res = run_hockey_elo_engine("hockey", "Boston Bruins", "Buffalo Sabres", "2026-10-01T00:00:00Z")
        self.assertEqual(res["metadata"]["sport"], "hockey")
        self.assertEqual(res["metadata"]["model_name"], "HockeyElo")
        self.assertTrue(res["hasSufficientData"])
        self.assertGreater(len(res["markets"]), 0)

    def test_4_hockey_poisson_receives_only_hockey_rows(self):
        """4. Hockey Poisson receives only hockey rows."""
        res = run_hockey_poisson_engine("hockey", "Boston Bruins", "Buffalo Sabres", "2026-10-01T00:00:00Z")
        self.assertEqual(res["metadata"]["sport"], "hockey")
        self.assertEqual(res["metadata"]["model_name"], "HockeyPoisson")
        self.assertTrue(res["hasSufficientData"])
        self.assertGreater(len(res["markets"]), 0)

    def test_5_hockey_predictions_cannot_use_other_sports_data(self):
        """5. Hockey predictions cannot use football/basketball/baseball training data."""
        with self.assertRaises(ValueError):
            run_hockey_elo_engine("football", "Arsenal", "Chelsea", "2026-10-01T00:00:00Z")
        with self.assertRaises(ValueError):
            run_hockey_elo_engine("basketball", "Lakers", "Warriors", "2026-10-01T00:00:00Z")
        with self.assertRaises(ValueError):
            run_hockey_elo_engine("baseball", "Yankees", "Red Sox", "2026-10-01T00:00:00Z")

        with self.assertRaises(ValueError):
            run_hockey_poisson_engine("football", "Arsenal", "Chelsea", "2026-10-01T00:00:00Z")
        with self.assertRaises(ValueError):
            run_hockey_poisson_engine("basketball", "Lakers", "Warriors", "2026-10-01T00:00:00Z")
        with self.assertRaises(ValueError):
            run_hockey_poisson_engine("baseball", "Yankees", "Red Sox", "2026-10-01T00:00:00Z")

    def test_6_insufficient_history_causes_abstention(self):
        """6. Insufficient history causes abstention."""
        # Non-existent or new team with 0 completed historical games
        res_elo = run_hockey_elo_engine("hockey", "Unknown Hockey Team A", "Unknown Hockey Team B", "2026-10-01T00:00:00Z")
        self.assertFalse(res_elo["hasSufficientData"])
        self.assertEqual(len(res_elo["markets"]), 0)

        res_poisson = run_hockey_poisson_engine("hockey", "Unknown Hockey Team A", "Unknown Hockey Team B", "2026-10-01T00:00:00Z")
        self.assertFalse(res_poisson["hasSufficientData"])
        self.assertEqual(len(res_poisson["markets"]), 0)

        feats = build_hockey_features("Unknown Team A", "Unknown Team B", "2026-10-01T00:00:00Z")
        self.assertFalse(feats["hasSufficientData"])

    def test_7_only_validated_hockey_predictions_reach_published_feed(self):
        """7. Only validated hockey predictions reach the published feed."""
        test_fixtures = [
            {
                "id": "hk_test_valid_1",
                "sport": "hockey",
                "league": "NHL",
                "homeTeam": "Boston Bruins",
                "awayTeam": "Buffalo Sabres",
                "kickoffUtc": "2026-10-05T19:00:00Z",
                "status": "scheduled",
                "currentScore": {"home": 0, "away": 0, "display": "Upcoming"},
            },
            {
                "id": "hk_test_invalid_insufficient",
                "sport": "hockey",
                "league": "NHL",
                "homeTeam": "Unknown Team Alpha",
                "awayTeam": "Unknown Team Beta",
                "kickoffUtc": "2026-10-05T19:00:00Z",
                "status": "scheduled",
                "currentScore": {"home": 0, "away": 0, "display": "Upcoming"},
            },
            {
                "id": "hk_test_completed_match",
                "sport": "hockey",
                "league": "NHL",
                "homeTeam": "Boston Bruins",
                "awayTeam": "Buffalo Sabres",
                "kickoffUtc": "2026-09-20T19:00:00Z",
                "status": "completed",
                "currentScore": {"home": 4, "away": 2, "display": "4 - 2"},
            }
        ]

        pipe_res = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=True,
            custom_fixtures=test_fixtures
        )

        published_feed = pipe_res["publishedFeed"]
        published_ids = [p["id"] for p in published_feed]

        # Valid fixture should publish
        self.assertIn("hk_test_valid_1", published_ids)
        # Insufficient history fixture must abstain and never reach published feed
        self.assertNotIn("hk_test_invalid_insufficient", published_ids)
        # Completed fixture must not be published as active prediction
        self.assertNotIn("hk_test_completed_match", published_ids)

        for p in published_feed:
            self.assertEqual(p["validationStatus"], "validated")
            self.assertGreater(len(p["validatedMarkets"]), 0)
            self.assertIn("highestPercentagePrediction", p)
            self.assertIn("modelMetadata", p)
            self.assertEqual(p["modelMetadata"]["sport"], "hockey")

if __name__ == "__main__":
    unittest.main()
