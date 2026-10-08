import unittest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone, timedelta
try:
    import numpy as np
except ImportError:
    np = None

from backend.db.mongodb import mongo_manager
from backend.services.sync_service import sync_service
from backend.services.model_service import model_service
from backend.engine.calibration import (
    clear_active_production_calibrations,
    get_active_calibration_metadata,
)
from backend.engine.evaluation_pipeline import evaluation_pipeline


class SyncScoresEvaluationPipelineTestSuite(unittest.TestCase):
    """
    Test suite for Sync Scores, Evaluation Record Persistence,
    Candidate Calibration, Chronological Validation, and Promotion Gates.
    """

    def setUp(self):
        from backend.db.database_router import database_router, RoutingMode
        database_router.set_routing_mode(RoutingMode.MONGODB_ONLY)
        # Clear database collections for clean test state
        mongo_manager.operational_events.delete_many({})
        mongo_manager.predictions.delete_many({})
        mongo_manager.prediction_results.delete_many({})
        mongo_manager.model_candidates.delete_many({})
        mongo_manager.model_promotions.delete_many({})
        clear_active_production_calibrations()

    def tearDown(self):
        from backend.db.database_router import database_router, RoutingMode
        database_router.set_routing_mode(RoutingMode.NEON_ONLY)
        mongo_manager.operational_events.delete_many({})
        mongo_manager.predictions.delete_many({})
        mongo_manager.prediction_results.delete_many({})
        mongo_manager.model_candidates.delete_many({})
        mongo_manager.model_promotions.delete_many({})
        clear_active_production_calibrations()

    # =========================================================================
    # Test 1 & 3: Result recording & Original prediction immutability
    # =========================================================================
    @patch("backend.providers.football_adapter.football_adapter.fetch_fixtures")
    @patch("backend.providers.basketball_adapter.basketball_adapter.fetch_fixtures")
    @patch("backend.providers.baseball_adapter.baseball_adapter.fetch_fixtures")
    @patch("backend.providers.sports_skills_hockey_provider.sports_skills_hockey_provider.fetch_fixtures")
    @patch("backend.providers.sports_skills_f1_provider.sports_skills_f1_provider.fetch_fixtures")
    def test_result_recording_and_original_prediction_immutability(
        self, mock_f1, mock_hk, mock_bb, mock_bk, mock_fb
    ):
        """
        Verify:
        1. Result recording creates a persistent evaluation record with all required fields.
        2. Original published prediction is completely immutable.
        """
        original_created_at = "2026-09-28T10:00:00Z"
        original_pred_doc = {
            "id": "pred_immutable_001",
            "fixture_id": "fix_test_100",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Tottenham",
            "kickoffUtc": "2026-09-28T16:30:00Z",
            "scheduled_at": "2026-09-28T16:30:00Z",
            "market": "Win / Draw / Loss (1X2)",
            "selection": "Arsenal Win",
            "percentage": 68.5,
            "validationStatus": "validated",
            "validatedMarkets": [
                {
                    "id": "pred_immutable_001-m1",
                    "marketName": "Win / Draw / Loss (1X2)",
                    "selection": "Arsenal Win",
                    "probabilityPercentage": 68.5,
                }
            ],
            "modelVersion": "ELO + POISSON",
            "model_version": "v2.1.0",
            "calibration_version": "cal_v1.0",
            "created_at": original_created_at,
        }

        # Seed operational event and original prediction
        mongo_manager.operational_events.insert_one({
            "id": "fix_test_100",
            "source_event_id": "source_100",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Tottenham",
            "status": "scheduled",
            "kickoffUtc": "2026-09-28T16:30:00Z",
            "scheduled_at": "2026-09-28T16:30:00Z",
            "currentScore": {"home": 0, "away": 0, "display": "0 - 0"},
        })
        mongo_manager.predictions.insert_one(original_pred_doc)

        # Mock completed match from provider
        mock_fb.return_value = [
            {
                "id": "fix_test_100",
                "source_event_id": "source_100",
                "sport": "football",
                "homeTeam": "Arsenal",
                "awayTeam": "Tottenham",
                "kickoffUtc": "2026-09-28T16:30:00Z",
                "status": "completed",
                "currentScore": {"home": 2, "away": 1, "display": "2 - 1"},
            }
        ]
        mock_bk.return_value = []
        mock_bb.return_value = []
        mock_hk.return_value = []
        mock_f1.return_value = []

        import asyncio
        diag = asyncio.run(sync_service.execute_sync_feed())

        # 1. Result Recording Check
        self.assertEqual(diag["completed_matches_updated"], 1)
        self.assertEqual(diag["predictions_evaluated"], 1)
        self.assertEqual(diag["hits"], 1)

        eval_rec = mongo_manager.prediction_results.find_one({"prediction_id": "pred_immutable_001-m1"})
        self.assertIsNotNone(eval_rec, "Persistent evaluation record must be stored in prediction_results")

        # Verify all required fields from prompt:
        required_fields = [
            "prediction_id",
            "fixture_id",
            "sport",
            "market",
            "model",
            "model_version",
            "calibration_version",
            "predicted_probability",
            "predicted_outcome",
            "actual_outcome",
            "hit_or_miss",
            "prediction_created_at",
            "result_recorded_at",
            "brier_score",
            "log_loss",
        ]
        for field in required_fields:
            self.assertIn(field, eval_rec, f"Evaluation record must contain field '{field}'")

        self.assertEqual(eval_rec["prediction_id"], "pred_immutable_001-m1")
        self.assertEqual(eval_rec["fixture_id"], "fix_test_100")
        self.assertEqual(eval_rec["sport"], "football")
        self.assertEqual(eval_rec["predicted_probability"], 0.685)
        self.assertEqual(eval_rec["predicted_outcome"], "Arsenal Win")
        self.assertEqual(eval_rec["actual_outcome"], "Arsenal Win")
        self.assertEqual(eval_rec["hit_or_miss"], "hit")
        self.assertEqual(eval_rec["prediction_created_at"], original_created_at)
        self.assertEqual(eval_rec["calibration_version"], "cal_v1.0")

        # 2. Original Prediction Immutability Check
        post_pred = mongo_manager.predictions.find_one({"id": "pred_immutable_001"})
        self.assertIsNotNone(post_pred)

        # Sync Scores MUST NOT modify:
        # - original predicted probability
        # - original model version
        # - original calibration version
        # - original prediction timestamp
        # - original published prediction
        self.assertEqual(post_pred["percentage"], 68.5)
        self.assertEqual(post_pred["validatedMarkets"][0]["probabilityPercentage"], 68.5)
        self.assertEqual(post_pred["modelVersion"], "ELO + POISSON")
        self.assertEqual(post_pred["model_version"], "v2.1.0")
        self.assertEqual(post_pred["calibration_version"], "cal_v1.0")
        self.assertEqual(post_pred["created_at"], original_created_at)
        self.assertEqual(post_pred["selection"], "Arsenal Win")

    # =========================================================================
    # Test 2: Duplicate result protection
    # =========================================================================
    @patch("backend.providers.football_adapter.football_adapter.fetch_fixtures")
    @patch("backend.providers.basketball_adapter.basketball_adapter.fetch_fixtures")
    @patch("backend.providers.baseball_adapter.baseball_adapter.fetch_fixtures")
    @patch("backend.providers.sports_skills_hockey_provider.sports_skills_hockey_provider.fetch_fixtures")
    @patch("backend.providers.sports_skills_f1_provider.sports_skills_f1_provider.fetch_fixtures")
    def test_duplicate_result_protection(
        self, mock_f1, mock_hk, mock_bb, mock_bk, mock_fb
    ):
        """
        Verify that running sync-feed twice on the same completed event does NOT
        create duplicate records or double-count evaluated predictions.
        """
        mongo_manager.operational_events.insert_one({
            "id": "fix_dup_test",
            "source_event_id": "dup_1",
            "sport": "football",
            "homeTeam": "Real Madrid",
            "awayTeam": "Barcelona",
            "status": "scheduled",
            "currentScore": {"home": 0, "away": 0, "display": "0 - 0"},
        })
        mongo_manager.predictions.insert_one({
            "id": "pred_dup_test",
            "fixture_id": "fix_dup_test",
            "sport": "football",
            "market": "Win / Draw / Loss (1X2)",
            "selection": "Real Madrid Win",
            "percentage": 55.0,
            "validationStatus": "validated",
            "validatedMarkets": [
                {
                    "id": "pred_dup_test-m1",
                    "marketName": "Win / Draw / Loss (1X2)",
                    "selection": "Real Madrid Win",
                    "probabilityPercentage": 55.0,
                }
            ],
            "modelVersion": "ELO + POISSON",
            "created_at": "2026-09-28T12:00:00Z",
        })

        completed_fixture = {
            "id": "fix_dup_test",
            "source_event_id": "dup_1",
            "sport": "football",
            "homeTeam": "Real Madrid",
            "awayTeam": "Barcelona",
            "status": "completed",
            "currentScore": {"home": 3, "away": 1, "display": "3 - 1"},
        }
        mock_fb.return_value = [completed_fixture]
        mock_bk.return_value = []
        mock_bb.return_value = []
        mock_hk.return_value = []
        mock_f1.return_value = []

        import asyncio
        # Run 1: First sync
        diag1 = asyncio.run(sync_service.execute_sync_feed())
        self.assertEqual(diag1["predictions_evaluated"], 1)

        total_records_after_run1 = mongo_manager.prediction_results.count_documents({"prediction_id": "pred_dup_test-m1"})
        self.assertEqual(total_records_after_run1, 1)

        # Run 2: Second sync (same match, already resolved)
        diag2 = asyncio.run(sync_service.execute_sync_feed())
        # Duplicate result protection: should not evaluate again
        self.assertEqual(diag2["predictions_evaluated"], 0, "Second sync of identical event must not re-evaluate")

        total_records_after_run2 = mongo_manager.prediction_results.count_documents({"prediction_id": "pred_dup_test-m1"})
        self.assertEqual(total_records_after_run2, 1, "Must not create duplicate evaluation records")

    # =========================================================================
    # Test 4 & 5: Candidate creation & Chronological validation
    # =========================================================================
    def test_candidate_creation_and_chronological_validation(self):
        """
        Verify that candidates are built from chronologically sorted evaluation data
        and out-of-sample forward partition is evaluated without lookahead.
        """
        base_time = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        synthetic_eval_dataset = []

        # Generate 30 chronologically ordered resolved predictions
        for i in range(30):
            ts = (base_time + timedelta(days=i)).isoformat()
            prob = 0.55 + (0.01 * (i % 10))
            hit = "hit" if i % 3 != 0 else "miss"  # ~66% hit rate
            synthetic_eval_dataset.append({
                "prediction_id": f"pred_chrono_{i:03d}",
                "fixture_id": f"fix_chrono_{i:03d}",
                "sport": "football",
                "market": "Over 2.5 Goals",
                "model": "ELO + POISSON",
                "model_version": "v2.0.0",
                "calibration_version": "cal_v1.0",
                "predicted_probability": prob,
                "predicted_outcome": "Over 2.5",
                "actual_outcome": "Over 2.5 Goals" if hit == "hit" else "Under 2.5 Goals",
                "hit_or_miss": hit,
                "prediction_created_at": ts,
                "result_recorded_at": ts,
            })

        # Run candidate evaluation pipeline with threshold 20
        summary = evaluation_pipeline.run_candidate_evaluation_and_promotion(
            custom_dataset=synthetic_eval_dataset,
            min_samples_threshold=20,
        )

        self.assertEqual(summary["candidates_evaluated"], 1)
        # Verify training records counted accurately (70% of 30 = 21)
        self.assertEqual(summary["candidate_calibration_records_created"], 21)

        candidate = summary["candidate_details"][0]
        self.assertIn("candidate_id", candidate)
        self.assertIn("validation_metrics", candidate)
        self.assertIn("gates", candidate)

        gates = candidate["gates"]
        self.assertIn("chronological_validation", gates)
        self.assertIn("calibration_validation", gates)
        self.assertIn("confidence_validation", gates)
        self.assertIn("abstention_validation", gates)

    # =========================================================================
    # Test 6 & 8: Failed promotion & Production model remains unchanged
    # =========================================================================
    def test_failed_promotion_keeps_production_model_unchanged(self):
        """
        When a candidate fails validation gates (e.g. severely degraded Brier score),
        it must be rejected and the current production model/calibrations MUST remain unchanged.
        """
        initial_prod_model = model_service.get_active_model()
        initial_cal_meta = get_active_calibration_metadata("football", "Win / Draw / Loss (1X2)")
        self.assertIn(initial_cal_meta["status"], {"uncalibrated_baseline", "NO_VALIDATED_PRODUCTION_CALIBRATION"})

        # Create severely miscalibrated data (predicted 0.90 but hit 0.05)
        # This will trigger catastrophic Brier degradation and high ECE
        base_time = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
        degraded_dataset = []
        for i in range(25):
            ts = (base_time + timedelta(days=i)).isoformat()
            # Predict high confidence (0.90) on training, but inverse on validation
            if i < 17:
                prob = 0.85
                hit = "hit"
            else:
                prob = 0.90
                hit = "miss"  # 100% misses on out-of-sample forward window!

            degraded_dataset.append({
                "prediction_id": f"pred_deg_{i:03d}",
                "fixture_id": f"fix_deg_{i:03d}",
                "sport": "football",
                "market": "Win / Draw / Loss (1X2)",
                "model": initial_prod_model,
                "model_version": "v2.0.0",
                "calibration_version": "cal_v1.0",
                "predicted_probability": prob,
                "predicted_outcome": "Arsenal Win",
                "actual_outcome": "Arsenal Win" if hit == "hit" else "Chelsea Win",
                "hit_or_miss": hit,
                "prediction_created_at": ts,
                "result_recorded_at": ts,
            })

        summary = evaluation_pipeline.run_candidate_evaluation_and_promotion(
            custom_dataset=degraded_dataset,
            min_samples_threshold=20,
        )

        self.assertEqual(summary["candidates_evaluated"], 1)
        self.assertEqual(summary["promotions_approved"], 0, "Failed candidate must NOT be promoted")

        candidate = summary["candidate_details"][0]
        self.assertFalse(candidate["promoted"])
        self.assertIn(candidate["status"], {"REJECTED", "rejected_gates_failed"})
        self.assertIn("failed_gates", candidate)

        # Assert: Current production model remains completely UNCHANGED
        current_prod_model = model_service.get_active_model()
        self.assertEqual(current_prod_model, initial_prod_model)

        # Assert: Active calibration remains unchanged (not promoted)
        post_cal_meta = get_active_calibration_metadata("football", "Win / Draw / Loss (1X2)")
        self.assertIn(post_cal_meta["status"], {"uncalibrated_baseline", "NO_VALIDATED_PRODUCTION_CALIBRATION"})

    # =========================================================================
    # Test 7: Successful promotion when all gates pass
    # =========================================================================
    def test_successful_promotion_when_all_gates_pass(self):
        """
        When a candidate passes all required gates, it is promoted and persistent
        promotion log is created.
        """
        # Create dataset where recalibration improves accuracy and passes all gates
        base_time = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
        well_calibrated_dataset = []

        for i in range(40):
            ts = (base_time + timedelta(days=i)).isoformat()
            if i % 2 == 0:
                prob = 0.85
                hit = "hit" if (i % 4 == 0 or i % 6 == 0) else "miss"
            else:
                prob = 0.25
                hit = "hit" if (i % 3 == 0) else "miss"

            well_calibrated_dataset.append({
                "prediction_id": f"pred_prom_{i:03d}",
                "fixture_id": f"fix_prom_{i:03d}",
                "sport": "basketball",
                "market": "Moneyline",
                "model": "ELO",
                "model_version": "v2.0.0",
                "calibration_version": "cal_v1.0",
                "predicted_probability": prob,
                "predicted_outcome": "Lakers Win",
                "actual_outcome": "Lakers Win" if hit == "hit" else "Warriors Win",
                "hit_or_miss": hit,
                "prediction_created_at": ts,
                "result_recorded_at": ts,
            })

        summary = evaluation_pipeline.run_candidate_evaluation_and_promotion(
            custom_dataset=well_calibrated_dataset,
            min_samples_threshold=20,
        )

        self.assertEqual(summary["candidates_evaluated"], 1)
        self.assertEqual(summary["promotions_approved"], 1, "Well-calibrated candidate should pass gates and be promoted")

        cand = summary["candidate_details"][0]
        self.assertTrue(cand["promoted"])
        self.assertIn(cand["status"], {"PRODUCTION", "promoted_to_production"})
        self.assertTrue(cand["gates"]["all_gates_passed"])

        # Verify promotion record in MongoDB
        prom_rec = mongo_manager.model_promotions.find_one({"candidate_id": cand["candidate_id"]})
        self.assertIsNotNone(prom_rec, "Persistent promotion record must be saved in model_promotions")
        self.assertEqual(prom_rec["sport"], "basketball")
        self.assertEqual(prom_rec["market"], "Moneyline")

        # Verify active production calibration is now the promoted candidate
        post_cal = get_active_calibration_metadata("basketball", "Moneyline", model="ELO")
        self.assertIn(post_cal["status"], {"PRODUCTION", "promoted_to_production"})


if __name__ == "__main__":
    unittest.main()
