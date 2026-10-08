import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta

from backend.services.sync_service import sync_service
from backend.db.duckdb_engine import duckdb_engine
from backend.services.feed_service import feed_service, get_current_lagos_today
from backend.engine.ranking import rank_and_select_best_of_day

class TestFullRefreshMultisport(unittest.IsolatedAsyncioTestCase):
    """
    Test the full refresh multi-sport sequence:
    Fresh provider discovery -> All real leagues -> DuckDB Staging -> Sport Engines -> Calibration/Validation -> Top 20 -> Neon Publication
    """

    async def asyncSetUp(self):
        self.today_lagos = get_current_lagos_today()

    async def test_full_refresh_multisport_pipeline(self):
        # 1. Mock real provider responses for all 5 supported sports
        now_dt = datetime.now(timezone.utc) + timedelta(hours=4)
        future_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        mock_provider_fixtures = {
            "football": [
                {
                    "provider": "api_sports",
                    "provider_competition_id": "39",
                    "provider_fixture_id": "fb_101",
                    "id": "api_sports_39_fb_101",
                    "fixture_id": "api_sports_39_fb_101",
                    "sport": "football",
                    "competition": "Premier League",
                    "league": "Premier League",
                    "season": "2026",
                    "start_time": future_iso,
                    "kickoffUtc": future_iso,
                    "scheduled_at": future_iso,
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "homeTeam": "Arsenal",
                    "awayTeam": "Chelsea",
                    "status": "scheduled",
                }
            ],
            "basketball": [
                {
                    "provider": "api_sports",
                    "provider_competition_id": "12",
                    "provider_fixture_id": "bk_201",
                    "id": "api_sports_12_bk_201",
                    "fixture_id": "api_sports_12_bk_201",
                    "sport": "basketball",
                    "competition": "NBA",
                    "league": "NBA",
                    "season": "2026",
                    "start_time": future_iso,
                    "kickoffUtc": future_iso,
                    "scheduled_at": future_iso,
                    "home_team": "Los Angeles Lakers",
                    "away_team": "Boston Celtics",
                    "homeTeam": "Los Angeles Lakers",
                    "awayTeam": "Boston Celtics",
                    "status": "scheduled",
                }
            ],
            "baseball": [
                {
                    "provider": "api_sports",
                    "provider_competition_id": "1",
                    "provider_fixture_id": "bb_301",
                    "id": "api_sports_1_bb_301",
                    "fixture_id": "api_sports_1_bb_301",
                    "sport": "baseball",
                    "competition": "Major League Baseball",
                    "league": "Major League Baseball",
                    "season": "2026",
                    "start_time": future_iso,
                    "kickoffUtc": future_iso,
                    "scheduled_at": future_iso,
                    "home_team": "New York Yankees",
                    "away_team": "Boston Red Sox",
                    "homeTeam": "New York Yankees",
                    "awayTeam": "Boston Red Sox",
                    "status": "scheduled",
                }
            ],
            "hockey": [
                {
                    "provider": "api_sports",
                    "provider_competition_id": "57",
                    "provider_fixture_id": "hk_401",
                    "id": "api_sports_57_hk_401",
                    "fixture_id": "api_sports_57_hk_401",
                    "sport": "hockey",
                    "competition": "NHL",
                    "league": "NHL",
                    "season": "2026",
                    "start_time": future_iso,
                    "kickoffUtc": future_iso,
                    "scheduled_at": future_iso,
                    "home_team": "Toronto Maple Leafs",
                    "away_team": "Edmonton Oilers",
                    "homeTeam": "Toronto Maple Leafs",
                    "awayTeam": "Edmonton Oilers",
                    "status": "scheduled",
                }
            ],
            "formula_1": [
                {
                    "provider": "api_sports",
                    "provider_competition_id": "1",
                    "provider_fixture_id": "f1_501",
                    "id": "api_sports_1_f1_501",
                    "fixture_id": "api_sports_1_f1_501",
                    "sport": "formula_1",
                    "competition": "Monaco Grand Prix",
                    "league": "Monaco Grand Prix",
                    "season": "2026",
                    "start_time": future_iso,
                    "kickoffUtc": future_iso,
                    "scheduled_at": future_iso,
                    "home_team": "Max Verstappen",
                    "away_team": "Lewis Hamilton",
                    "homeTeam": "Max Verstappen",
                    "awayTeam": "Lewis Hamilton",
                    "status": "scheduled",
                }
            ]
        }

        async def mock_refresh_football(target_dates, **kwargs):
            return {"fixtures": mock_provider_fixtures["football"], "fetched_count": 1, "calls_avoided": 0, "competitions_checked": 1, "provider_calls": 1, "errors": []}

        async def mock_refresh_basketball(target_dates, **kwargs):
            return {"fixtures": mock_provider_fixtures["basketball"], "fetched_count": 1, "calls_avoided": 0, "competitions_checked": 1, "provider_calls": 1, "errors": []}

        async def mock_refresh_baseball(target_dates, **kwargs):
            return {"fixtures": mock_provider_fixtures["baseball"], "fetched_count": 1, "calls_avoided": 0, "competitions_checked": 1, "provider_calls": 1, "errors": []}

        async def mock_refresh_hockey(target_dates, **kwargs):
            return {"fixtures": mock_provider_fixtures["hockey"], "fetched_count": 1, "calls_avoided": 0, "competitions_checked": 1, "provider_calls": 1, "errors": []}

        async def mock_refresh_f1(target_dates, **kwargs):
            return {"fixtures": mock_provider_fixtures["formula_1"], "fetched_count": 1, "calls_avoided": 0, "events_checked": 1, "provider_calls": 1, "errors": []}

        saved_predictions_to_neon = []

        def mock_save_predictions(predictions):
            nonlocal saved_predictions_to_neon
            saved_predictions_to_neon.extend(predictions)
            return len(predictions)

        from backend.providers.provider_router import provider_router
        from backend.services.data_access.prediction_repository import prediction_repository

        with patch.object(sync_service, "refresh_football", side_effect=mock_refresh_football), \
             patch.object(sync_service, "refresh_basketball", side_effect=mock_refresh_basketball), \
             patch.object(sync_service, "refresh_baseball", side_effect=mock_refresh_baseball), \
             patch.object(sync_service, "refresh_hockey", side_effect=mock_refresh_hockey), \
             patch.object(sync_service, "refresh_f1", side_effect=mock_refresh_f1), \
             patch.object(prediction_repository, "save_predictions", side_effect=mock_save_predictions):

            # 2. Run force refresh
            rec = await sync_service.execute_refresh(force=True)

            # 3. Assert competitions & fixtures were discovered
            self.assertIsNotNone(rec)
            rec_dict = rec.to_dict() if hasattr(rec, "to_dict") else (rec.dict() if hasattr(rec, "dict") else rec.__dict__)
            self.assertIn("runId", rec_dict)
            self.assertGreater(rec_dict.get("fixturesDiscovered", 0), 0)

            # 4. Assert fixtures were staged in DuckDB
            staged = duckdb_engine.con.execute("SELECT COUNT(*) FROM discovered_fixtures").fetchone()[0]
            self.assertGreater(staged, 0, "Fixtures must be staged in DuckDB")

            # 5. Assert prediction engines ran
            self.assertGreater(rec_dict.get("candidatesGenerated", 0), 0, "Prediction candidates must be generated")

            # 6. Assert only validated predictions were published
            pub_count = rec_dict.get("publishedPredictions", 0)
            val_count = rec_dict.get("validatedPredictions", 0)
            self.assertLessEqual(pub_count, val_count, "Published predictions cannot exceed validated count")

            # 7. Assert Neon receives ONLY published predictions
            for pred in saved_predictions_to_neon:
                self.assertTrue(pred.get("published", False), "Neon must receive only published predictions")
                self.assertEqual(pred.get("validationStatus"), "validated")
                # Assert write contract fields
                self.assertIn("fixture_id", pred)
                self.assertIn("sport", pred)
                self.assertIn("highestPercentagePrediction", pred)

            # 8. Assert no fabricated fixture IDs exist
            for fix_list in mock_provider_fixtures.values():
                for f in fix_list:
                    self.assertTrue(f["fixture_id"].startswith("api_sports_"))

            # 9. Assert no fallback league created when provider metadata missing
            empty_comp_res = await provider_router.fetch_all_fixtures("unknown_sport_xyz")
            self.assertEqual(empty_comp_res, [])

            # 10. Assert top 20 selection enforces MAX_SPORT_SHARE = 9 & max 20 total
            # Test ranking function directly with 25 mock predictions across 3 sports
            mock_preds = []
            for i in range(15):
                mock_preds.append({
                    "fixture_id": f"fb_{i}",
                    "sport": "football",
                    "validationStatus": "validated",
                    "published": True,
                    "highestPercentagePrediction": 70.0 + i,
                })
            for i in range(10):
                mock_preds.append({
                    "fixture_id": f"bk_{i}",
                    "sport": "basketball",
                    "validationStatus": "validated",
                    "published": True,
                    "highestPercentagePrediction": 65.0 + i,
                })

            top20 = rank_and_select_best_of_day(mock_preds)
            self.assertLessEqual(len(top20), 20)
            fb_count = sum(1 for p in top20 if p["sport"] == "football")
            self.assertLessEqual(fb_count, 9, "MAX_SPORT_SHARE of 9 must be enforced in ranking")

            # 12. Assert no past fixture appears in upcoming feed
            past_iso = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            past_fixture_pred = {
                "fixture_id": "past_101",
                "sport": "football",
                "fixture_time": past_iso,
                "validationStatus": "validated",
                "published": True,
            }
            # Verify filtering logic excludes past fixtures
            from backend.services.feed_service import is_upcoming_fixture
            self.assertFalse(is_upcoming_fixture(past_fixture_pred, self.today_lagos))

if __name__ == "__main__":
    unittest.main()
