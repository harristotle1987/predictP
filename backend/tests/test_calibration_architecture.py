import unittest
from datetime import datetime, timezone
try:
    import numpy as np
except ImportError:
    np = None

from backend.db.mongodb import mongo_manager
from backend.db.database_router import database_router
from backend.engine.calibration import (
    fit_calibration_candidate,
    promote_calibration_candidate,
    get_production_calibration,
    calibrate_probability,
    get_calibration_details,
    clear_active_production_calibrations,
)
from backend.engine.validation_and_abstention import validate_market_publication


class MockMongoCollection:
    def __init__(self):
        self._docs = []

    def insert_one(self, doc):
        self._docs.append(dict(doc))

    def update_one(self, filt, update, upsert=False):
        set_vals = update.get("$set", {})
        for d in self._docs:
            if all(d.get(k) == v for k, v in filt.items()):
                d.update(set_vals)
                return
        if upsert:
            new_doc = dict(filt)
            new_doc.update(set_vals)
            self._docs.append(new_doc)

    def update_many(self, filt, update):
        set_vals = update.get("$set", {})
        for d in self._docs:
            if all(d.get(k) == v for k, v in filt.items()):
                d.update(set_vals)

    def delete_many(self, filt=None):
        if not filt:
            self._docs.clear()
        else:
            self._docs = [d for d in self._docs if not all(d.get(k) == v for k, v in filt.items())]

    def find_one(self, filt=None, projection=None, sort=None):
        docs = self._docs
        if filt:
            docs = [d for d in docs if all(d.get(k) == v for k, v in filt.items())]
        if not docs:
            return None
        if sort and isinstance(sort, list):
            sort_key, sort_dir = sort[0]
            docs = sorted(docs, key=lambda x: str(x.get(sort_key, "")), reverse=(sort_dir == -1))
        return dict(docs[0])

    def find(self, filt=None, proj=None):
        docs = self._docs
        if filt:
            docs = [d for d in docs if all(d.get(k) == v for k, v in filt.items())]
        return docs


