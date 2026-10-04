import unittest
import os
from unittest.mock import patch, MagicMock

from backend.db.duckdb_engine import DuckDBEngine, duckdb_engine
from backend.db.r2_storage import R2StorageManager, r2_manager
from backend.services.health_service import health_service


class TestFix3DuckDBBundledData(unittest.TestCase):
    def setUp(self):
        self.engine = DuckDBEngine()

    def test_1_duckdb_starts_and_discovers_bundled_parquet_files(self):
        """Verifies DuckDB initializes and discovers the 5 bundled Parquet datasets."""
        sports = ["football", "basketball", "baseball", "hockey", "formula_1"]
        for sp in sports:
            path = self.engine.find_bundled_parquet_path(sp)
            self.assertIsNotNone(path, f"Parquet file for {sp} must be discovered")
            self.assertTrue(os.path.exists(path), f"File {path} must exist on disk")

    def test_2_historical_views_are_available(self):
        """Verifies required historical views/tables are registered."""
        required_views = [
            "football_history",
            "basketball_history",
            "baseball_history",
            "hockey_history",
            "formula1_history",
        ]
        conn = self.engine.check_connection()
        registered = conn.get("registeredViews", [])
        for v in required_views:
            self.assertIn(v, registered, f"View {v} must be registered in DuckDB catalog")

    def test_3_catalog_row_counts_greater_than_zero(self):
        """Verifies DuckDB health reports dynamic catalog row counts > 0 from actual files."""
        conn = self.engine.check_connection()
        self.assertEqual(conn["status"], "connected")
        rows = conn["inMemoryCatalogRows"]
        self.assertGreater(rows, 0, "Catalog rows must be > 0 when bundled datasets exist")
        # Check individual sport counts
        for sp in ["football", "basketball", "baseball", "hockey"]:
            count = self.engine.get_sport_history_count(sp)
            self.assertGreater(count, 0, f"History count for {sp} should be > 0")

    def test_4_duplicate_registration_does_not_occur(self):
        """Verifies re-calling init_catalog does not duplicate view registrations."""
        initial_views = list(self.engine._registered_views)
        res = self.engine.init_catalog(force=False)
        self.assertEqual(res["status"], "already_initialized")
        self.assertEqual(self.engine._registered_views, initial_views)

    def test_5_r2_outage_does_not_destroy_local_historical_availability(self):
        """Verifies local bundled Parquet is served even when R2 is unavailable or unconfigured."""
        mgr = R2StorageManager()
        # Mock client to raise or return None simulating R2 outage
        with patch.object(mgr, "get_client", return_value=None):
            for sp in ["football", "basketball", "baseball", "hockey"]:
                local_p = mgr.get_local_parquet_path(sp, "v1")
                self.assertTrue(os.path.exists(local_p))
                self.assertGreater(os.path.getsize(local_p), 0)

    def test_6_no_select_star_in_application_queries(self):
        """Verifies application queries specify explicit column lists rather than SELECT *."""
        from backend.db.duckdb_engine import SPORT_COLUMNS
        for sp, cols in SPORT_COLUMNS.items():
            self.assertNotIn("*", cols, f"Sport {sp} columns must not contain SELECT *")
            self.assertIn("match_id", cols)
            self.assertIn("match_date", cols)


if __name__ == "__main__":
    unittest.main()
