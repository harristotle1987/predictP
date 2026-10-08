import unittest
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any

from backend.engine.ranking import rank_and_select_best_of_day, SPORTS
from backend.engine.calibration import seed_initial_production_calibrations, DEFAULT_PRODUCTION_MODELS
from backend.engine.validation_and_abstention import validate_market_for_sport
from backend.engine.pipeline import execute_prediction_pipeline
from backend.services.feed_service import feed_service
from backend.db.neon_adapter import neon_adapter
from backend.db.database_router import database_router


class SportDistributionAndPipelineTestSuite(unittest.TestCase):
    """
    Unit test suite verifying sport-aware ranking, multi-sport representation,
    strict minimum history enforcement, zero synthetic predictions, and market validity.
    """

    def _make_candidate(
        self,
        c_id: str,
        sport: str,
        pct: float,
        league: str = "Premier League",
        home: str = "Arsenal FC",
        away: str = "Chelsea FC"
    ) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "id": c_id,
            "fixtureId": c_id,
            "sport": sport,
            "league": league,
            "homeTeam": home,
            "awayTeam": away,
            "kickoffUtc": now_iso,
            "scheduled_at": now_iso,
            "kickoff": now_iso,
            "validationStatus": "validated",
            "published": True,
            "calibratedPercentage": pct,
            "highestPercentagePrediction": {
                "marketName": "Winner",
                "selection": f"{home} Win",
                "percentage": pct,
            },
            "markets": [
                {
                    "marketName": "Winner",
                    "selection": f"{home} Win",
                    "calibratedPercentage": pct,
                    "confidenceScore": 0.85,
                    "isPublishable": True,
                    "marketCategory": "1X2" if sport == "football" else "Moneyline",
                }
            ],
        }

    # =========================================================================
    # Test 1: Top-20 does NOT become hockey-only when other sports have candidates
    # =========================================================================
    def test_top20_does_not_become_hockey_only_when_other_sports_have_valid_candidates(self):
        candidates = []
        # 15 hockey candidates with high percentages (80%-95%)
        for i in range(15):
            candidates.append(self._make_candidate(f"hk_{i}", "hockey", 80.0 + i, home=f"Rangers {i}", away=f"Bruins {i}"))
        # 5 football candidates with moderate percentages (65%-70%)
        for i in range(5):
            candidates.append(self._make_candidate(f"fb_{i}", "football", 65.0 + i, home=f"Arsenal {i}", away=f"Chelsea {i}"))
        # 3 basketball candidates with moderate percentages (60%-63%)
        for i in range(3):
            candidates.append(self._make_candidate(f"bk_{i}", "basketball", 60.0 + i, home=f"Lakers {i}", away=f"Celtics {i}"))

        ranked = rank_and_select_best_of_day(candidates, max_results=20)
        sports_in_ranked = {c.get("sport") for c in ranked}

        self.assertIn("football", sports_in_ranked, "Top-20 must include football representation")
        self.assertIn("basketball", sports_in_ranked, "Top-20 must include basketball representation")
        self.assertIn("hockey", sports_in_ranked, "Top-20 must include hockey representation")
        self.assertLessEqual(len(ranked), 20)

    # =========================================================================
    # Test 2: Sport without valid candidates is NOT fabricated
    # =========================================================================
    def test_sport_without_valid_candidates_is_not_fabricated(self):
        candidates = []
        for i in range(5):
            candidates.append(self._make_candidate(f"fb_{i}", "football", 70.0 + i))
        for i in range(5):
            candidates.append(self._make_candidate(f"hk_{i}", "hockey", 75.0 + i))

        # No baseball or formula_1 candidates supplied
        ranked = rank_and_select_best_of_day(candidates, max_results=20)
        sports_in_ranked = {c.get("sport") for c in ranked}

        self.assertNotIn("baseball", sports_in_ranked, "Baseball must not be fabricated when zero candidates exist")
        self.assertNotIn("formula_1", sports_in_ranked, "Formula 1 must not be fabricated when zero candidates exist")
        self.assertEqual(len(ranked), 10)

    # =========================================================================
    # Test 3: Each valid sport can receive representation
    # =========================================================================
    def test_each_valid_sport_can_receive_representation(self):
        candidates = []
        all_sports = ["football", "basketball", "baseball", "hockey", "formula_1"]
        for sp in all_sports:
            for i in range(4):
                candidates.append(self._make_candidate(f"{sp}_{i}", sp, 60.0 + i))

        ranked = rank_and_select_best_of_day(candidates, max_results=20)
        sports_in_ranked = {c.get("sport") for c in ranked}

        for sp in all_sports:
            self.assertIn(sp, sports_in_ranked, f"Sport {sp} must receive representation in top 20")

    # =========================================================================
    # Test 4: Total predictions never exceeds 20
    # =========================================================================
    def test_total_predictions_never_exceeds_20(self):
        candidates = []
        for i in range(50):
            sp = ["football", "basketball", "baseball", "hockey", "formula_1"][i % 5]
            candidates.append(self._make_candidate(f"cand_{i}", sp, 50.0 + (i % 30)))

        ranked = rank_and_select_best_of_day(candidates, max_results=20)
        self.assertLessEqual(len(ranked), 20, "Total predictions must never exceed 20")

    # =========================================================================
    # Test 5: Minimum history rule remains strictly 5 completed matches
    # =========================================================================
    def test_minimum_history_rule_remains_5(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        # Candidate with unknown teams (0 history in DuckDB)
        res = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=True,
            custom_fixtures=[{
                "id": "fix_insufficient_history_test_001",
                "sport": "basketball",
                "league": "NBA",
                "homeTeam": "Unknown Team Alpha",
                "awayTeam": "Unknown Team Beta",
                "kickoffUtc": now_iso,
                "status": "scheduled",
            }]
        )
        all_res = res.get("allResults", [])
        self.assertEqual(len(all_res), 1)
        self.assertEqual(all_res[0]["stopReason"], "INSUFFICIENT_HISTORY")
        self.assertEqual(all_res[0]["validationStatus"], "insufficient_data")

    # =========================================================================
    # Test 6: No synthetic predictions or calibrations are created
    # =========================================================================
    def test_no_synthetic_predictions_are_created(self):
        seeded_count = seed_initial_production_calibrations()
        self.assertEqual(seeded_count, 0, "Initial synthetic calibration seeding must return 0")

        # Verify DEFAULT_PRODUCTION_MODELS configured for all sports
        for sp in ["football", "basketball", "baseball", "hockey"]:
            self.assertEqual(DEFAULT_PRODUCTION_MODELS[sp], "ELO + POISSON")

    # =========================================================================
    # Test 7: No invalid sport markets are published
    # =========================================================================
    def test_no_invalid_sport_markets_are_published(self):
        # Football market 'Corners' on Basketball must be rejected
        self.assertFalse(validate_market_for_sport("basketball", "Match Corners", "Corners"))
        # Basketball 'PointsTotal' on Football must be rejected
        self.assertFalse(validate_market_for_sport("football", "Points Total", "PointsTotal"))
        # Valid football 1X2 market must pass
        self.assertTrue(validate_market_for_sport("football", "Full Time 1X2", "1X2"))
        # Valid basketball Moneyline market must pass
        self.assertTrue(validate_market_for_sport("basketball", "Moneyline", "Moneyline"))

    # =========================================================================
    # Test 8: Feed preserves sport distribution from ranking.py
    # =========================================================================
    def test_feed_preserves_sport_distribution_from_ranking(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        date_str = now_iso[:10]

        preds = []
        # Save 12 hockey predictions and 3 football predictions
        for i in range(12):
            preds.append(self._make_candidate(f"feed_hk_{i}", "hockey", 85.0 + i))
        for i in range(3):
            preds.append(self._make_candidate(f"feed_fb_{i}", "football", 62.0 + i))

        asyncio.run(database_router.predictions.save_predictions(preds))
        asyncio.run(neon_adapter.predictions.save_predictions(preds))

        feed = asyncio.run(feed_service.get_feed(sport="all", limit=20))
        sports_in_feed = {item.get("sport") for item in feed}

        self.assertIn("football", sports_in_feed, "Feed must preserve multi-sport representation")
        self.assertIn("hockey", sports_in_feed, "Feed must preserve hockey representation")


if __name__ == "__main__":
    unittest.main()
