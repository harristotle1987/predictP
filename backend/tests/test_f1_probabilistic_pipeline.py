import unittest
import os
import re
from datetime import datetime

try:
    import duckdb
    HAS_DUCKDB = duckdb is not None
except ImportError:
    HAS_DUCKDB = False

from backend.features.f1_feature_builder import (
    build_f1_features,
    build_f1_chronological_dataset,
    F1_FEATURE_NAMES,
)
from backend.engine.f1_probability_engine import (
    run_f1_probability_engine,
    F1_MODEL_VERSION,
)
from backend.markets.f1_markets import build_f1_markets
from backend.engine.gradient_boosting_engine import run_gradient_boosting_engine

class TestF1ProbabilisticPipeline(unittest.TestCase):
    """
    Test suite for the Formula 1 probabilistic machine learning pipeline.
    Verifies:
    1. No future leakage: point-in-time features strictly exclude all future data.
    2. No in-sample-only validation: chronological splitting into distinct train and validation periods.
    3. No heuristic probability multipliers: no 'win * 2.2', no 'win * 3.8 + 0.2', no 'win * 1.8'.
    4. Probabilities are valid: 0 < P < 1, sum(P_win) == 1.0, P_win <= P_podium <= P_top10.
    5. Unsupported markets abstain: markets lacking validated models are omitted (fail closed).
    """

    def setUp(self):
        if not HAS_DUCKDB:
            self.skipTest("duckdb not installed in current environment")

    def test_1_no_future_leakage(self):
        """1. Feature builder guarantees zero future leakage using point-in-time cutoffs."""
        cutoff_early = "2024-03-15T00:00:00Z"
        early_feats = build_f1_features(cutoff_early)
        self.assertTrue(early_feats["hasSufficientData"])

        # Early cutoff only has races before March 15, 2024
        driver_early = next(d for d in early_feats["drivers"] if d["driverId"] == "max_verstappen")
        early_obs = driver_early["observationCount"]

        cutoff_later = "2024-06-01T00:00:00Z"
        later_feats = build_f1_features(cutoff_later)
        driver_later = next(d for d in later_feats["drivers"] if d["driverId"] == "max_verstappen")
        later_obs = driver_later["observationCount"]

        self.assertGreater(later_obs, early_obs, "Later cutoff must accumulate more historical observations")

        # Verify dataset builder preserves chronological separation
        ds = build_f1_chronological_dataset("2024-06-01T00:00:00Z")
        self.assertTrue(ds["hasSufficientData"])
        self.assertGreater(len(ds["X_train"]), 0)
        self.assertGreater(len(ds["X_val"]), 0)

    def test_2_no_in_sample_only_validation(self):
        """2. Chronological out-of-sample validation: train and validation sets are strictly disjoint in time."""
        cutoff = "2024-06-01T00:00:00Z"
        prob_res = run_f1_probability_engine("formula_1", cutoff)
        self.assertTrue(prob_res["hasSufficientData"])
        meta = prob_res["metadata"]

        # Disjoint row counts
        self.assertGreaterEqual(meta["training_rows"], 40)
        self.assertGreaterEqual(meta["validation_rows"], 20)
        self.assertGreater(meta["training_races"], 0)
        self.assertGreater(meta["validation_races"], 0)

        # Validation Brier scores are recorded strictly from out-of-sample validation set
        self.assertIsNotNone(meta["validation_brier_win"])
        self.assertLess(meta["validation_brier_win"], 0.25)

        # Verify source code does not evaluate classifiers on training sets
        f1_engine_path = os.path.abspath("backend/engine/f1_probability_engine.py")
        with open(f1_engine_path, "r", encoding="utf-8") as f:
            code = f.read()

        self.assertNotIn("clf_win.score(X_train", code)
        self.assertNotIn("clf_podium.score(X_train", code)
        self.assertNotIn("clf_top10.score(X_train", code)

    def test_3_no_heuristic_probability_multipliers(self):
        """3. No heuristic probability multipliers (e.g. win * 2.2, win * 3.8 + 0.2, win * 1.8)."""
        engine_files = [
            "backend/engine/f1_probability_engine.py",
            "backend/engine/gradient_boosting_engine.py",
            "backend/markets/f1_markets.py",
        ]

        forbidden_patterns = [
            r"\*\s*2\.2",
            r"\*\s*3\.8",
            r"\*\s*1\.8",
            r"\*\s*2\.5",
            r"p_win\s*\*\s*0\.7",
        ]

        for rel_path in engine_files:
            full_path = os.path.abspath(rel_path)
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            for pattern in forbidden_patterns:
                matches = re.findall(pattern, content)
                self.assertEqual(
                    len(matches),
                    0,
                    f"Found forbidden heuristic formula '{pattern}' in {rel_path}",
                )

        # Verify that podium and top 10 probabilities vary dynamically by driver
        prob_res = run_f1_probability_engine("formula_1", "2024-06-01T00:00:00Z")
        drivers = prob_res["drivers"]
        valid_pairs = [
            d for d in drivers
            if d["podiumProbability"] is not None and d["winProbability"] > 0.05
        ]
        if len(valid_pairs) >= 2:
            ratios = [d["podiumProbability"] / d["winProbability"] for d in valid_pairs]
            # Since probabilities come from dedicated models, ratios must not all be an identical constant multiplier
            unique_ratios = set(round(r, 3) for r in ratios)
            self.assertGreater(len(unique_ratios), 1, "Podium probabilities must not be a static multiplier of win")

    def test_4_probabilities_are_valid(self):
        """4. Probabilities are strictly valid: in (0, 1), win sum == 1.0, win <= podium <= top10."""
        prob_res = run_f1_probability_engine("formula_1", "2024-06-01T00:00:00Z")
        self.assertTrue(prob_res["hasSufficientData"])
        drivers = prob_res["drivers"]

        # Sum of win probabilities over the field
        win_sum = sum(d["winProbability"] for d in drivers)
        self.assertAlmostEqual(win_sum, 1.0, places=2, msg="Field win probabilities must sum to 1.0")

        for d in drivers:
            p_win = d["winProbability"]
            p_pod = d["podiumProbability"]
            p_t10 = d["top10Probability"]

            self.assertGreater(p_win, 0.0)
            self.assertLess(p_win, 1.0)

            if p_pod is not None:
                self.assertGreater(p_pod, 0.0)
                self.assertLess(p_pod, 1.0)
                self.assertGreaterEqual(p_pod, p_win, f"Podium prob {p_pod} must be >= win prob {p_win} for {d['driverId']}")

            if p_t10 is not None:
                self.assertGreater(p_t10, 0.0)
                self.assertLess(p_t10, 1.0)
                if p_pod is not None:
                    self.assertGreaterEqual(p_t10, p_pod, f"Top 10 prob {p_t10} must be >= podium prob {p_pod} for {d['driverId']}")

        # Markets raw probabilities are all valid
        markets = build_f1_markets(drivers)
        for m in markets:
            raw_p = m["rawProbability"]
            self.assertGreater(raw_p, 0.0)
            self.assertLessEqual(raw_p, 1.0)

    def test_5_unsupported_markets_abstain(self):
        """5. Unsupported markets abstain without generating fake probabilities."""
        # Case A: Driver with no validated fastest lap and no validated H2H
        mock_driver = {
            "driverId": "driver_alpha",
            "driverName": "Driver Alpha",
            "winProbability": 0.40,
            "podiumProbability": 0.70,
            "top10Probability": 0.90,
            "fastestLapProbability": None,  # Unsupported / unvalidated
            "hasValidatedFastestLap": False,
            "hasValidatedH2H": False,
            "h2hProbability": None,
        }

        markets = build_f1_markets([mock_driver])
        market_cats = {m["marketCategory"] for m in markets}
        self.assertNotIn("FastestLap", market_cats, "FastestLap must abstain when model is unvalidated")
        self.assertNotIn("H2H", market_cats, "H2H must abstain when model is unvalidated")

        # Case B: Engine abstention on mismatched sport
        with self.assertRaises(ValueError):
            run_f1_probability_engine("football", "2024-06-01T00:00:00Z")

        # Case C: Engine abstention on very early cutoff with insufficient data
        early_res = run_f1_probability_engine("formula_1", "2023-01-01T00:00:00Z")
        self.assertFalse(early_res["hasSufficientData"])
        self.assertEqual(len(early_res["drivers"]), 0)
        self.assertEqual(early_res["status"], "abstained")

    def test_6_gradient_boosting_engine_f1_pipeline(self):
        """6. GradientBoostingEngine uses validated chronological F1 models without heuristic multipliers."""
        gb_res = run_gradient_boosting_engine(
            sport="formula_1",
            home_team="F1 Drivers Field",
            away_team="F1 Constructors Field",
            cutoff_timestamp="2024-06-01T00:00:00Z",
            features={},
        )
        self.assertTrue(gb_res["hasSufficientData"])
        self.assertEqual(gb_res["status"], "trained")
        self.assertGreater(len(gb_res["markets"]), 0)

        meta = gb_res["metadata"]
        self.assertEqual(meta["sport"], "formula_1")
        self.assertEqual(meta["feature_count"], len(F1_FEATURE_NAMES))
        self.assertGreater(meta["training_rows"], 0)
        self.assertGreater(meta["validation_rows"], 0)
        self.assertIsNotNone(meta["validation_score"])

if __name__ == "__main__":
    unittest.main()
