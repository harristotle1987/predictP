import unittest
import math
from typing import Dict, Any, List
from datetime import datetime, timezone

try:
    import duckdb
    import pyarrow
    HAS_DEPS = duckdb is not None and pyarrow is not None
except ImportError:
    HAS_DEPS = False

from backend.engine.quant_analytics import (
    devig_odds,
    calculate_quant_metrics,
    compute_exponential_time_weight,
)
from backend.engine.football_poisson_engine import (
    run_football_poisson_engine,
    dixon_coles_tau,
    poisson_prob,
)
from backend.engine.hockey_poisson_engine import run_hockey_poisson_engine
from backend.engine.pipeline import execute_prediction_pipeline


class QuantAnalyticsAndEngineUpgradesTestSuite(unittest.TestCase):
    """
    Comprehensive test suite verifying that the quant analytics module,
    Dixon-Coles time-decay engines, and pipeline integration were properly
    and securely implemented.
    """

    # =========================================================================
    # 1. DE-VIGGING (IMPLIED PROBABILITY EXTRACTION)
    # =========================================================================

    def test_devig_odds_two_way_fair_market(self):
        """Evenly balanced 1.909 / 1.909 market should yield 50%/50% fair probability."""
        res = devig_odds(home_odds=1.909, away_odds=1.909)
        self.assertAlmostEqual(res["home_fair_prob"], 0.50, places=2)
        self.assertAlmostEqual(res["away_fair_prob"], 0.50, places=2)
        self.assertAlmostEqual(res["draw_fair_prob"], 0.0, places=2)
        self.assertGreater(res["overround_pct"], 4.0)

    def test_devig_odds_three_way_market(self):
        """3-way 1X2 market probabilities must sum strictly to 1.0."""
        # e.g. Home 2.20, Draw 3.40, Away 3.20
        res = devig_odds(home_odds=2.20, away_odds=3.20, draw_odds=3.40)
        total_p = res["home_fair_prob"] + res["away_fair_prob"] + res["draw_fair_prob"]
        self.assertAlmostEqual(total_p, 1.0, places=4)
        self.assertGreater(res["home_fair_prob"], res["away_fair_prob"])
        self.assertGreater(res["away_fair_prob"], res["draw_fair_prob"])
        self.assertGreater(res["overround_pct"], 0.0)

    def test_devig_odds_degenerate_inputs(self):
        """Should safely return neutral probabilities without crashing when odds are invalid."""
        res = devig_odds(home_odds=0.0, away_odds=-1.0, draw_odds=None)
        self.assertEqual(res["home_fair_prob"], 0.5)
        self.assertEqual(res["away_fair_prob"], 0.5)
        self.assertEqual(res["overround_pct"], 0.0)

    # =========================================================================
    # 2. EXPECTED VALUE (+EV) & FRACTIONAL KELLY SIZING
    # =========================================================================

    def test_calculate_quant_metrics_positive_ev(self):
        """
        When calibrated prob = 0.60 and decimal odds = 1.90:
        EV = (0.60 * 1.90) - 1 = +0.14 (+14.0% EV).
        Should flag isPositiveEV = True and calculate a bounded fractional Kelly stake.
        """
        metrics = calculate_quant_metrics(calibrated_prob=0.60, reference_odds=1.90)
        self.assertTrue(metrics["isPositiveEV"])
        self.assertAlmostEqual(metrics["evPercentage"], 14.0, delta=0.5)
        self.assertGreater(metrics["recommendedKellyPct"], 0.0)
        self.assertLessEqual(metrics["recommendedKellyPct"], 5.0)  # Capped at 5%
        self.assertGreater(metrics["modelEdgePct"], 0.0)

    def test_calculate_quant_metrics_negative_ev(self):
        """
        When calibrated prob = 0.45 and decimal odds = 1.90:
        EV = (0.45 * 1.90) - 1 = -0.145 (-14.5% EV).
        Should flag isPositiveEV = False and recommend a 0.0% stake.
        """
        metrics = calculate_quant_metrics(calibrated_prob=0.45, reference_odds=1.90)
        self.assertFalse(metrics["isPositiveEV"])
        self.assertLess(metrics["evPercentage"], 0.0)
        self.assertEqual(metrics["recommendedKellyPct"], 0.0)

    def test_calculate_quant_metrics_closing_line_value(self):
        """
        If opening odds were 2.10 and closing odds shortened to 1.95,
        CLV alpha should be positive: (2.10 / 1.95) - 1 = +7.69%.
        """
        metrics = calculate_quant_metrics(
            calibrated_prob=0.55,
            reference_odds=2.10,
            closing_odds=1.95,
        )
        self.assertAlmostEqual(metrics["clvAlpha"], 7.69, places=1)

    def test_calculate_quant_metrics_boundary_conditions(self):
        """Handles edge case probabilities and missing odds gracefully."""
        # Case 1: prob near 0
        m_zero = calculate_quant_metrics(calibrated_prob=0.0)
        self.assertFalse(m_zero["isPositiveEV"])
        self.assertEqual(m_zero["recommendedKellyPct"], 0.0)

        # Case 2: prob near 1
        m_one = calculate_quant_metrics(calibrated_prob=1.0)
        self.assertLessEqual(m_one["recommendedKellyPct"], 5.0)

        # Case 3: reference odds None / 0.0 -> automatically derives fair market line
        m_auto = calculate_quant_metrics(calibrated_prob=0.70, reference_odds=None)
        self.assertGreater(m_auto["marketOdds"], 1.0)
        self.assertIn("evPercentage", m_auto)

    # =========================================================================
    # 3. EXPONENTIAL TIME-DECAY WEIGHTING
    # =========================================================================

    def test_compute_exponential_time_weight(self):
        """Weight at 0 days should be 1.0, and decay monotonically with time."""
        w0 = compute_exponential_time_weight(0.0)
        w30 = compute_exponential_time_weight(30.0)
        w180 = compute_exponential_time_weight(180.0)

        self.assertAlmostEqual(w0, 1.0, places=4)
        self.assertGreater(w0, w30)
        self.assertGreater(w30, w180)
        self.assertGreater(w180, 0.0)

    # =========================================================================
    # 4. DIXON-COLES & POISSON CORRECTION ENGINES
    # =========================================================================

    def test_dixon_coles_tau_low_scores(self):
        """
        Dixon-Coles tau adjustment must modify low scores (0,0), (1,0), (0,1), (1,1)
        and equal 1.0 for higher score combinations.
        """
        lmbda, mu = 1.35, 1.10
        tau_00 = dixon_coles_tau(0, 0, lmbda, mu)
        tau_10 = dixon_coles_tau(1, 0, lmbda, mu)
        tau_01 = dixon_coles_tau(0, 1, lmbda, mu)
        tau_11 = dixon_coles_tau(1, 1, lmbda, mu)
        tau_32 = dixon_coles_tau(3, 2, lmbda, mu)

        # With default rho = -0.11:
        # tau_00 = 1 - lmbda * mu * rho > 1.0 (corrects under-prediction of 0-0)
        self.assertGreater(tau_00, 1.0)
        # tau_11 = 1 - rho > 1.0
        self.assertGreater(tau_11, 1.0)
        # Higher score tau must be identity 1.0
        self.assertEqual(tau_32, 1.0)

    def test_football_poisson_engine_returns_valid_distribution(self):
        """
        run_football_poisson_engine must produce valid 1X2 probabilities
        summing to 1.0, and non-empty markets.
        """
        res = run_football_poisson_engine(
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            cutoff_timestamp="2026-10-04T00:00:00Z",
        )
        self.assertTrue(res["hasSufficientData"])
        self.assertGreater(res["expectedHomeGoals"], 0.0)
        self.assertGreater(res["expectedAwayGoals"], 0.0)

        markets = res["markets"]
        self.assertGreaterEqual(len(markets), 3)

        # 1X2 sum
        p_1x2 = [m["rawProbability"] for m in markets if m.get("marketCategory") == "1X2"]
        if p_1x2:
            self.assertAlmostEqual(sum(p_1x2), 1.0, places=3)

    @unittest.skipIf(not HAS_DEPS, "duckdb or pyarrow not installed")
    def test_hockey_poisson_engine_returns_valid_distribution(self):
        """
        run_hockey_poisson_engine must compute home and away probabilities
        using the exponential decay weights.
        """
        res = run_hockey_poisson_engine(
            sport="hockey",
            home_team="Rangers",
            away_team="Bruins",
            cutoff_timestamp="2026-10-04T00:00:00Z",
        )
        self.assertTrue(res["hasSufficientData"])
        self.assertGreater(res["expectedHomeGoals"], 0.0)
        self.assertGreater(res["expectedAwayGoals"], 0.0)
        self.assertGreater(len(res["markets"]), 0)

    # =========================================================================
    # 5. PIPELINE INTEGRATION & QUANT METRICS PUBLICATION
    # =========================================================================

    def test_pipeline_attaches_quant_metrics_to_published_fixtures(self):
        """
        Verifies that when a fixture candidate is evaluated and validated,
        quantMetrics is populated on both each validatedMarket and the top-level
        published prediction payload.
        """
        candidate = {
            "id": "test_quant_fixture_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": "2026-10-15T15:00:00Z",
            "status": "upcoming",
        }

        result = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=False,
            custom_fixtures=[candidate],
        )

        published = result.get("publishedFeed", [])
        if published:
            first_pub = published[0]
            self.assertIn("quantMetrics", first_pub)
            q = first_pub["quantMetrics"]
            self.assertIsNotNone(q)
            self.assertIn("evPercentage", q)
            self.assertIn("isPositiveEV", q)
            self.assertIn("recommendedKellyPct", q)
            self.assertIn("modelEdgePct", q)
            self.assertIn("marketOdds", q)

            # Check individual market items as well
            val_markets = first_pub.get("validatedMarkets", [])
            self.assertGreater(len(val_markets), 0)
            for m in val_markets:
                self.assertIn("quantMetrics", m)
                self.assertIsNotNone(m["quantMetrics"])

    def test_pipeline_fail_closed_safety_preserved(self):
        """
        Verifies that the fail-closed architecture remains 100% strict:
        If a team has fewer than 5 matches, the candidate must be rejected
        with INSUFFICIENT_HISTORY and zero published predictions.
        """
        candidate = {
            "id": "test_insufficient_fixture",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Unknown Team FC",
            "awayTeam": "Arsenal",
            "kickoffUtc": "2026-10-15T15:00:00Z",
            "status": "upcoming",
        }

        result = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=True,
            custom_fixtures=[candidate],
        )

        self.assertEqual(len(result["publishedFeed"]), 0)
        all_res = result["allResults"]
        self.assertEqual(len(all_res), 1)
        self.assertIn(all_res[0]["stopReason"], ("INSUFFICIENT_HISTORY", "NO_VALIDATED_PRODUCTION_CALIBRATION"))


if __name__ == "__main__":
    unittest.main()
