import unittest
from unittest.mock import AsyncMock, patch

try:
    from fastapi.testclient import TestClient
    from backend.app import app
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    TestClient = None
    app = None

from backend.engine.feature_builders import build_football_features


@unittest.skipIf(not HAS_FASTAPI, "fastapi not installed")
class GoalPredictionsDataRegressionTestSuite(unittest.TestCase):
    """
    Regression test suite for Goal Predictions data integrity.
    Verifies that:
    1. awayAvgScored is populated strictly from awayGoalsScoredAvg.
    2. awayAvgScored is NEVER populated from awayGoalsConcededAvg.
    3. bothTeamsScoredRecentRate is populated from real calculated feature when available.
    4. Missing statistics remain None (unavailable) rather than fabricated.
    """

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    # =========================================================================
    # Test 1 & 2: awayAvgScored uses awayGoalsScoredAvg and NOT awayGoalsConcededAvg
    # =========================================================================
    def test_away_avg_scored_comes_from_away_goals_scored_avg_not_conceded(self):
        """
        Verify that awayAvgScored = awayGoalsScoredAvg, and is distinctly NOT
        awayGoalsConcededAvg when the two values differ.
        """
        mock_fixture = {
            "id": "match_football_test_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": "2026-09-30T16:30:00Z",
            "status": "upcoming",
            "sportStats": {
                "homeGoalsScoredAvg": 2.40,
                "awayGoalsScoredAvg": 1.60,      # Scored
                "awayGoalsConcededAvg": 0.85,    # Conceded (distinct value)
                "bothTeamsScoredRecentRate": 0.70,
                "xGRecentHome": 2.10,
                "xGRecentAway": 1.30,
            },
            "validatedMarkets": [
                {
                    "id": "m1",
                    "marketName": "Over 2.5 Goals",
                    "selection": "Over 2.5",
                    "probabilityPercentage": 64.5,
                }
            ],
            "validationStatus": "validated",
        }

        with patch("backend.services.feed_service.feed_service.get_feed", new_callable=AsyncMock) as mock_get_feed:
            mock_get_feed.return_value = [mock_fixture]

            resp = self.client.get("/api/predictions/goals")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data), 1)

            goal_item = data[0]

            # 1. awayAvgScored comes from awayGoalsScoredAvg
            self.assertEqual(
                goal_item["awayAvgScored"],
                1.60,
                f"Expected awayAvgScored=1.60 (from awayGoalsScoredAvg), got {goal_item['awayAvgScored']}",
            )

            # 2. awayAvgScored is NOT populated from awayGoalsConcededAvg
            self.assertNotEqual(
                goal_item["awayAvgScored"],
                0.85,
                "awayAvgScored must NOT equal awayGoalsConcededAvg (0.85)",
            )

            # Home avg scored matches
            self.assertEqual(goal_item["homeAvgScored"], 2.40)

    # =========================================================================
    # Test 3: BTTS recent rate is returned when available
    # =========================================================================
    def test_btts_recent_rate_is_returned_when_available(self):
        """
        Verify that bothTeamsScoredRecentRate is populated from real feature/statistics object.
        """
        mock_fixture = {
            "id": "match_football_test_2",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Liverpool",
            "awayTeam": "Manchester City",
            "kickoffUtc": "2026-09-30T19:00:00Z",
            "status": "upcoming",
            "sportStats": {
                "homeGoalsScoredAvg": 2.10,
                "awayGoalsScoredAvg": 1.90,
                "awayGoalsConcededAvg": 1.10,
                "bothTeamsScoredRecentRate": 0.80, # 80% BTTS in recent matches
                "xGRecentHome": 1.95,
                "xGRecentAway": 1.80,
            },
            "validatedMarkets": [
                {
                    "id": "m2",
                    "marketName": "Both Teams To Score (BTTS)",
                    "selection": "Yes",
                    "probabilityPercentage": 68.0,
                }
            ],
            "validationStatus": "validated",
        }

        with patch("backend.services.feed_service.feed_service.get_feed", new_callable=AsyncMock) as mock_get_feed:
            mock_get_feed.return_value = [mock_fixture]

            resp = self.client.get("/api/predictions/goals")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data), 1)

            goal_item = data[0]
            self.assertEqual(
                goal_item["bothTeamsScoredRecentRate"],
                0.80,
                f"Expected bothTeamsScoredRecentRate=0.80, got {goal_item['bothTeamsScoredRecentRate']}",
            )
            self.assertIsNotNone(goal_item["bothTeamsScoredRecentRate"])

    # =========================================================================
    # Test 4: Missing statistics remain None rather than fabricated
    # =========================================================================
    def test_missing_real_statistics_remain_unavailable(self):
        """
        Missing statistics must remain None (unavailable) rather than being
        fabricated with synthetic defaults or substituting conceded goals.
        """
        mock_fixture_missing = {
            "id": "match_football_test_3",
            "sport": "football",
            "league": "Championship",
            "homeTeam": "Team A",
            "awayTeam": "Team B",
            "kickoffUtc": "2026-10-01T14:00:00Z",
            "status": "upcoming",
            "sportStats": {
                # Statistics missing or insufficient
                "homeGoalsScoredAvg": None,
                "awayGoalsScoredAvg": None,
                "awayGoalsConcededAvg": 1.50, # Even if conceded is present, do NOT substitute!
                "bothTeamsScoredRecentRate": None,
                "xGRecentHome": None,
                "xGRecentAway": None,
            },
            "validatedMarkets": [
                {
                    "id": "m3",
                    "marketName": "Over 1.5 Goals",
                    "selection": "Over 1.5",
                    "probabilityPercentage": 60.0,
                }
            ],
            "validationStatus": "validated",
        }

        with patch("backend.services.feed_service.feed_service.get_feed", new_callable=AsyncMock) as mock_get_feed:
            mock_get_feed.return_value = [mock_fixture_missing]

            resp = self.client.get("/api/predictions/goals")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data), 1)

            goal_item = data[0]

            # Missing away goals scored MUST be None, NOT substituted with 1.50 (conceded)
            self.assertIsNone(
                goal_item["awayAvgScored"],
                f"awayAvgScored must remain None when unavailable, got {goal_item['awayAvgScored']}",
            )

            # Missing BTTS rate MUST be None
            self.assertIsNone(
                goal_item["bothTeamsScoredRecentRate"],
                f"bothTeamsScoredRecentRate must remain None when unavailable, got {goal_item['bothTeamsScoredRecentRate']}",
            )

            # Combined xG is None when either team's xG is missing
            self.assertIsNone(goal_item["xGCombined"])

    # =========================================================================
    # Test 5: Feature Builder calculates real awayGoalsScoredAvg & bothTeamsScoredRecentRate
    # =========================================================================
    def test_feature_builder_pipeline_produces_consistent_feature_names(self):
        """
        Verify that build_football_features computes and returns:
        - awayGoalsScoredAvg
        - awayGoalsConcededAvg
        - bothTeamsScoredRecentRate
        """
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        # Mock point in time matches (6 matches each to satisfy >= 5 gate)
        fake_home_matches = [
            {"homeTeam": "Arsenal", "awayTeam": "Team X", "homeTeamKey": "arsenal", "awayTeamKey": "team_x", "homeScore": 3, "awayScore": 1, "date": "2026-08-01", "homeXg": 2.2, "awayXg": 0.8},
            {"homeTeam": "Team Y", "awayTeam": "Arsenal", "homeTeamKey": "team_y", "awayTeamKey": "arsenal", "homeScore": 0, "awayScore": 2, "date": "2026-08-08", "homeXg": 0.5, "awayXg": 1.9},
            {"homeTeam": "Arsenal", "awayTeam": "Team Z", "homeTeamKey": "arsenal", "awayTeamKey": "team_z", "homeScore": 2, "awayScore": 2, "date": "2026-08-15", "homeXg": 1.8, "awayXg": 1.6},
            {"homeTeam": "Team W", "awayTeam": "Arsenal", "homeTeamKey": "team_w", "awayTeamKey": "arsenal", "homeScore": 1, "awayScore": 1, "date": "2026-08-22", "homeXg": 1.1, "awayXg": 1.4},
            {"homeTeam": "Arsenal", "awayTeam": "Team V", "homeTeamKey": "arsenal", "awayTeamKey": "team_v", "homeScore": 4, "awayScore": 0, "date": "2026-08-29", "homeXg": 2.8, "awayXg": 0.4},
        ]
        fake_away_matches = [
            {"homeTeam": "Chelsea", "awayTeam": "Team A", "homeTeamKey": "chelsea", "awayTeamKey": "team_a", "homeScore": 2, "awayScore": 1, "date": "2026-08-01", "homeXg": 1.8, "awayXg": 0.9},
            {"homeTeam": "Team B", "awayTeam": "Chelsea", "homeTeamKey": "team_b", "awayTeamKey": "chelsea", "homeScore": 2, "awayScore": 3, "date": "2026-08-08", "homeXg": 1.4, "awayXg": 2.1},
            {"homeTeam": "Chelsea", "awayTeam": "Team C", "homeTeamKey": "chelsea", "awayTeamKey": "team_c", "homeScore": 1, "awayScore": 0, "date": "2026-08-15", "homeXg": 1.2, "awayXg": 0.6},
            {"homeTeam": "Team D", "awayTeam": "Chelsea", "homeTeamKey": "team_d", "awayTeamKey": "chelsea", "homeScore": 1, "awayScore": 1, "date": "2026-08-22", "homeXg": 0.9, "awayXg": 1.2},
            {"homeTeam": "Chelsea", "awayTeam": "Team E", "homeTeamKey": "chelsea", "awayTeamKey": "team_e", "homeScore": 0, "awayScore": 1, "date": "2026-08-29", "homeXg": 1.1, "awayXg": 1.3},
        ]

        with patch("backend.engine.feature_builders.get_point_in_time_matches") as mock_pit:
            def pit_side_effect(cutoff, sport, team):
                if team == "Arsenal":
                    return fake_home_matches
                return fake_away_matches

            mock_pit.side_effect = pit_side_effect

            feats = build_football_features("Arsenal", "Chelsea", now_iso)
            self.assertTrue(feats["hasSufficientData"])

            # Verify away goals scored: Chelsea scored 2, 3, 1, 1, 0 -> sum=7 / 5 = 1.4
            self.assertEqual(feats["awayGoalsScoredAvg"], 1.4)

            # Verify away goals conceded: Chelsea conceded 1, 2, 0, 1, 1 -> sum=5 / 5 = 1.0
            self.assertEqual(feats["awayGoalsConcededAvg"], 1.0)

            # awayGoalsScoredAvg and awayGoalsConcededAvg are distinct
            self.assertNotEqual(feats["awayGoalsScoredAvg"], feats["awayGoalsConcededAvg"])

            # bothTeamsScoredRecentRate is calculated and consistent
            self.assertIn("bothTeamsScoredRecentRate", feats)
            self.assertIsNotNone(feats["bothTeamsScoredRecentRate"])
            self.assertEqual(feats["bothTeamsScoredRecentRate"], feats["bttsRate"])


if __name__ == "__main__":
    unittest.main()
