import unittest
from typing import List, Dict, Any
from backend.engine.ranking import rank_and_select_best_of_day

def make_mock_candidate(sport: str, comp: str, index: int, prob: float) -> Dict[str, Any]:
    f_id = f"{sport}_{comp}_{index}"
    return {
        "id": f_id,
        "fixtureId": f_id,
        "fixture_id": f_id,
        "sport": sport,
        "league": comp,
        "competition_name": comp,
        "homeTeam": f"{sport.title()} Team A{index}",
        "awayTeam": f"{sport.title()} Team B{index}",
        "kickoffUtc": f"2026-10-06T18:{index:02d}:00Z",
        "validationStatus": "validated",
        "validation_status": "validated",
        "markets": [
            {
                "marketName": "Moneyline" if sport != "football" else "1X2",
                "selection": f"{sport.title()} Team A{index} Win",
                "calibratedPercentage": prob,
                "probabilityPercentage": prob,
                "confidenceScore": 0.75,
                "confidenceRating": "HIGH",
                "isPublishable": True,
                "marketCategory": "Moneyline",
            }
        ],
        "highestPercentagePrediction": {
            "marketName": "Moneyline" if sport != "football" else "1X2",
            "selection": f"{sport.title()} Team A{index} Win",
            "percentage": prob,
        },
    }

class TestRankingBalance(unittest.TestCase):
    def test_multisport_fair_representation_and_hockey_cap(self):
        """
        Given:
          Football = 8 valid
          Basketball = 6 valid
          Baseball = 5 valid
          Hockey = 20 valid
          F1 = 2 valid

        Expected Top 20:
          Football >= 1
          Basketball >= 1
          Baseball >= 1
          Hockey <= 9
          F1 >= 1
          Total = 20
        """
        candidates: List[Dict[str, Any]] = []

        # Football (8 valid)
        for i in range(8):
            candidates.append(make_mock_candidate("football", "Premier League", i + 1, 65.0 - i))

        # Basketball (6 valid)
        for i in range(6):
            candidates.append(make_mock_candidate("basketball", "NBA", i + 1, 70.0 - i))

        # Baseball (5 valid)
        for i in range(5):
            candidates.append(make_mock_candidate("baseball", "MLB", i + 1, 60.0 - i))

        # Hockey (20 valid)
        for i in range(20):
            candidates.append(make_mock_candidate("hockey", "NHL", i + 1, 75.0 - i * 0.5))

        # F1 (2 valid)
        for i in range(2):
            candidates.append(make_mock_candidate("formula_1", "FIA Formula One World Championship", i + 1, 68.0 - i))

        top20 = rank_and_select_best_of_day(candidates, max_results=20)

        self.assertEqual(len(top20), 20, "Must return exactly 20 predictions")

        sport_counts: Dict[str, int] = {}
        for item in top20:
            sp = item["sport"]
            sport_counts[sp] = sport_counts.get(sp, 0) + 1

        self.assertGreaterEqual(sport_counts.get("football", 0), 1, "Football must have >= 1 prediction")
        self.assertGreaterEqual(sport_counts.get("basketball", 0), 1, "Basketball must have >= 1 prediction")
        self.assertGreaterEqual(sport_counts.get("baseball", 0), 1, "Baseball must have >= 1 prediction")
        self.assertGreaterEqual(sport_counts.get("formula_1", 0), 1, "Formula 1 must have >= 1 prediction")
        self.assertLessEqual(sport_counts.get("hockey", 0), 9, "Hockey must have <= 9 predictions when other sports have candidates")

        # Verify isBestOfDay exists and is exactly one
        best_of_day_count = sum(1 for item in top20 if item.get("isBestOfDay") is True)
        self.assertEqual(best_of_day_count, 1, "Must have exactly one isBestOfDay candidate")

    def test_single_sport_available_occupies_feed(self):
        """
        Given:
          Football = 0
          Basketball = 0
          Baseball = 0
          Hockey = 20
          F1 = 0

        Expected:
          Hockey may legitimately occupy all 20.
        """
        candidates: List[Dict[str, Any]] = []
        for i in range(20):
            candidates.append(make_mock_candidate("hockey", "NHL", i + 1, 72.0 - i * 0.5))

        top20 = rank_and_select_best_of_day(candidates, max_results=20)

        self.assertEqual(len(top20), 20, "Must return 20 predictions")
        hockey_count = sum(1 for item in top20 if item["sport"] == "hockey")
        self.assertEqual(hockey_count, 20, "Hockey may occupy all 20 when no other sport has candidates")

        best_of_day_count = sum(1 for item in top20 if item.get("isBestOfDay") is True)
        self.assertEqual(best_of_day_count, 1, "Must have exactly one isBestOfDay")

if __name__ == "__main__":
    unittest.main()
