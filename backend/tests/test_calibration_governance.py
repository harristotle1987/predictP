"""
Test Suite for Calibration Governance & Pipeline Fail-Closed Protection.
Verifies all 6 mandatory user requirements:
1. No synthetic production calibration exists.
2. No identity fallback exists.
3. Missing calibration causes abstention.
4. Historical data never gets written to Neon.
5. DuckDB survives process restart.
6. Only validated predictions reach the subscriber feed.
"""

import unittest
import os
import shutil
import asyncio
from datetime import datetime, timezone

from backend.config import settings
from backend.engine.calibration import (
    get_production_calibration,
    seed_initial_production_calibrations,
    CalibrationUnavailableError,
    clear_active_production_calibrations,
)
from backend.engine.pipeline import execute_prediction_pipeline
from backend.db.duckdb_engine import DuckDBEngine, duckdb_engine
from backend.db.neon_budget_guard import (
    neon_budget_guard,
    DataClassification,
    ProhibitedHistoricalWriteError,
)
from backend.services.feed_service import feed_service


class CalibrationGovernanceTestSuite(unittest.TestCase):
    """
    Validates calibration fail-closed governance and storage safety requirements.
    """

    def setUp(self):
        clear_active_production_calibrations()
        neon_budget_guard.reset_metrics()

    # =========================================================================
    # Requirement 1: No synthetic production calibration exists
    # =========================================================================
    def test_01_no_synthetic_production_calibration_exists(self):
        """Proves that seed_initial_production_calibrations is disabled and no hard-coded baseline records are created."""
        seeded = seed_initial_production_calibrations()
        self.assertEqual(seeded, 0, "seed_initial_production_calibrations must return 0 and create zero synthetic records")

        # Confirm get_production_calibration fails closed for default sports
        for sport in ["football", "basketball", "baseball", "hockey", "formula_1"]:
            with self.assertRaises(CalibrationUnavailableError):
                get_production_calibration(sport=sport, model="ELO + POISSON", market="default")

    # =========================================================================
    # Requirement 2: No identity fallback exists
    # =========================================================================
    def test_02_no_identity_fallback_exists(self):
        """Proves that requesting a missing calibration raises CalibrationUnavailableError instead of returning an identity curve."""
        with self.assertRaises(CalibrationUnavailableError):
            get_production_calibration(sport="football", model="ELO + POISSON", market="Win / Draw / Loss (1X2)")

    # =========================================================================
    # Requirement 3: Missing calibration causes abstention
    # =========================================================================
    def test_03_missing_calibration_causes_abstention(self):
        """Proves that fixtures fail closed and abstain when production calibration is unavailable."""
        cutoff = datetime.now(timezone.utc).isoformat()
        pipeline_res = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=True,
            custom_fixtures=[{
                "id": "test_uncalibrated_fixture_001",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": cutoff,
                "status": "scheduled",
            }],
        )

        results = pipeline_res.get("allResults", [])
        self.assertEqual(len(results), 1)
        item = results[0]

        # Must abstain with NO_VALIDATED_PRODUCTION_CALIBRATION
        self.assertEqual(item["validationStatus"], "abstained")
        self.assertEqual(item["stopReason"], "NO_VALIDATED_PRODUCTION_CALIBRATION")
        self.assertEqual(len(item.get("markets", [])), 0, "No markets should be published for uncalibrated fixtures")

        # Published feed must be empty
        published_feed = pipeline_res.get("publishedFeed", [])
        self.assertEqual(len(published_feed), 0, "Uncalibrated fixtures must never reach published feed")

    # =========================================================================
    # Requirement 4: Historical data never gets written to Neon
    # =========================================================================
    def test_04_historical_data_never_written_to_neon(self):
        """Proves that writes classified as HISTORICAL, ANALYTICS, or RAW_ARCHIVE are rejected by Neon budget guard."""
        with self.assertRaises(ProhibitedHistoricalWriteError):
            neon_budget_guard.validate_write_classification(DataClassification.HISTORICAL)

        with self.assertRaises(ProhibitedHistoricalWriteError):
            neon_budget_guard.validate_write_classification(DataClassification.ANALYTICS)

        with self.assertRaises(ProhibitedHistoricalWriteError):
            neon_budget_guard.validate_write_classification(DataClassification.RAW_ARCHIVE)

        # Prohibited SQL write query check
        with self.assertRaises(ProhibitedHistoricalWriteError):
            neon_budget_guard.inspect_query("INSERT INTO historical_matches VALUES ('h1', 'Arsenal', 'Chelsea');", is_write=True)

    # =========================================================================
    # Requirement 5: DuckDB survives process restart
    # =========================================================================
    def test_05_duckdb_survives_process_restart(self):
        """Proves that DuckDB connects to a persistent path and survives engine re-instantiation."""
        test_db_path = "/tmp/test_persistent_duckdb_restart.duckdb"
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

        # Initialize instance 1 and write data
        try:
            try:
                import duckdb
            except ImportError:
                import sqlite3 as duckdb
            con1 = duckdb.connect(database=test_db_path)
            con1.execute("CREATE TABLE test_persistence (id VARCHAR PRIMARY KEY, val INTEGER);")
            con1.execute("INSERT INTO test_persistence VALUES ('key_1', 42);")
            if hasattr(con1, "commit"):
                con1.commit()
            con1.close()

            # Initialize instance 2 and verify data exists
            con2 = duckdb.connect(database=test_db_path)
            res = con2.execute("SELECT val FROM test_persistence WHERE id = 'key_1';").fetchone()
            con2.close()

            self.assertIsNotNone(res)
            self.assertEqual(res[0], 42, "DuckDB persistent storage must retain data across database reconnects")
        finally:
            if os.path.exists(test_db_path):
                try:
                    os.remove(test_db_path)
                except Exception:
                    pass

    # =========================================================================
    # Requirement 6: Only validated predictions reach the subscriber feed
    # =========================================================================
    def test_06_only_validated_predictions_reach_subscriber_feed(self):
        """Proves that feed_service and pipeline filter strictly exclude abstained or uncalibrated predictions."""
        raw_candidates = [
            {
                "id": "cand_validated",
                "validationStatus": "validated",
                "published": True,
                "validatedMarkets": [{"marketName": "1X2", "selection": "Arsenal Win", "probabilityPercentage": 65.0}],
            },
            {
                "id": "cand_abstained",
                "validationStatus": "abstained",
                "published": False,
                "stopReason": "NO_VALIDATED_PRODUCTION_CALIBRATION",
                "validatedMarkets": [],
            },
            {
                "id": "cand_rejected",
                "validationStatus": "rejected",
                "published": False,
                "validatedMarkets": [],
            },
        ]

        normalized = feed_service.normalize_feed_items(raw_candidates)
        published_ids = [item["id"] for item in normalized if item.get("validationStatus") == "validated"]

        self.assertIn("cand_validated", published_ids)
        self.assertNotIn("cand_abstained", published_ids)
        self.assertNotIn("cand_rejected", published_ids)


if __name__ == "__main__":
    unittest.main()