class CalibrationArchitectureTestSuite(unittest.TestCase):
    """
    Test suite for the Persistent MongoDB Calibration Architecture.
    Verifies:
    1. Calibration survives process restart.
    2. Candidate cannot be used as production.
    3. Rejected calibration cannot publish.
    4. Production calibration is loaded correctly.
    5. Missing production calibration fails closed.
    6. Explicit baseline mode is kept strictly outside production subscriber path.
    """

    def setUp(self):
        from backend.db.neon_adapter import neon_adapter
        self.mock_col = MockMongoCollection()
        self._orig_db = mongo_manager._db
        mongo_manager._db = {"calibration_records": self.mock_col, "calibrations": self.mock_col}
        if hasattr(neon_adapter, "_mock_db") and hasattr(neon_adapter._mock_db, "_mock_store"):
            neon_adapter._mock_db._mock_store["neon_calibration_metadata"] = {}

    def tearDown(self):
        mongo_manager._db = self._orig_db

    # =========================================================================
    # Test 1: Calibration survives process restart
    # =========================================================================
    def test_calibration_survives_process_restart(self):
        """
        Verify that a promoted PRODUCTION calibration persisted in MongoDB
        survives process restart (memory cache clearance) and loads authoritatively.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        cal_id = "cal_restart_test_v1"

        cal_doc = {
            "calibration_id": cal_id,
            "model": "ELO + POISSON",
            "sport": "football",
            "market": "Win / Draw / Loss (1X2)",
            "calibration_method": "isotonic_regression",
            "training_dataset": "ds_historical_v1",
            "training_period": "2026-01-01 to 2026-06-30",
            "validation_period": "2026-07-01 to 2026-08-31",
            "created_at": now_iso,
            "metrics": {
                "raw_brier": 0.2250,
                "calibrated_brier": 0.2010,
                "brier_improvement": 0.0240,
                "ece": 0.0380,
            },
            "calibration_error": 0.0380,
            "status": "PRODUCTION",
            "approved_at": now_iso,
            "approved_by": "admin_audit_gate",
            "feature_schema_version": "v2.0",
            "thresholds_x": [0.10, 0.50, 0.90],
            "thresholds_y": [0.12, 0.48, 0.88],
        }

        # 1. Persist to MongoDB and DatabaseRouter
        mongo_manager.calibration_records.insert_one(cal_doc)
        import asyncio
        asyncio.run(database_router.calibrations.save_calibration("football", "ELO + POISSON", cal_doc.get("market", "default"), cal_doc))

        # 2. Simulate process restart by reloading from MongoDB
        loaded = get_production_calibration("football", "ELO + POISSON", "Win / Draw / Loss (1X2)")
        self.assertIsNotNone(loaded, "Persisted calibration must be found after restart")
        self.assertEqual(loaded["calibration_id"], cal_id)
        self.assertEqual(loaded["status"], "PRODUCTION")
        self.assertEqual(loaded["approved_by"], "admin_audit_gate")

        # 3. Verify it applies the persistent interpolation curve
        # At raw_prob = 0.50, thresholds yield 0.48
        cal_prob = calibrate_probability(0.50, "football", "ELO + POISSON", "Win / Draw / Loss (1X2)")
        self.assertIsNotNone(cal_prob)
        self.assertAlmostEqual(cal_prob, 0.48, places=2)

    # =========================================================================
    # Test 2: Candidate cannot be used as production
    # =========================================================================
    def test_candidate_cannot_be_used_as_production(self):
        """
        Verify that a calibration in CANDIDATE status cannot be used as production,
        is ignored by get_production_calibration, and cannot publish to subscriber feed.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        cand_id = "cal_candidate_only_v1"

        cand_doc = {
            "calibration_id": cand_id,
            "model": "ELO",
            "sport": "basketball",
            "market": "Moneyline",
            "calibration_method": "isotonic_regression",
            "training_dataset": "eval_ds_cand_1",
            "training_period": "2026-05-01 to 2026-07-01",
            "validation_period": "2026-07-02 to 2026-08-01",
            "created_at": now_iso,
            "metrics": {"ece": 0.08},
            "calibration_error": 0.08,
            "status": "CANDIDATE", # CANDIDATE status, not approved
            "approved_at": None,
            "approved_by": None,
            "feature_schema_version": "v2.0",
            "thresholds_x": [0.2, 0.8],
            "thresholds_y": [0.25, 0.75],
        }
        mongo_manager.calibration_records.insert_one(cand_doc)
        import asyncio
        asyncio.run(database_router.calibrations.save_calibration("basketball", "ELO", cand_doc.get("market", "default"), cand_doc))

        # 1. Production lookup ignores CANDIDATE
        prod = get_production_calibration("basketball", "ELO", "Moneyline")
        self.assertIsNone(prod, "CANDIDATE calibration must not be returned by production lookup")

        # 2. calibrate_probability refuses to use it without explicit baseline
        prob = calibrate_probability(0.60, "basketball", "ELO", "Moneyline", allow_baseline=False)
        self.assertIsNone(prob, "calibrate_probability must fail closed and return None for CANDIDATE")

        # 3. Market publication gate rejects candidate status
        val = validate_market_publication(
            {
                "marketName": "Moneyline",
                "selection": "Lakers Win",
                "rawProbability": 0.60,
                "calibratedProbability": 0.60,
                "calibratedPercentage": 60.0,
                "confidenceScore": 0.75,
                "hasSufficientData": True,
                "marketCategory": "Moneyline",
                "isCalibrated": False,
                "calibrationStatus": "CANDIDATE",
            },
            sport="basketball",
            is_subscriber_feed=True,
            active_model="ELO",
        )
        self.assertFalse(val["isPublishable"], "CANDIDATE calibration must not be publishable")
        self.assertEqual(val["stopReason"], "CANDIDATE_NOT_PRODUCTION")

    # =========================================================================
    # Test 3: Rejected calibration cannot publish
    # =========================================================================
    def test_rejected_calibration_cannot_publish(self):
        """
        Verify that a REJECTED calibration cannot be used as production and
        cannot publish.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        rej_id = "cal_rejected_sample_v1"

        rej_doc = {
            "calibration_id": rej_id,
            "model": "ELO",
            "sport": "baseball",
            "market": "Moneyline",
            "calibration_method": "isotonic_regression",
            "training_dataset": "eval_ds_rej_1",
            "training_period": "2026-05-01 to 2026-07-01",
            "validation_period": "2026-07-02 to 2026-08-01",
            "created_at": now_iso,
            "metrics": {"ece": 0.35, "rejection": "failed_calibration_gate"},
            "calibration_error": 0.35,
            "status": "REJECTED", # Failed gate
            "approved_at": None,
            "approved_by": None,
            "feature_schema_version": "v2.0",
            "thresholds_x": [],
            "thresholds_y": [],
        }
        mongo_manager.calibration_records.insert_one(rej_doc)
        import asyncio
        asyncio.run(database_router.calibrations.save_calibration("baseball", "ELO", rej_doc.get("market", "default"), rej_doc))

        # 1. Production lookup ignores REJECTED
        prod = get_production_calibration("baseball", "ELO", "Moneyline")
        self.assertIsNone(prod)

        # 2. Market publication gate strictly rejects REJECTED calibrations
        val = validate_market_publication(
            {
                "marketName": "Moneyline",
                "selection": "Yankees Win",
                "rawProbability": 0.65,
                "calibratedProbability": 0.65,
                "calibratedPercentage": 65.0,
                "confidenceScore": 0.80,
                "hasSufficientData": True,
                "marketCategory": "Moneyline",
                "isCalibrated": False,
                "calibrationStatus": "REJECTED",
            },
            sport="baseball",
            is_subscriber_feed=True,
            active_model="ELO",
        )
        self.assertFalse(val["isPublishable"])
        self.assertEqual(val["stopReason"], "REJECTED_CALIBRATION")

    # =========================================================================
    # Test 4: Production calibration is loaded correctly
    # =========================================================================
    def test_production_calibration_is_loaded_correctly(self):
        """
        Verify that an approved PRODUCTION calibration is loaded from MongoDB
        with all required minimum fields and applied accurately.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        prod_id = "cal_prod_hockey_v1"

        prod_doc = {
            "calibration_id": prod_id,
            "model": "ELO",
            "sport": "hockey",
            "market": "Moneyline",
            "calibration_method": "isotonic_regression",
            "training_dataset": "nhl_dataset_2026",
            "training_period": "2025-10-01 to 2026-04-30",
            "validation_period": "2026-05-01 to 2026-06-30",
            "created_at": now_iso,
            "metrics": {
                "raw_brier": 0.2310,
                "calibrated_brier": 0.2090,
                "brier_improvement": 0.0220,
                "ece": 0.0410,
            },
            "calibration_error": 0.0410,
            "status": "PRODUCTION",
            "approved_at": now_iso,
            "approved_by": "qa_pipeline_evaluator",
            "feature_schema_version": "v2.0",
            "thresholds_x": [0.10, 0.40, 0.70, 0.90],
            "thresholds_y": [0.15, 0.42, 0.68, 0.85],
        }
        mongo_manager.calibration_records.insert_one(prod_doc)
        import asyncio
        asyncio.run(database_router.calibrations.save_calibration("hockey", "ELO", prod_doc.get("market", "default"), prod_doc))

        loaded = get_production_calibration("hockey", "ELO", "Moneyline")
        self.assertIsNotNone(loaded)

        # Verify all required fields from prompt:
        required_keys = [
            "calibration_id",
            "model",
            "sport",
            "market",
            "calibration_method",
            "training_dataset",
            "training_period",
            "validation_period",
            "created_at",
            "metrics",
            "calibration_error",
            "status",
            "approved_at",
            "approved_by",
            "feature_schema_version",
        ]
        for k in required_keys:
            self.assertIn(k, loaded, f"Persisted calibration must contain key '{k}'")

        self.assertEqual(loaded["status"], "PRODUCTION")
        self.assertEqual(loaded["approved_by"], "qa_pipeline_evaluator")

        # Test curve application at 0.40 -> 0.42
        cal_val = calibrate_probability(0.40, "hockey", "ELO", "Moneyline")
        self.assertAlmostEqual(cal_val, 0.42, places=2)

    # =========================================================================
    # Test 5: Missing production calibration fails closed
    # =========================================================================
    def test_missing_production_calibration_fails_closed(self):
        """
        When no approved production calibration exists:
        - Must abstain and fail closed.
        - Must NOT silently label raw probabilities as calibrated.
        """
        # Ensure database has NO calibration for 'formula_1'
        mongo_manager.calibration_records.delete_many({"sport": "formula_1"})

        # 1. calibrate_probability returns None
        cal_val = calibrate_probability(0.72, "formula_1", model="F1", market="RaceWinner", allow_baseline=False)
        self.assertIsNone(cal_val, "Must return None when production calibration is missing")

        # 2. get_calibration_details marks is_calibrated=False and status=MISSING_PRODUCTION_CALIBRATION
        details = get_calibration_details(0.72, "formula_1", model="F1", market="RaceWinner", allow_baseline=False)
        self.assertFalse(details["is_calibrated"])
        self.assertIsNone(details["calibrated_probability"])
        self.assertEqual(details["calibration_status"], "MISSING_PRODUCTION_CALIBRATION")

        # 3. Market publication gate fails closed
        val = validate_market_publication(
            {
                "marketName": "Race Winner",
                "selection": "Verstappen Win",
                "rawProbability": 0.72,
                "calibratedProbability": details["calibrated_probability"],
                "calibratedPercentage": details["calibrated_percentage"],
                "confidenceScore": 0.85,
                "hasSufficientData": True,
                "marketCategory": "RaceWinner",
                "isCalibrated": details["is_calibrated"],
                "calibrationStatus": details["calibration_status"],
            },
            sport="formula_1",
            is_subscriber_feed=True,
            active_model="F1",
        )
        self.assertFalse(val["isPublishable"])
        self.assertEqual(val["stopReason"], "UNCALIBRATED_ABSTAIN")

    # =========================================================================
    # Test 6: Explicit baseline mode kept outside production path
    # =========================================================================
    def test_explicit_baseline_mode_kept_outside_production_path(self):
        """
        When baseline mode is explicitly allowed (e.g. non-subscriber feed),
        it must be clearly marked as baseline, NOT calibrated, and rejected
        if attempted on subscriber feed.
        """
        mongo_manager.calibration_records.delete_many({"sport": "football"})
        from backend.engine.calibration import _PROD_CALIBRATION_CACHE
        _PROD_CALIBRATION_CACHE.clear()
        try:
            import asyncio
            from backend.db.database_router import database_router
            asyncio.run(database_router.calibrations.save_calibration("football", "ELO", "default", {"status": "DELETED", "thresholds_x": [], "thresholds_y": []}))
        except Exception:
            pass

        # Request with allow_baseline=True
        details = get_calibration_details(0.65, "football", model="ELO", market="default", allow_baseline=True)

        self.assertTrue(details["is_baseline"])
        self.assertFalse(details["is_calibrated"], "Baseline must NOT be marked as calibrated")
        self.assertEqual(details["calibration_status"], "UNVALIDATED_BASELINE")
        self.assertAlmostEqual(details["calibrated_probability"], 0.65, places=2)

        # Even with baseline numbers, subscriber publication gate rejects it
        val = validate_market_publication(
            {
                "marketName": "Win / Draw / Loss (1X2)",
                "selection": "Home Win",
                "rawProbability": 0.65,
                "calibratedProbability": details["calibrated_probability"],
                "calibratedPercentage": details["calibrated_percentage"],
                "confidenceScore": 0.70,
                "hasSufficientData": True,
                "marketCategory": "1X2",
                "isCalibrated": details["is_calibrated"], # False
                "calibrationStatus": details["calibration_status"], # UNVALIDATED_BASELINE
                "isBaseline": details["is_baseline"], # True
            },
            sport="football",
            is_subscriber_feed=True,
            active_model="ELO",
        )
        self.assertFalse(val["isPublishable"])
        self.assertEqual(val["stopReason"], "UNCALIBRATED_ABSTAIN")


if __name__ == "__main__":
    unittest.main()
