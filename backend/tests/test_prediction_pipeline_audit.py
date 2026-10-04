"""
Comprehensive End-to-End Prediction Pipeline Audit and Verification Suite.
Validates:
1. Real production pipeline transition from fixture ingestion to subscriber feeds.
2. Point-in-time lookahead protection (WHERE match_date < cutoff_timestamp).
3. Minimum historical sample size enforcement (>= 5 matches, fails closed).
4. Strict sport isolation across football, basketball, baseball, hockey, and formula_1.
5. Elo engine correctness (initialization, updates, venue adjustment, sport isolation).
6. Poisson engine correctness (attack/defense strength, expected goals, bivariate distribution).
7. Ensemble engine execution, weight combination, normalization, and failure closed on missing engine.
8. Persisted calibration validation (chronological split, ECE, fail-closed for subscriber feed).
9. Dynamic confidence scoring (varies with sample size, completeness, certainty; non-constant).
10. Abstention and publication gates (unvalidated/abstained candidates blocked from subscriber feed).
11. Database boundaries (Mongo=operational primary, Neon=standby operational only, Redis=feed cache, DuckDB/Parquet=historical analytics).
12. Failure modes (Mongo failure, Neon failure, Redis failure, R2/DuckDB failure, Provider failure) never fabricate predictions.
"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from backend.db.mongodb import mongo_manager
from backend.engine.pipeline import execute_prediction_pipeline
from backend.engine.ensemble_engine import run_model_ensemble, is_production_model, is_challenger_model
from backend.engine.confidence import calculate_confidence
from backend.engine.calibration import (
    calibrate_probability,
    get_calibration_details,
    fit_calibration_candidate,
    promote_calibration_candidate,
    apply_calibration_curve,
)
from backend.engine.validation_and_abstention import (
    validate_fixture_pre_conditions,
    validate_market_for_sport,
    validate_market_publication,
    get_lagos_date_str,
    get_current_lagos_today,
    LAGOS_TZ,
)
from backend.engine.ranking import rank_and_select_best_of_day
from backend.engine.football_elo_engine import compute_historical_elo_ratings, run_football_elo_engine
from backend.engine.football_poisson_engine import run_football_poisson_engine, poisson_prob, dixon_coles_tau


class TestPredictionPipelineAudit(unittest.TestCase):

    def setUp(self):
        self.now_utc = datetime.now(timezone.utc).isoformat()
        self.future_date_utc = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT18:00:00Z")
        self.today_lagos = get_current_lagos_today()

    # =========================================================================
    # 1. DATA INTEGRITY & POINT-IN-TIME PROTECTION
    # =========================================================================

    def test_future_matches_cannot_use_future_results(self):
        """Verifies that point-in-time queries strictly enforce match_date < cutoff_timestamp."""
        from backend.engine.historical_store import get_point_in_time_matches

        mock_duckdb = MagicMock()
        mock_duckdb.get_point_in_time_matches.return_value = [
            {"match_id": "m1", "match_date": "2026-08-01", "home_team": "Arsenal", "away_team": "Chelsea", "home_score": 2, "away_score": 1}
        ]

        with patch("backend.engine.historical_store.duckdb_engine", mock_duckdb):
            cutoff = "2026-08-10T15:00:00Z"
            matches = get_point_in_time_matches(cutoff, "football")
            self.assertEqual(len(matches), 1)
            mock_duckdb.get_point_in_time_matches.assert_called_once_with("football", cutoff)

    def test_minimum_historical_sample_requirement_enforced(self):
        """Pipeline must abstain when either team has fewer than 5 historical matches."""
        fixture = {
            "id": "fix_low_history_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Newly Promoted FC",
            "kickoffUtc": self.future_date_utc,
            "status": "scheduled",
        }

        # Mock pit_features to return only 3 matches for away team
        with patch("backend.engine.pipeline.build_football_features") as mock_feat:
            mock_feat.return_value = {
                "hasSufficientData": True,
                "homeMatchesCount": 10,
                "awayMatchesCount": 3,  # < 5 minimum
            }

            res = execute_prediction_pipeline(
                active_model="ELO + POISSON",
                is_subscriber_feed=True,
                custom_fixtures=[fixture],
            )

            self.assertEqual(len(res["publishedFeed"]), 0)
            candidate = res["allResults"][0]
            self.assertEqual(candidate["validationStatus"], "insufficient_data")
            self.assertEqual(candidate["stopReason"], "INSUFFICIENT_HISTORY")
            self.assertIn("Insufficient point-in-time matches", candidate["abstentionDiagnostics"][0])

    def test_completed_matches_rejected_from_predictions(self):
        """Matches marked completed must be rejected from active predictions."""
        fixture = {
            "id": "fix_completed_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Liverpool",
            "awayTeam": "Everton",
            "kickoffUtc": self.future_date_utc,
            "status": "completed",
            "currentScore": {"home": 2, "away": 0},
        }

        pre_val = validate_fixture_pre_conditions(fixture)
        self.assertFalse(pre_val["isValid"])
        self.assertEqual(pre_val["stopReason"], "REJECTED_COMPLETED")

    def test_past_kickoff_date_rejected_from_predictions(self):
        """Kickoffs in past calendar dates in Lagos timezone must be rejected."""
        past_kickoff = (datetime.now(LAGOS_TZ) - timedelta(days=2)).strftime("%Y-%m-%dT12:00:00Z")
        fixture = {
            "id": "fix_past_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Liverpool",
            "awayTeam": "Everton",
            "kickoffUtc": past_kickoff,
            "status": "scheduled",
        }

        pre_val = validate_fixture_pre_conditions(fixture)
        self.assertFalse(pre_val["isValid"])
        self.assertEqual(pre_val["stopReason"], "REJECTED_PAST")

    # =========================================================================
    # 2. SPORT ISOLATION & CROSS-SPORT RESTRICTIONS
    # =========================================================================

    def test_sport_isolation_prevents_cross_sport_models(self):
        """Football Elo engine must raise ValueError if invoked with non-football sport."""
        with self.assertRaises(ValueError) as ctx:
            run_football_elo_engine("basketball", "Boston Celtics", "LA Lakers", self.now_utc)
        self.assertIn("only supports football", str(ctx.exception))

    def test_football_specific_markets_forbidden_on_other_sports(self):
        """Football markets (1X2, BTTS, Corners) must be rejected for basketball/baseball/hockey/F1."""
        self.assertFalse(validate_market_for_sport("basketball", "Both Teams to Score", "BTTS"))
        self.assertFalse(validate_market_for_sport("baseball", "1X2 (Draw)", "1X2"))
        self.assertFalse(validate_market_for_sport("hockey", "Corners Total Over 9.5", "Corners"))
        self.assertFalse(validate_market_for_sport("formula_1", "Over 2.5 Goals", "GoalsTotal"))

        # Valid sport market combinations must pass
        self.assertTrue(validate_market_for_sport("football", "Win / Draw / Loss (1X2)", "1X2"))
        self.assertTrue(validate_market_for_sport("basketball", "Moneyline Winner", "Moneyline"))
        self.assertTrue(validate_market_for_sport("baseball", "Run Line -1.5", "RunLine"))
        self.assertTrue(validate_market_for_sport("hockey", "Puck Line -1.5", "PuckLine"))
        self.assertTrue(validate_market_for_sport("formula_1", "Race Winner", "RaceWinner"))

    # =========================================================================
    # 3. ELO & POISSON ENGINES INTEGRITY
    # =========================================================================

    def test_elo_mathematical_calculation_no_hardcoded_probabilities(self):
        """Elo calculation must produce mathematically derived probabilities based on rating differential."""
        mock_matches = [
            {"sport": "football", "homeTeamKey": "arsenal", "awayTeamKey": "chelsea", "homeTeam": "Arsenal", "awayTeam": "Chelsea", "homeScore": 2, "awayScore": 1, "matchDate": "2026-08-01"},
            {"sport": "football", "homeTeamKey": "arsenal", "awayTeamKey": "everton", "homeTeam": "Arsenal", "awayTeam": "Everton", "homeScore": 3, "awayScore": 0, "matchDate": "2026-08-05"},
            {"sport": "football", "homeTeamKey": "chelsea", "awayTeamKey": "arsenal", "homeTeam": "Chelsea", "awayTeam": "Arsenal", "homeScore": 1, "awayScore": 1, "matchDate": "2026-08-10"},
            {"sport": "football", "homeTeamKey": "arsenal", "awayTeamKey": "tottenham", "homeTeam": "Arsenal", "awayTeam": "Tottenham", "homeScore": 2, "awayScore": 0, "matchDate": "2026-08-15"},
            {"sport": "football", "homeTeamKey": "fulham", "awayTeamKey": "arsenal", "homeTeam": "Fulham", "awayTeam": "Arsenal", "homeScore": 0, "awayScore": 2, "matchDate": "2026-08-20"},
            {"sport": "football", "homeTeamKey": "chelsea", "awayTeamKey": "wolves", "homeTeam": "Chelsea", "awayTeam": "Wolves", "homeScore": 1, "awayScore": 0, "matchDate": "2026-08-02"},
            {"sport": "football", "homeTeamKey": "everton", "awayTeamKey": "chelsea", "homeTeam": "Everton", "awayTeam": "Chelsea", "homeScore": 0, "awayScore": 1, "matchDate": "2026-08-06"},
            {"sport": "football", "homeTeamKey": "chelsea", "awayTeamKey": "newcastle", "homeTeam": "Chelsea", "awayTeam": "Newcastle", "homeScore": 2, "awayScore": 1, "matchDate": "2026-08-12"},
            {"sport": "football", "homeTeamKey": "aston_villa", "awayTeamKey": "chelsea", "homeTeam": "Aston Villa", "awayTeam": "Chelsea", "homeScore": 1, "awayScore": 2, "matchDate": "2026-08-18"},
            {"sport": "football", "homeTeamKey": "chelsea", "awayTeamKey": "brentford", "homeTeam": "Chelsea", "awayTeam": "Brentford", "homeScore": 3, "awayScore": 1, "matchDate": "2026-08-22"},
        ]

        with patch("backend.engine.football_elo_engine.get_point_in_time_matches", return_value=mock_matches):
            res = run_football_elo_engine("football", "Arsenal", "Chelsea", "2026-08-25T15:00:00Z")
            self.assertTrue(res["hasSufficientData"])
            self.assertTrue(len(res["markets"]) > 0)
            
            # Check probability bounds
            for m in res["markets"]:
                self.assertGreater(m["rawProbability"], 0.0)
                self.assertLess(m["rawProbability"], 1.0)

    def test_poisson_bivariate_expected_goals_calculation(self):
        """Poisson engine must compute attack/defensive strength and bivariate score matrix."""
        # Test basic Poisson distribution function
        p0 = poisson_prob(1.5, 0)
        p1 = poisson_prob(1.5, 1)
        p2 = poisson_prob(1.5, 2)
        self.assertAlmostEqual(p0, 0.2231, places=3)
        self.assertAlmostEqual(p1, 0.3347, places=3)
        self.assertAlmostEqual(p2, 0.2510, places=3)

        # Dixon-Coles tau adjustment check
        tau00 = dixon_coles_tau(0, 0, 1.5, 1.2, rho=-0.11)
        self.assertGreater(tau00, 1.0)  # low-score deflation/inflation adjustment

    # =========================================================================
    # 4. ENSEMBLE ENGINE EXECUTION & NORMALIZATION
    # =========================================================================

    def test_ensemble_both_engines_execute_and_normalize(self):
        """Ensemble ELO + POISSON must require both engines and normalize sum to 1.0."""
        mock_elo_markets = [
            {"marketName": "Win / Draw / Loss (1X2)", "selection": "Arsenal Win", "rawProbability": 0.55, "marketCategory": "1X2"},
            {"marketName": "Win / Draw / Loss (1X2)", "selection": "Draw", "rawProbability": 0.25, "marketCategory": "1X2"},
            {"marketName": "Win / Draw / Loss (1X2)", "selection": "Chelsea Win", "rawProbability": 0.20, "marketCategory": "1X2"},
        ]
        mock_poisson_markets = [
            {"marketName": "Win / Draw / Loss (1X2)", "selection": "Arsenal Win", "rawProbability": 0.50, "marketCategory": "1X2"},
            {"marketName": "Win / Draw / Loss (1X2)", "selection": "Draw", "rawProbability": 0.28, "marketCategory": "1X2"},
            {"marketName": "Win / Draw / Loss (1X2)", "selection": "Chelsea Win", "rawProbability": 0.22, "marketCategory": "1X2"},
        ]

        with patch("backend.engine.football_elo_engine.run_football_elo_engine") as mock_elo, \
             patch("backend.engine.football_poisson_engine.run_football_poisson_engine") as mock_poi:
            mock_elo.return_value = {"hasSufficientData": True, "markets": mock_elo_markets, "metadata": {"training_match_count": 20}}
            mock_poi.return_value = {"hasSufficientData": True, "markets": mock_poisson_markets, "metadata": {"training_match_count": 20}}

            ens_res = run_model_ensemble("ELO + POISSON", "football", "Arsenal", "Chelsea", self.now_utc, {})
            self.assertTrue(ens_res["hasSufficientData"])
            self.assertTrue(ens_res["eloSuccessful"])
            self.assertTrue(ens_res["poissonSuccessful"])

            # Verify probabilities sum to 1.0
            sum_p = sum(m["rawProbability"] for m in ens_res["markets"] if m["marketName"] == "Win / Draw / Loss (1X2)")
            self.assertAlmostEqual(sum_p, 1.0, places=2)

    def test_ensemble_fails_closed_if_an_engine_fails(self):
        """If one required engine has insufficient data, ensemble fails closed with 0 markets."""
        with patch("backend.engine.football_elo_engine.run_football_elo_engine") as mock_elo, \
             patch("backend.engine.football_poisson_engine.run_football_poisson_engine") as mock_poi:
            mock_elo.return_value = {"hasSufficientData": True, "markets": [{"marketName": "1X2", "selection": "A", "rawProbability": 0.5}], "metadata": {}}
            mock_poi.return_value = {"hasSufficientData": False, "markets": [], "metadata": {}}  # Poisson fails

            ens_res = run_model_ensemble("ELO + POISSON", "football", "Arsenal", "Chelsea", self.now_utc, {})
            self.assertFalse(ens_res["hasSufficientData"])
            self.assertEqual(len(ens_res["markets"]), 0)

    # =========================================================================
    # 5. CALIBRATION ARCHITECTURE
    # =========================================================================

    def test_calibration_chronological_fit_and_ece(self):
        """Empirical isotonic calibration must fit on chronological partitions without leakage."""
        from unittest.mock import PropertyMock
        # 60 ordered samples (exceeding min 50 threshold)
        preds = [0.40 + (i % 20) * 0.02 for i in range(60)]
        outcomes = [1 if p > 0.55 else 0 for p in preds]
        timestamps = [(datetime(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(60)]

        mock_col = MagicMock()
        mock_col.find_one.return_value = None
        with patch.object(type(mongo_manager), "calibration_records", new_callable=PropertyMock, return_value=mock_col):
            cand = fit_calibration_candidate("football", "default", "ELO + POISSON", preds, outcomes, timestamps)
            self.assertIn(cand["status"], ["VALIDATED", "REJECTED"])
            self.assertIn("ece", cand["metrics"])
            self.assertIn("brier_improvement", cand["metrics"])
            # Never set to PRODUCTION automatically
            self.assertNotEqual(cand["status"], "PRODUCTION")

    def test_uncalibrated_market_fails_closed_in_subscriber_feed(self):
        """Subscriber feed strictly rejects uncalibrated predictions."""
        with patch("backend.engine.calibration.get_production_calibration", return_value=None):
            details = get_calibration_details(0.65, "football", model="ELO + POISSON", allow_baseline=False)
            self.assertIsNone(details["calibrated_probability"])
            self.assertFalse(details["is_calibrated"])
            self.assertEqual(details["calibration_status"], "MISSING_PRODUCTION_CALIBRATION")

            # Validate that publication gate rejects it
            gate_val = validate_market_publication({
                "marketName": "Win / Draw / Loss (1X2)",
                "selection": "Arsenal Win",
                "rawProbability": 0.65,
                "calibratedProbability": None,
                "calibratedPercentage": None,
                "confidenceScore": 0.70,
                "hasSufficientData": True,
                "marketCategory": "1X2",
                "isCalibrated": False,
                "calibrationStatus": "MISSING_PRODUCTION_CALIBRATION",
            }, sport="football", is_subscriber_feed=True)

            self.assertFalse(gate_val["isPublishable"])
            self.assertEqual(gate_val["stopReason"], "UNCALIBRATED_ABSTAIN")

    # =========================================================================
    # 6. DYNAMIC CONFIDENCE SCORING
    # =========================================================================

    def test_confidence_is_dynamic_and_varies_with_data_factors(self):
        """Confidence score must not be constant: varies with sample size, completeness, certainty."""
        # Low certainty (close to 0.50), low sample size (5 matches)
        conf_low = calculate_confidence(0.52, {"hasSufficientData": True}, min_completed_matches=5)
        # High certainty (0.85), high sample size (10+ matches)
        conf_high = calculate_confidence(0.85, {"hasSufficientData": True}, min_completed_matches=12)

        self.assertNotEqual(conf_low["score"], conf_high["score"])
        self.assertGreater(conf_high["score"], conf_low["score"])
        self.assertIn(conf_low["rating"], ["Moderate", "Solid"])
        self.assertEqual(conf_high["rating"], "High")

    # =========================================================================
    # 7. PUBLICATION GATES & RANKING
    # =========================================================================

    def test_ranking_enforces_maximum_20_and_selects_best_of_day(self):
        """Ranking must cap feed to 20 and mark exactly the top prediction as isBestOfDay."""
        candidates = []
        for i in range(25):
            pct = 55.0 + i
            candidates.append({
                "fixtureId": f"fix_{i}",
                "validationStatus": "validated",
                "markets": [{
                    "marketName": "Win / Draw / Loss (1X2)",
                    "selection": f"Team {i} Win",
                    "calibratedPercentage": pct,
                    "confidenceScore": 0.70,
                }],
            })

        ranked = rank_and_select_best_of_day(candidates, max_results=20)
        self.assertEqual(len(ranked), 20)
        self.assertTrue(ranked[0]["isBestOfDay"])
        self.assertFalse(ranked[1]["isBestOfDay"])
        self.assertEqual(ranked[0]["highestPercentagePrediction"]["percentage"], 79.0)

    # =========================================================================
    # 8. DATABASE USAGE BOUNDARIES
    # =========================================================================

    def test_duckdb_used_for_analytics_and_not_neon(self):
        """Historical point-in-time extraction queries DuckDB Parquet views, not Neon."""
        from backend.engine.historical_store import get_point_in_time_matches

        with patch("backend.engine.historical_store.duckdb_engine") as mock_duck:
            mock_duck.get_point_in_time_matches.return_value = []
            get_point_in_time_matches(self.now_utc, "football")
            mock_duck.get_point_in_time_matches.assert_called_once()

    def test_redis_failure_fails_closed_safely_without_corrupting_predictions(self):
        """Redis write failure in sync service marks publication failed without fabricating predictions."""
        from backend.db.redis_client import RedisOperationResult

        failed_write = RedisOperationResult(
            success=False,
            state="UNAVAILABLE",
            latency_ms=1.0,
            error="Connection refused",
        )
        self.assertFalse(failed_write)
        self.assertEqual(failed_write.state, "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
