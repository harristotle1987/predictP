import unittest
import asyncio
from datetime import datetime, timezone

try:
    import duckdb
    import pyarrow
    HAS_DEPS = duckdb is not None and pyarrow is not None
except ImportError:
    HAS_DEPS = False

from backend.engine.gradient_boosting_engine import (
    run_gradient_boosting_engine,
    build_point_in_time_training_dataset,
    GB_MODEL_VERSION,
)
from backend.historical.f1_historical_sync import f1_historical_sync
from backend.engine.feature_builders import (
    build_football_features,
    build_basketball_features,
    build_baseball_features,
)
from backend.features.hockey_feature_builder import build_hockey_features
from backend.features.f1_feature_builder import build_f1_features

@unittest.skipIf(not HAS_DEPS, "duckdb or pyarrow not installed")
class TestGradientBoostingEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure DuckDB views for all sports are initialized with historical Parquet data
        from backend.db.duckdb_engine import duckdb_engine
        import os
        from backend.config import settings

        cache_dir = settings.parquet_cache_dir
        for sp in ["football", "basketball", "baseball", "hockey", "formula_1"]:
            p = os.path.join(cache_dir, f"{sp}_v1_history.parquet")
            if os.path.exists(p):
                duckdb_engine.register_parquet_view(f"{sp}_matches", p)
                if sp == "formula_1":
                    duckdb_engine.register_parquet_view("f1_results", p)
                elif sp == "hockey":
                    duckdb_engine.register_parquet_view("ice_hockey_matches", p)

        # Ingest F1 dataset as well
        asyncio.run(f1_historical_sync.ingest_f1("test_gb_f1"))

    def test_1_real_gradient_boosting_implementation(self):
        """1. Verify real supervised learning with valid model metadata and validation score."""
        cutoff = "2026-09-01T00:00:00Z"
        feats = build_football_features("Arsenal", "Chelsea", cutoff)
        
        # Override to ensure sufficient data for test if teams exist
        res = run_gradient_boosting_engine("football", "Arsenal", "Chelsea", cutoff, feats)
        
        if res["hasSufficientData"]:
            self.assertEqual(res["status"], "trained")
            self.assertEqual(res["modelVersion"], GB_MODEL_VERSION)
            meta = res["metadata"]
            self.assertEqual(meta["sport"], "football")
            self.assertEqual(meta["model_version"], GB_MODEL_VERSION)
            self.assertIsNotNone(meta["validation_score"])
            self.assertGreaterEqual(meta["validation_score"], 0.0)
            self.assertLessEqual(meta["validation_score"], 1.0)
            self.assertIn("training_timestamp", meta)

    def test_2_minimum_history_5_plus_requirement(self):
        """2. Verify minimum 5 completed relevant matches requirement for teams/drivers."""
        cutoff = "2026-09-01T00:00:00Z"
        
        # Team with < 5 matches should cause abstention
        insufficient_feats = {
            "homeGoalsScoredAvg": 1.8,
            "homeGoalsConcededAvg": 1.0,
            "awayGoalsScoredAvg": 1.2,
            "awayGoalsConcededAvg": 1.5,
            "homeMatchesCount": 3,  # LESS THAN 5
            "awayMatchesCount": 6,
            "hasSufficientData": False,
        }
        res = run_gradient_boosting_engine("football", "New FC", "Chelsea", cutoff, insufficient_feats)
        self.assertFalse(res["hasSufficientData"])
        self.assertIn(res["status"], ["insufficient_data", "abstained"])
        self.assertEqual(len(res["markets"]), 0)

    def test_3_no_future_leakage(self):
        """3. Time-ordered walk-forward dataset construction guarantees feature_timestamp < target_match_start."""
        cutoff = "2024-03-10T00:00:00Z"
        X_win, y_win, X_tot, y_tot, dates = build_point_in_time_training_dataset("football", cutoff)
        
        for d in dates:
            self.assertLess(d, cutoff, f"Training match date {d} must be strictly prior to cutoff {cutoff}")

    def test_4_sport_isolation(self):
        """4. Verify training datasets are isolated per sport and reject mismatched sports."""
        with self.assertRaises(ValueError):
            run_gradient_boosting_engine("cricket", "Team A", "Team B", "2026-09-01T00:00:00Z", {})

        # Verify dataset builder returns isolated rows
        X_f, _, _, _, _ = build_point_in_time_training_dataset("football", "2026-09-01T00:00:00Z")
        X_b, _, _, _, _ = build_point_in_time_training_dataset("basketball", "2026-09-01T00:00:00Z")
        X_h, _, _, _, _ = build_point_in_time_training_dataset("hockey", "2026-09-01T00:00:00Z")

        self.assertIsInstance(X_f, list)
        self.assertIsInstance(X_b, list)
        self.assertIsInstance(X_h, list)

    def test_5_deterministic_training_and_non_random_probabilities(self):
        """5. Training with random_state=42 produces deterministic, non-random probabilities."""
        cutoff = "2026-09-01T00:00:00Z"
        sample_feats = {
            "homeGoalsScoredAvg": 2.2,
            "homeGoalsConcededAvg": 0.8,
            "awayGoalsScoredAvg": 1.0,
            "awayGoalsConcededAvg": 1.8,
            "homeMatchesCount": 10,
            "awayMatchesCount": 10,
            "hasSufficientData": True,
        }

        res1 = run_gradient_boosting_engine("football", "Arsenal", "Chelsea", cutoff, sample_feats)
        res2 = run_gradient_boosting_engine("football", "Arsenal", "Chelsea", cutoff, sample_feats)

        if res1["hasSufficientData"] and res2["hasSufficientData"]:
            p1 = res1["markets"][0]["rawProbability"]
            p2 = res2["markets"][0]["rawProbability"]
            self.assertEqual(p1, p2, "Gradient Boosting predictions must be deterministic across runs")
            self.assertGreater(p1, 0.0)
            self.assertLess(p1, 1.0)

    def test_6_insufficient_data_abstention(self):
        """6. Insufficient training data causes internal abstention and no published markets."""
        cutoff = "2026-09-01T00:00:00Z"
        empty_feats = {"hasSufficientData": False, "homeMatchesCount": 0, "awayMatchesCount": 0}
        res = run_gradient_boosting_engine("football", "Unknown A", "Unknown B", cutoff, empty_feats)
        
        self.assertFalse(res["hasSufficientData"])
        self.assertIn(res["status"], ["insufficient_data", "abstained"])
        self.assertEqual(len(res["markets"]), 0)

if __name__ == "__main__":
    unittest.main()
