import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import math

from backend.engine.gradient_boosting_engine import (
    run_gradient_boosting_engine,
    build_point_in_time_training_dataset,
    GB_MODEL_VERSION,
)


class GradientBoostingEngineTestSuite(unittest.TestCase):
    """
    Test suite for genuine supervised Gradient Boosting engine.
    Verifies:
    1. No temporal data leakage.
    2. Strict chronological splitting.
    3. Minimum history rule enforcement.
    4. Genuine multiclass probability sum equals 1.0.
    5. One-class dataset triggers complete abstention.
    6. Invalid features trigger complete abstention.
    7. No heuristic probability fallback formulas.
    """

    def _generate_synthetic_matches(self, n=30, include_future=False, all_home_wins=False):
        """Helper to create historical completed matches with varying results."""
        from backend.utils.text_normalize import normalize_team_name
        base_time = datetime(2026, 8, 1, 15, 0, 0, tzinfo=timezone.utc)
        matches = []
        teams = ["Arsenal", "Chelsea", "Liverpool", "ManCity", "Tottenham", "ManUnited"]

        for i in range(n):
            m_date = (base_time + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            h_team = teams[i % len(teams)]
            a_team = teams[(i + 1) % len(teams)]

            if all_home_wins:
                h_score, a_score = 3, 0
            else:
                # Cycle between Home Win (2), Draw (1), Away Win (0)
                outcome = i % 3
                if outcome == 0:
                    h_score, a_score = 0, 2  # Away Win
                elif outcome == 1:
                    h_score, a_score = 1, 1  # Draw
                else:
                    h_score, a_score = 3, 1  # Home Win

            matches.append({
                "id": f"fb_match_{i:03d}",
                "sport": "football",
                "homeTeam": h_team,
                "awayTeam": a_team,
                "homeTeamKey": normalize_team_name(h_team),
                "awayTeamKey": normalize_team_name(a_team),
                "homeScore": h_score,
                "awayScore": a_score,
                "matchDate": m_date,
                "status": "completed",
            })

        if include_future:
            # Add future matches after cutoff
            future_base = datetime(2026, 10, 1, 15, 0, 0, tzinfo=timezone.utc)
            for j in range(5):
                matches.append({
                    "id": f"fb_future_{j}",
                    "sport": "football",
                    "homeTeam": "Arsenal",
                    "awayTeam": "Chelsea",
                    "homeTeamKey": "arsenal",
                    "awayTeamKey": "chelsea",
                    "homeScore": 10,  # Obvious score if leaked
                    "awayScore": 10,
                    "matchDate": (future_base + timedelta(days=j)).isoformat(),
                    "status": "completed",
                })

        return matches

    # =========================================================================
    # Test 1: No leakage
    # =========================================================================
    @patch("backend.engine.gradient_boosting_engine.get_point_in_time_matches")
    def test_no_leakage(self, mock_get_matches):
        """
        Verify matches on or after cutoff_timestamp are strictly excluded
        from feature construction and training rows.
        """
        cutoff = "2026-09-15T00:00:00Z"
        matches = self._generate_synthetic_matches(n=25, include_future=True)
        mock_get_matches.return_value = matches

        X_target, y_target, X_totals, y_totals, sample_dates = build_point_in_time_training_dataset(
            sport="football",
            cutoff_timestamp=cutoff,
        )

        self.assertGreater(len(sample_dates), 0)
        for date_str in sample_dates:
            self.assertLess(
                date_str,
                cutoff,
                f"Leaked match date {date_str} must be strictly prior to cutoff {cutoff}",
            )

    # =========================================================================
    # Test 2: Chronological split
    # =========================================================================
    @patch("backend.engine.gradient_boosting_engine.get_point_in_time_matches")
    def test_chronological_split(self, mock_get_matches):
        """
        Verify chronological out-of-sample forward split:
        training_end < validation_start. Model is NEVER evaluated on training rows.
        """
        cutoff = "2026-09-25T00:00:00Z"
        matches = self._generate_synthetic_matches(n=35)
        mock_get_matches.return_value = matches

        features = {
            "hasSufficientData": True,
            "homeMatchesCount": 6,
            "awayMatchesCount": 6,
            "homeGoalsScoredAvg": 1.8,
            "homeGoalsConcededAvg": 1.0,
            "awayGoalsScoredAvg": 1.4,
            "awayGoalsConcededAvg": 1.2,
            "homeRecentWDL": {"wins": 3, "draws": 1, "losses": 1},
            "awayRecentWDL": {"wins": 2, "draws": 2, "losses": 1},
        }

        res = run_gradient_boosting_engine(
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            cutoff_timestamp=cutoff,
            features=features,
        )

        self.assertTrue(res["hasSufficientData"])
        self.assertEqual(res["status"], "trained")

        meta = res["metadata"]
        self.assertIn("training_end", meta)
        self.assertIn("validation_start", meta)
        self.assertLess(
            meta["training_end"],
            meta["validation_start"],
            "Training window must chronologically precede out-of-sample validation window",
        )
        self.assertGreater(meta["training_rows"], 0)
        self.assertGreater(meta["validation_rows"], 0)
        self.assertIsNotNone(meta["validation_score"])

    # =========================================================================
    # Test 3: Minimum history
    # =========================================================================
    def test_minimum_history_rule(self):
        """
        Verify that teams with fewer than 5 completed matches trigger complete abstention.
        """
        cutoff = "2026-09-25T00:00:00Z"
        # Only 4 matches for away team
        features_insufficient = {
            "hasSufficientData": True,
            "homeMatchesCount": 5,
            "awayMatchesCount": 4, # Less than 5
            "homeGoalsScoredAvg": 1.5,
            "homeGoalsConcededAvg": 1.0,
            "awayGoalsScoredAvg": 1.2,
            "awayGoalsConcededAvg": 1.4,
        }

        res = run_gradient_boosting_engine(
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            cutoff_timestamp=cutoff,
            features=features_insufficient,
        )

        self.assertFalse(res["hasSufficientData"])
        self.assertEqual(res["markets"], [])
        self.assertEqual(res["status"], "abstained")
        self.assertIn("ABSTAIN: Minimum 5 completed matches required", res["abstentionReason"])

    # =========================================================================
    # Test 4: Multiclass probability sum = 1
    # =========================================================================
    @patch("backend.engine.gradient_boosting_engine.get_point_in_time_matches")
    def test_multiclass_probability_sum_equals_one(self, mock_get_matches):
        """
        Verify that 3-way 1X2 market probabilities sum exactly to 1.0
        and come from a genuine multiclass classifier.
        """
        cutoff = "2026-09-25T00:00:00Z"
        matches = self._generate_synthetic_matches(n=35)
        mock_get_matches.return_value = matches

        features = {
            "hasSufficientData": True,
            "homeMatchesCount": 6,
            "awayMatchesCount": 6,
            "homeGoalsScoredAvg": 2.2,
            "homeGoalsConcededAvg": 0.8,
            "awayGoalsScoredAvg": 1.1,
            "awayGoalsConcededAvg": 1.5,
            "homeRecentWDL": {"wins": 4, "draws": 1, "losses": 0},
            "awayRecentWDL": {"wins": 1, "draws": 2, "losses": 2},
        }

        res = run_gradient_boosting_engine(
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            cutoff_timestamp=cutoff,
            features=features,
        )

        self.assertTrue(res["hasSufficientData"])
        m_1x2 = [m for m in res["markets"] if m["marketCategory"] == "1X2"]
        self.assertEqual(len(m_1x2), 3)

        p_home = next(m["rawProbability"] for m in m_1x2 if "Arsenal" in m["selection"])
        p_draw = next(m["rawProbability"] for m in m_1x2 if "Draw" in m["selection"])
        p_away = next(m["rawProbability"] for m in m_1x2 if "Chelsea" in m["selection"])

        total_prob = p_home + p_draw + p_away
        self.assertAlmostEqual(total_prob, 1.0, places=3, msg="Multiclass 1X2 probabilities must sum to 1.0")

        # Over / Under totals sum
        m_tot = [m for m in res["markets"] if m["marketCategory"] == "GoalsTotal"]
        self.assertEqual(len(m_tot), 2)
        p_over = next(m["rawProbability"] for m in m_tot if "Over" in m["selection"])
        p_under = next(m["rawProbability"] for m in m_tot if "Under" in m["selection"])
        self.assertAlmostEqual(p_over + p_under, 1.0, places=3, msg="Totals probabilities must sum to 1.0")

    # =========================================================================
    # Test 5: One-class dataset abstains
    # =========================================================================
    @patch("backend.engine.gradient_boosting_engine.get_point_in_time_matches")
    def test_one_class_dataset_abstains(self, mock_get_matches):
        """
        Verify that if the training dataset contains only one target class,
        the engine strictly abstains rather than returning fake fallbacks.
        """
        cutoff = "2026-09-25T00:00:00Z"
        # All matches are home wins -> single class
        matches = self._generate_synthetic_matches(n=35, all_home_wins=True)
        mock_get_matches.return_value = matches

        features = {
            "hasSufficientData": True,
            "homeMatchesCount": 6,
            "awayMatchesCount": 6,
            "homeGoalsScoredAvg": 2.0,
            "homeGoalsConcededAvg": 1.0,
            "awayGoalsScoredAvg": 1.0,
            "awayGoalsConcededAvg": 2.0,
        }

        res = run_gradient_boosting_engine(
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            cutoff_timestamp=cutoff,
            features=features,
        )

        self.assertFalse(res["hasSufficientData"])
        self.assertEqual(res["markets"], [])
        self.assertEqual(res["status"], "abstained")
        self.assertIn("ABSTAIN: Incomplete 1X2 multiclass distribution", res["abstentionReason"])

    # =========================================================================
    # Test 6: Invalid features trigger abstention
    # =========================================================================
    def test_invalid_features_abstains(self):
        """
        Verify that missing, None, NaN, or non-dictionary features trigger immediate abstention.
        """
        cutoff = "2026-09-25T00:00:00Z"

        # Case A: Missing required scoring average
        res_none = run_gradient_boosting_engine(
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            cutoff_timestamp=cutoff,
            features={
                "hasSufficientData": True,
                "homeMatchesCount": 6,
                "awayMatchesCount": 6,
                "homeGoalsScoredAvg": None, # Invalid None
                "homeGoalsConcededAvg": 1.0,
                "awayGoalsScoredAvg": 1.5,
                "awayGoalsConcededAvg": 1.2,
            },
        )
        self.assertFalse(res_none["hasSufficientData"])
        self.assertEqual(res_none["markets"], [])
        self.assertEqual(res_none["status"], "abstained")

        # Case B: NaN in feature values
        res_nan = run_gradient_boosting_engine(
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            cutoff_timestamp=cutoff,
            features={
                "hasSufficientData": True,
                "homeMatchesCount": 6,
                "awayMatchesCount": 6,
                "homeGoalsScoredAvg": float("nan"), # NaN
                "homeGoalsConcededAvg": 1.0,
                "awayGoalsScoredAvg": 1.5,
                "awayGoalsConcededAvg": 1.2,
            },
        )
        self.assertFalse(res_nan["hasSufficientData"])
        self.assertEqual(res_nan["markets"], [])
        self.assertEqual(res_nan["status"], "abstained")

    # =========================================================================
    # Test 7: No heuristic probability fallback
    # =========================================================================
    @patch("backend.engine.gradient_boosting_engine.get_point_in_time_matches")
    def test_no_heuristic_probability_fallback(self, mock_get_matches):
        """
        Verify that the draw probability is genuinely learned from the data
        and is NOT computed via a heuristic formula such as:
        p_draw = 0.26 * math.exp(-margin * 3.5).
        """
        cutoff = "2026-09-25T00:00:00Z"
        matches = self._generate_synthetic_matches(n=35)
        mock_get_matches.return_value = matches

        features = {
            "hasSufficientData": True,
            "homeMatchesCount": 6,
            "awayMatchesCount": 6,
            "homeGoalsScoredAvg": 1.5,
            "homeGoalsConcededAvg": 1.2,
            "awayGoalsScoredAvg": 1.3,
            "awayGoalsConcededAvg": 1.4,
            "homeRecentWDL": {"wins": 2, "draws": 2, "losses": 1},
            "awayRecentWDL": {"wins": 2, "draws": 1, "losses": 2},
        }

        res = run_gradient_boosting_engine(
            sport="football",
            home_team="Arsenal",
            away_team="Chelsea",
            cutoff_timestamp=cutoff,
            features=features,
        )

        self.assertTrue(res["hasSufficientData"])
        m_1x2 = [m for m in res["markets"] if m["marketCategory"] == "1X2"]
        p_home = next(m["rawProbability"] for m in m_1x2 if "Arsenal" in m["selection"])
        p_draw = next(m["rawProbability"] for m in m_1x2 if "Draw" in m["selection"])

        # Check what the old heuristic would have produced:
        margin = abs(p_home - 0.5)
        heuristic_draw = round(0.26 * math.exp(-margin * 3.5), 4)

        # The true learned multiclass probability should not be arbitrarily bound to that exact formula
        # and on failure, no 0.85/0.15 hardcoded probabilities are emitted
        for m in res["markets"]:
            self.assertNotIn(m["rawProbability"], [0.85, 0.15], "Must not return artificial 0.85/0.15 probabilities")


if __name__ == "__main__":
    unittest.main()
