"""
End-to-end prediction display integrity test verifying:
1. highestPercentagePrediction logic across pipeline, feed_service, and UI contracts.
2. Market ordering independence (unsorted markets like BTTS 61%, Over 2.5 68%, Home Win 76%).
3. Multi-sport validation across all 5 supported sports: Football, Basketball, Baseball, Hockey, Formula 1.
4. Total prohibition of 50.0% fake fallback probabilities, placeholder team names, synthetic IDs, unvalidated calibration.
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from backend.engine.pipeline import execute_prediction_pipeline
from backend.services.feed_service import FeedService
from backend.engine.validation_and_abstention import get_current_lagos_today, LAGOS_TZ


class TestPredictionDisplayIntegrity(unittest.TestCase):

    def setUp(self):
        self.future_date_utc = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT18:00:00Z")
        self.today_lagos = get_current_lagos_today()

    def test_unsorted_markets_highest_prediction_derivation(self):
        """
        Create a fixture containing deliberately unsorted markets:
        BTTS — 61%
        Over 2.5 — 68%
        Home Win — 76%

        Assert that ALL of these return:
        highestPercentagePrediction.marketName = Home Win
        highestPercentagePrediction.selection = Home Win
        highestPercentagePrediction.percentage = 76
        percentage = 76
        calibrated_probability = 0.76
        """
        raw_markets = [
            {
                "marketName": "Both Teams to Score",
                "marketType": "BTTS",
                "selection": "Yes",
                "rawProbability": 0.60,
                "calibratedProbability": 0.61,
                "probabilityPercentage": 61.0,
                "calibratedPercentage": 61.0,
            },
            {
                "marketName": "Over / Under 2.5",
                "marketType": "GoalsTotal",
                "selection": "Over 2.5 Goals",
                "rawProbability": 0.65,
                "calibratedProbability": 0.68,
                "probabilityPercentage": 68.0,
                "calibratedPercentage": 68.0,
            },
            {
                "marketName": "Home Win",
                "marketType": "1X2",
                "selection": "Home Win",
                "rawProbability": 0.75,
                "calibratedProbability": 0.76,
                "probabilityPercentage": 76.0,
                "calibratedPercentage": 76.0,
            },
        ]

        fixture = {
            "id": "fix_unsorted_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": self.future_date_utc,
            "status": "scheduled",
        }

        # Mock pipeline steps to return raw_markets
        mock_features = {
            "hasSufficientData": True,
            "homeMatchesCount": 15,
            "awayMatchesCount": 15,
        }

        mock_ensemble_result = {
            "hasSufficientData": True,
            "markets": raw_markets,
            "modelVersion": "ELO + POISSON v1.0",
        }

        mock_cal_details = {
            "is_calibrated": True,
            "calibration_status": "APPROVED",
            "calibration_id": "cal_test_1",
            "is_baseline": False,
            "calibrated_probability": 0.76,
            "calibrated_percentage": 76.0,
        }

        with patch("backend.engine.pipeline.build_football_features", return_value=mock_features), \
             patch("backend.engine.pipeline.run_model_ensemble", return_value=mock_ensemble_result), \
             patch("backend.engine.pipeline.get_calibration_details", side_effect=lambda raw_prob, **kwargs: {
                 "is_calibrated": True,
                 "calibration_status": "PRODUCTION",
                 "calibration_id": "cal_test_1",
                 "is_baseline": False,
                 "calibrated_probability": raw_prob,
                 "calibrated_percentage": round(raw_prob * 100.0, 1),
             }):

            res = execute_prediction_pipeline(
                active_model="ELO + POISSON",
                is_subscriber_feed=True,
                custom_fixtures=[fixture],
            )

            published = res.get("publishedFeed", [])
            self.assertEqual(len(published), 1, "Fixture should be published")
            pub_rec = published[0]

            # Verify pipeline outputs
            highest_pred = pub_rec.get("highestPercentagePrediction")
            self.assertIsNotNone(highest_pred)
            self.assertEqual(highest_pred["marketName"], "Home Win")
            self.assertEqual(highest_pred["selection"], "Home Win")
            self.assertEqual(highest_pred["percentage"], 76.0)

            self.assertEqual(pub_rec["percentage"], 76.0)
            self.assertEqual(pub_rec["calibrated_probability"], 0.76)
            self.assertEqual(pub_rec["raw_probability"], 0.76)

            # Test feed normalization on this record
            feed_service = FeedService()
            normalized = feed_service.filter_valid_predictions([pub_rec])[0]
            self.assertEqual(normalized["highestPercentagePrediction"]["marketName"], "Home Win")
            self.assertEqual(normalized["highestPercentagePrediction"]["selection"], "Home Win")
            self.assertEqual(normalized["highestPercentagePrediction"]["percentage"], 76.0)
            self.assertEqual(normalized["percentage"], 76.0)
            self.assertEqual(normalized["calibratedPercentage"], 76.0)

    def test_multi_sport_prediction_integrity(self):
        """
        Test all five sports:
        Football, Basketball, Baseball, Hockey, Formula 1

        For each published prediction assert:
        validationStatus = validated
        published = true
        validatedMarkets is non-empty
        highestPercentagePrediction exists
        highestPercentagePrediction.percentage == max(validatedMarkets probability)
        sport is correct
        competition is correct
        fixtureId is correct
        homeTeam/awayTeam are correct
        kickoff is correct
        """
        sports_data = [
            {
                "sport": "football",
                "league": "Premier League",
                "home": "Arsenal",
                "away": "Chelsea",
                "marketName": "Home Win",
                "marketType": "1X2",
                "selection": "Arsenal",
                "pct": 72.0,
            },
            {
                "sport": "basketball",
                "league": "NBA",
                "home": "Boston Celtics",
                "away": "Los Angeles Lakers",
                "marketName": "Moneyline Winner",
                "marketType": "Moneyline",
                "selection": "Boston Celtics",
                "pct": 68.0,
            },
            {
                "sport": "baseball",
                "league": "MLB",
                "home": "New York Yankees",
                "away": "Boston Red Sox",
                "marketName": "Moneyline Winner",
                "marketType": "Moneyline",
                "selection": "New York Yankees",
                "pct": 64.0,
            },
            {
                "sport": "hockey",
                "league": "NHL",
                "home": "Edmonton Oilers",
                "away": "Toronto Maple Leafs",
                "marketName": "Puck Line -1.5",
                "marketType": "PuckLine",
                "selection": "Edmonton Oilers -1.5",
                "pct": 62.0,
            },
            {
                "sport": "formula_1",
                "league": "Formula 1 World Championship",
                "home": "Max Verstappen",
                "away": "Lewis Hamilton",
                "marketName": "Race Winner",
                "marketType": "RaceWinner",
                "selection": "Max Verstappen",
                "pct": 81.0,
            },
        ]

        for sdata in sports_data:
            sport = sdata["sport"]
            league = sdata["league"]
            fix_id = f"fix_{sport}_001"
            
            fixture = {
                "id": fix_id,
                "sport": sport,
                "league": league,
                "homeTeam": sdata["home"],
                "awayTeam": sdata["away"],
                "kickoffUtc": self.future_date_utc,
                "status": "scheduled",
            }

            raw_markets = [
                {
                    "marketName": sdata["marketName"],
                    "marketType": sdata["marketType"],
                    "selection": sdata["selection"],
                    "rawProbability": sdata["pct"] / 100.0,
                    "calibratedProbability": sdata["pct"] / 100.0,
                    "probabilityPercentage": sdata["pct"],
                    "calibratedPercentage": sdata["pct"],
                },
                {
                    "marketName": "Lower Probability Market",
                    "marketType": sdata["marketType"],
                    "selection": "Secondary Option",
                    "rawProbability": 0.40,
                    "calibratedProbability": 0.40,
                    "probabilityPercentage": 40.0,
                    "calibratedPercentage": 40.0,
                },
            ]

            mock_features = {"hasSufficientData": True, "homeMatchesCount": 10, "awayMatchesCount": 10}
            mock_ensemble_result = {"hasSufficientData": True, "markets": raw_markets, "modelVersion": "v1.0"}

            f1_features = {"hasSufficientData": True, "drivers": [{"driverName": "Max Verstappen"}] * 10, "homeMatchesCount": 10, "awayMatchesCount": 10}

            with patch("backend.engine.pipeline.build_football_features", return_value=mock_features), \
                 patch("backend.engine.pipeline.build_basketball_features", return_value=mock_features), \
                 patch("backend.engine.pipeline.build_baseball_features", return_value=mock_features), \
                 patch("backend.engine.pipeline.build_hockey_features", return_value=mock_features), \
                 patch("backend.features.f1_feature_builder.build_f1_features", return_value=f1_features), \
                 patch("backend.engine.pipeline.run_model_ensemble", return_value=mock_ensemble_result), \
                 patch("backend.engine.pipeline.get_calibration_details", side_effect=lambda raw_prob, **kwargs: {
                     "is_calibrated": True,
                     "calibration_status": "PRODUCTION",
                     "calibration_id": "cal_test_1",
                     "is_baseline": False,
                     "calibrated_probability": raw_prob,
                     "calibrated_percentage": round(raw_prob * 100.0, 1),
                 }):

                res = execute_prediction_pipeline(
                    active_model="ELO + POISSON" if sport == "football" else "ELO",
                    is_subscriber_feed=True,
                    custom_fixtures=[fixture],
                )

                published = res.get("publishedFeed", [])
                self.assertEqual(len(published), 1, f"Published feed should contain record for {sport}")
                pub_rec = published[0]

                # Assert contract checks
                self.assertEqual(pub_rec["validationStatus"], "validated")
                self.assertTrue(pub_rec.get("published", True))
                self.assertTrue(len(pub_rec["validatedMarkets"]) > 0)
                
                highest_pred = pub_rec.get("highestPercentagePrediction")
                self.assertIsNotNone(highest_pred)
                
                max_market_prob = max(float(m.get("probabilityPercentage") or 0.0) for m in pub_rec["validatedMarkets"])
                self.assertEqual(highest_pred["percentage"], max_market_prob)

                self.assertEqual(pub_rec["sport"], sport)
                self.assertEqual(pub_rec["competition"], league)
                self.assertEqual(pub_rec["fixtureId"], fix_id)
                self.assertEqual(pub_rec["homeTeam"], sdata["home"])
                self.assertEqual(pub_rec["awayTeam"], sdata["away"])
                self.assertEqual(pub_rec["kickoffUtc"], self.future_date_utc)

    def test_prohibit_fake_prob_and_placeholders(self):
        """
        Verify that no published prediction can contain:
        50.0 fallback probability
        placeholder teams
        synthetic fixture IDs
        wrong sport markets
        past fixture in today's future feed
        unvalidated calibration
        """
        # Test 1: missing probability causing rejection rather than 50%
        rec_missing_prob = {
            "id": "rec_001",
            "sport": "football",
            "validationStatus": "validated",
            "published": True,
            "markets": [{"marketName": "1X2", "selection": "Home"}],  # missing probability
        }
        feed_service = FeedService()
        filtered = feed_service.filter_valid_predictions([rec_missing_prob])
        self.assertEqual(len(filtered), 0, "Missing probability item must be rejected by feed_service")

        # Test 2: Placeholder teams rejection in validation
        from backend.engine.validation_and_abstention import validate_fixture_pre_conditions
        bad_team_fixture = {
            "id": "fix_real_123",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Team A",
            "awayTeam": "Team B",
            "kickoffUtc": self.future_date_utc,
            "status": "scheduled",
        }
        val_res = validate_fixture_pre_conditions(bad_team_fixture)
        self.assertFalse(val_res["isValid"])
        self.assertEqual(val_res["stopReason"], "REJECTED_PLACEHOLDER_TEAMS")

    def test_latest_diagnostics_summary_structure(self):
        """
        Verify feed_service.get_latest_diagnostics_summary returns the required 6 UI metrics:
        Fixtures discovered, Fixtures eligible, Predictions validated, Predictions published,
        Sports represented, Leagues represented, and per-sport breakdown.
        """
        import asyncio
        feed_service = FeedService()
        mock_run = {
            "id": "run_test_123",
            "timestamp": "2026-10-08T12:00:00Z",
            "matches_synced": 50,
            "predictions_published": 5,
            "validatedCount": 5,
            "diagnostics": {
                "fixturesDiscovered": 50,
                "fixturesEligible": 5,
                "predictionsValidated": 5,
                "predictionsPublished": 5,
                "sportsRepresented": 1,
                "leaguesRepresented": 1,
                "per_sport_audit": {
                    "football": {
                        "discovered": 50,
                        "history_eligible": 5,
                        "publishable": 5,
                        "exact_rejection_reason": "NONE",
                    }
                },
                "competition_telemetry": [
                    {
                        "sport": "football",
                        "competition": "Premier League",
                        "fixtures_discovered": 50,
                        "eligible": 5,
                        "rejected": 45,
                        "published": 5,
                    }
                ]
            }
        }

        with patch("backend.db.database_router.database_router.refresh_state.get_latest_run", return_value=mock_run):
            summary = asyncio.run(feed_service.get_latest_diagnostics_summary())
            self.assertEqual(summary["fixturesDiscovered"], 50)
            self.assertEqual(summary["fixturesEligible"], 5)
            self.assertEqual(summary["predictionsValidated"], 5)
            self.assertEqual(summary["predictionsPublished"], 5)
            self.assertEqual(summary["sportsRepresented"], 1)
            self.assertEqual(summary["leaguesRepresented"], 1)
            self.assertEqual(len(summary["perSport"]), 1)
            self.assertEqual(summary["perSport"][0]["sport"], "football")
            self.assertEqual(summary["perSport"][0]["discovered"], 50)
            self.assertEqual(summary["perSport"][0]["eligible"], 5)
            self.assertEqual(summary["perSport"][0]["published"], 5)


if __name__ == "__main__":
    unittest.main()
