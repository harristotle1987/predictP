import unittest
from backend.engine.pipeline import execute_prediction_pipeline

class TestStatusNormalization(unittest.TestCase):
    def test_scheduled_status_becomes_upcoming(self):
        """
        Regression Test for Step 5:
        Verify that provider/internal operational status 'scheduled'
        is normalized to 'upcoming' in published predictions before reaching React.
        """
        mock_fixtures = [
            {
                "id": "fb_test_101",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": "2026-10-01T15:00:00Z",
                "status": "scheduled",
                "currentScore": {"home": 0, "away": 0, "display": "0 - 0"},
            },
            {
                "id": "bk_test_102",
                "sport": "basketball",
                "league": "NBA Championship",
                "homeTeam": "Los Angeles Lakers",
                "awayTeam": "Golden State Warriors",
                "kickoffUtc": "2026-10-01T20:00:00Z",
                "status": "LIVE",
                "currentScore": {"home": 50, "away": 48, "display": "50 - 48"},
            },
            {
                "id": "bb_test_103",
                "sport": "baseball",
                "league": "Major League Baseball (MLB)",
                "homeTeam": "New York Yankees",
                "awayTeam": "Boston Red Sox",
                "kickoffUtc": "2026-09-20T18:00:00Z",
                "status": "completed",
                "currentScore": {"home": 4, "away": 2, "display": "4 - 2"},
            },
        ]

        result = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=False,
            custom_fixtures=mock_fixtures,
        )

        all_results = result.get("allResults", [])
        self.assertEqual(len(all_results), 3)

        # Check raw 'scheduled' was mapped to 'upcoming'
        fb_res = next(r for r in all_results if r["fixtureId"] == "fb_test_101")
        self.assertEqual(fb_res["status"], "upcoming")

        # Check 'LIVE' was mapped to 'live'
        bk_res = next(r for r in all_results if r["fixtureId"] == "bk_test_102")
        self.assertEqual(bk_res["status"], "live")

        # Check 'completed' remains 'completed'
        bb_res = next(r for r in all_results if r["fixtureId"] == "bb_test_103")
        self.assertEqual(bb_res["status"], "completed")

        # Verify in publishedFeed if validated
        for item in result.get("publishedFeed", []):
            self.assertIn(item["status"], ["upcoming", "live", "completed"])
            self.assertNotEqual(item["status"], "scheduled")

if __name__ == "__main__":
    unittest.main()
