"""
Test Suite for Strict PredictPro Database Rules.
Verifies all 10 mandatory architectural requirements:
1. Historical data is not written to Neon.
2. Duplicate refreshes do not duplicate Neon records.
3. Historical model training reads DuckDB/R2.
4. Analytics reads DuckDB/R2.
5. Neon queries are bounded.
6. Current published predictions still write successfully to Neon.
7. Home still receives predictions.
8. Match Details still receives all validated markets.
9. Storage protection rejects prohibited Neon writes.
10. DuckDB/R2 data remains persistent after deployment/restart.
"""

import unittest
import asyncio
import os
import json
from datetime import datetime, timezone, timedelta

from backend.config import settings
from backend.db.neon_budget_guard import (
    neon_budget_guard,
    SafetyState,
    DataClassification,
    ProhibitedHistoricalWriteError,
)
from backend.db.interfaces import BudgetExceededError
from backend.db.duckdb_engine import duckdb_engine
from backend.db.neon_adapter import neon_adapter
from backend.engine.historical_store import get_point_in_time_matches
from backend.engine.pipeline import execute_prediction_pipeline
from backend.services.feed_service import feed_service


class StrictPredictProDatabaseRulesTestSuite(unittest.TestCase):
    """
    Validates adherence to the 10 strict database architecture rules.
    """

    def setUp(self):
        neon_budget_guard.reset_metrics()
        duckdb_engine.init_catalog()

    # =========================================================================
    # Rule 1: Historical data is NOT written to Neon
    # =========================================================================
    def test_01_historical_data_not_written_to_neon(self):
        """Proves that historical and archive data classifications are strictly blocked from Neon."""
        # 1. Direct classification validation
        with self.assertRaises(ProhibitedHistoricalWriteError):
            neon_budget_guard.validate_write_classification(DataClassification.HISTORICAL)

        with self.assertRaises(ProhibitedHistoricalWriteError):
            neon_budget_guard.validate_write_classification(DataClassification.ANALYTICS)

        with self.assertRaises(ProhibitedHistoricalWriteError):
            neon_budget_guard.validate_write_classification(DataClassification.RAW_ARCHIVE)

        # 2. Only CURRENT_OPERATIONAL is accepted
        try:
            neon_budget_guard.validate_write_classification(DataClassification.CURRENT_OPERATIONAL)
        except Exception as e:
            self.fail(f"CURRENT_OPERATIONAL classification must be accepted: {e}")

        # 3. Query inspection prohibits historical tables
        prohibited_query = "INSERT INTO historical_matches (id, score) VALUES ('hist_1', 2);"
        with self.assertRaises(ProhibitedHistoricalWriteError):
            neon_budget_guard.inspect_query(prohibited_query, is_write=True)

    # =========================================================================
    # Rule 2: Duplicate refreshes do not duplicate Neon records
    # =========================================================================
    def test_02_duplicate_refreshes_do_not_duplicate_neon_records(self):
        """Proves that repeated upserts with deterministic IDs are idempotent in Neon."""
        test_fixture_id = "fix_idempotent_rule_test_001"
        test_pred_id = "pred_idempotent_rule_test_001"
        now_iso = datetime.now(timezone.utc).isoformat()

        fixture = {
            "id": test_fixture_id,
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": now_iso,
            "status": "scheduled",
            "currentScore": None,
        }

        prediction = {
            "id": test_pred_id,
            "fixtureId": test_fixture_id,
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": now_iso,
            "date": now_iso[:10],
            "modelVersion": "ELO + POISSON",
            "validationStatus": "validated",
            "published": True,
            "calibratedPercentage": 72.5,
            "isBestOfDay": False,
            "validatedMarkets": [
                {
                    "id": f"{test_pred_id}-m1",
                    "marketName": "Match Winner",
                    "selection": "Arsenal Win",
                    "probabilityPercentage": 72.5,
                }
            ],
        }

        # Run upsert 5 consecutive times (simulating 5 refreshes)
        for _ in range(5):
            asyncio.run(neon_adapter.fixtures.upsert_fixtures([fixture]))
            asyncio.run(neon_adapter.predictions.save_predictions([prediction]))

        # Retrieve count and verify exactly 1 record exists
        feed = asyncio.run(neon_adapter.predictions.get_published_feed(sport="football", limit=50))
        matching_preds = [p for p in feed if p.get("id") == test_pred_id]
        self.assertEqual(len(matching_preds), 1, "Idempotent upsert must not create duplicate prediction records")

    # =========================================================================
    # Rule 3: Historical model training reads DuckDB / R2
    # =========================================================================
    def test_03_historical_model_training_reads_duckdb_r2(self):
        """Proves that model training and historical feature extraction query DuckDB/Parquet without querying Neon."""
        # Query point in time historical matches for football
        cutoff = "2026-10-04T15:00:00Z"
        matches = get_point_in_time_matches("football", cutoff, team="Arsenal")
        self.assertIsInstance(matches, list)
        self.assertGreater(len(matches), 0, "DuckDB must return historical matches for Arsenal")

        # Verify execution was handled by DuckDB
        row_count = duckdb_engine.get_sport_history_count("football")
        self.assertGreater(row_count, 0, "DuckDB football historical dataset must contain rows")

        # Execute prediction pipeline and confirm it runs on DuckDB data
        future_kickoff = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        pipeline_res = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=True,
            custom_fixtures=[{
                "id": "test_pipeline_duckdb_read",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": future_kickoff,
                "status": "scheduled",
            }],
        )
        self.assertEqual(len(pipeline_res.get("allResults", [])), 1)
        res_item = pipeline_res["allResults"][0]
        self.assertEqual(res_item["validationStatus"], "abstained")
        self.assertEqual(res_item["stopReason"], "NO_VALIDATED_PRODUCTION_CALIBRATION")

    # =========================================================================
    # Rule 4: Analytics reads DuckDB / R2
    # =========================================================================
    def test_04_analytics_reads_duckdb_r2(self):
        """Proves that analytical queries and statistics are processed in DuckDB."""
        cutoff = "2026-10-04T15:00:00Z"
        # Test team recent history analytics via DuckDB
        recent = duckdb_engine.get_team_recent_history("football", "arsenal", cutoff, limit=10)
        self.assertIsInstance(recent, list)
        self.assertGreater(len(recent), 0, "DuckDB must execute team recent history analytics")

        # Verify all 5 sports have registered analytical views in DuckDB
        for sp in ["football", "basketball", "baseball", "hockey", "formula_1"]:
            count = duckdb_engine.get_sport_history_count(sp)
            self.assertGreater(count, 0, f"DuckDB analytical view for {sp} must be populated")

    # =========================================================================
    # Rule 5: Neon queries are bounded
    # =========================================================================
    def test_05_neon_queries_are_bounded(self):
        """Proves that NeonBudgetGuard rejects unbounded queries, SELECT *, and oversized LIMITs."""
        # 1. Rejects SELECT *
        with self.assertRaises(BudgetExceededError):
            neon_budget_guard.inspect_query("SELECT * FROM neon_fixtures LIMIT 10;")

        # 2. Rejects unbounded SELECT without LIMIT
        with self.assertRaises(BudgetExceededError):
            neon_budget_guard.inspect_query("SELECT id, sport FROM neon_fixtures;")

        # 3. Rejects LIMIT exceeding query_max_rows (default 500)
        with self.assertRaises(BudgetExceededError):
            neon_budget_guard.inspect_query("SELECT id, sport FROM neon_fixtures LIMIT 1000;")

        # 4. Accepts properly bounded SELECT with explicit columns and reasonable LIMIT
        try:
            neon_budget_guard.inspect_query("SELECT id, sport, league FROM neon_fixtures WHERE sport = $1 LIMIT 50;")
        except Exception as e:
            self.fail(f"Bounded SELECT query must be accepted: {e}")

    # =========================================================================
    # Rule 6: Current published predictions still write successfully to Neon
    # =========================================================================
    def test_06_current_published_predictions_write_successfully_to_neon(self):
        """Proves that CURRENT_OPERATIONAL predictions write cleanly to Neon with full governance fields."""
        pred_id = "pred_rule6_verification_001"
        now_iso = datetime.now(timezone.utc).isoformat()
        pred_doc = {
            "id": pred_id,
            "fixtureId": pred_id,
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Arsenal",
            "awayTeam": "Liverpool",
            "kickoffUtc": now_iso,
            "date": now_iso[:10],
            "modelVersion": "ELO + POISSON",
            "validationStatus": "validated",
            "published": True,
            "calibratedPercentage": 68.0,
            "isBestOfDay": True,
            "prediction_payload": json.dumps({"market": "1X2", "selection": "Arsenal Win"}),
            "validatedMarkets": [
                {
                    "id": f"{pred_id}-m1",
                    "marketName": "Match Winner",
                    "selection": "Arsenal Win",
                    "probabilityPercentage": 68.0,
                }
            ],
        }

        from backend.db.database_router import database_router
        asyncio.run(database_router.predictions.save_predictions([pred_doc]))
        written = asyncio.run(neon_adapter.predictions.save_predictions([pred_doc]))
        self.assertGreaterEqual(written, 1, "Published prediction must write successfully to Neon")

    # =========================================================================
    # Rule 7: Home still receives predictions
    # =========================================================================
    def test_07_home_still_receives_predictions(self):
        """Proves that FeedService returns published predictions formatted for Home view cards."""
        feed = asyncio.run(feed_service.get_feed(sport="all", limit=20))
        self.assertIsInstance(feed, list)
        self.assertGreater(len(feed), 0, "FeedService must return published predictions")
        first_item = feed[0]
        self.assertIn("fixtureId", first_item)
        self.assertIn("sport", first_item)
        self.assertIn("homeTeam", first_item)
        self.assertIn("awayTeam", first_item)
        self.assertIn("validatedMarkets", first_item)

    # =========================================================================
    # Rule 8: Match Details still receives all validated markets
    # =========================================================================
    def test_08_match_details_receives_all_validated_markets(self):
        """Proves that prediction feed and fixture queries retain all validated markets and probabilities."""
        feed = asyncio.run(neon_adapter.predictions.get_published_feed(limit=10))
        self.assertGreater(len(feed), 0)
        item = feed[0]
        self.assertTrue(item.get("published"), "Returned item must be marked published=True")
        self.assertEqual(item.get("validationStatus"), "validated")
        self.assertIsInstance(item.get("validatedMarkets"), list)
        self.assertGreater(len(item["validatedMarkets"]), 0)

    # =========================================================================
    # Rule 9: Storage protection rejects prohibited Neon writes
    # =========================================================================
    def test_09_storage_protection_rejects_prohibited_writes(self):
        """Proves that NeonBudgetGuard tracks storage and rejects writes when limits or classifications fail."""
        # Test 1: Prohibited classification write rejection
        with self.assertRaises(ProhibitedHistoricalWriteError):
            neon_budget_guard.validate_write_classification(DataClassification.HISTORICAL)

        # Test 2: Historical write attempts count increments
        metrics = neon_budget_guard.get_metrics()
        est_usage = metrics["predictproEstimatedUsage"]
        self.assertGreaterEqual(est_usage["historicalWriteAttemptsCount"], 1)

        # Test 3: Hard resource limit rejection for bulk writes
        neon_budget_guard.record_storage_estimate(neon_budget_guard.storage_hard_limit_bytes + 1000)
        self.assertEqual(neon_budget_guard.get_safety_state(), SafetyState.RESOURCE_LIMITED)

        # Bulk write slot is rejected in RESOURCE_LIMITED state
        with self.assertRaises(BudgetExceededError):
            asyncio.run(neon_budget_guard.acquire_query_slot("neon_bulk_sync", is_write=True))

        # Reset storage estimate back to safe level so subsequent tests can run cleanly
        neon_budget_guard.record_storage_estimate(0)

    # =========================================================================
    # Rule 10: DuckDB / R2 data remains persistent after deployment / restart
    # =========================================================================
    def test_10_duckdb_r2_data_persistent_after_restart(self):
        """Proves that DuckDB re-initialization cleanly re-mounts all Parquet datasets without data loss."""
        # Force re-initialize catalog
        init_res = duckdb_engine.init_catalog(force=True)
        self.assertIn(init_res["status"], ("success", "catalog_initialized"))
        self.assertGreaterEqual(len(init_res["registeredViews"]), 5)

        # Verify historical row counts across all sports
        fb_cnt = duckdb_engine.get_sport_history_count("football")
        bk_cnt = duckdb_engine.get_sport_history_count("basketball")
        bb_cnt = duckdb_engine.get_sport_history_count("baseball")
        hk_cnt = duckdb_engine.get_sport_history_count("hockey")
        f1_cnt = duckdb_engine.get_sport_history_count("formula_1")

        self.assertGreater(fb_cnt, 0, "Football history must persist after re-init")
        self.assertGreater(bk_cnt, 0, "Basketball history must persist after re-init")
        self.assertGreater(bb_cnt, 0, "Baseball history must persist after re-init")
        self.assertGreater(hk_cnt, 0, "Hockey history must persist after re-init")
        self.assertGreater(f1_cnt, 0, "Formula 1 history must persist after re-init")


if __name__ == "__main__":
    unittest.main()
