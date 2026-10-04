import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None
    pq = None

# Ensure root import path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.config import settings
from backend.db.duckdb_engine import duckdb_engine
from backend.db.r2_storage import R2StorageManager
from backend.db.mongodb import mongo_manager
from backend.services.sync_service import sync_service
from backend.services.historical_ingestion_service import (
    historical_ingestion_service,
    FOOTBALL_SCHEMA,
    BASKETBALL_SCHEMA,
    BASEBALL_SCHEMA,
)
from backend.engine.historical_store import get_point_in_time_matches
from backend.engine.feature_builders import (
    build_football_features,
    build_basketball_features,
    build_baseball_features,
)
from backend.engine.pipeline import execute_prediction_pipeline
from backend.utils.text_normalize import normalize_team_name

@unittest.skipIf(pa is None, "pyarrow not installed")
class Step2HistoricalDataTestSuite(unittest.IsolatedAsyncioTestCase):

    def test_1_r2_storage_raises_runtime_error_no_fallback_seeding(self):
        """r2_storage.py must raise RuntimeError when dataset unavailable and never seed fake data"""
        mgr = R2StorageManager()
        # Point cache dir to an empty temporary location
        mgr.cache_dir = "/tmp/test_empty_r2_cache"
        os.makedirs(mgr.cache_dir, exist_ok=True)

        with self.assertRaises(RuntimeError) as ctx:
            mgr.get_local_parquet_path("football", "v999_nonexistent")
        self.assertIn("Required real historical football Parquet dataset is unavailable from R2", str(ctx.exception))

        # Confirm no fake file was created
        fake_file = os.path.join(mgr.cache_dir, "football_v999_nonexistent_history.parquet")
        self.assertFalse(os.path.exists(fake_file))

    def test_2_duckdb_team_history_count_and_normalized_keys(self):
        """duckdb_engine.get_team_history_count must return actual count using normalized team keys"""
        # Register a test view
        test_parquet = os.path.join("/tmp", "test_duckdb_history.parquet")
        schema = FOOTBALL_SCHEMA
        rows = [
            {
                "match_id": f"test_{i}",
                "match_date": f"2024-01-{10+i:02d}T15:00:00Z",
                "league": "Premier League",
                "home_team": "Arsenal FC" if i % 2 == 0 else "Chelsea FC",
                "away_team": "Liverpool FC" if i % 2 == 0 else "Arsenal FC",
                "home_team_key": "arsenal" if i % 2 == 0 else "chelsea",
                "away_team_key": "liverpool" if i % 2 == 0 else "arsenal",
                "home_score": 2,
                "away_score": 1,
                "status": "completed",
                "home_xg": None,
                "away_xg": None,
                "home_corners": None,
                "away_corners": None,
                "source": "test",
            }
            for i in range(10)
        ]
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, test_parquet)

        duckdb_engine.register_parquet_view("test_sport_matches", test_parquet)

        # Query using raw alias name "Arsenal" vs "Arsenal FC"
        cnt = duckdb_engine.get_team_history_count("test_sport", "Arsenal", "2024-01-25T00:00:00Z")
        self.assertEqual(cnt, 10)

        # Query with cutoff before all matches
        cnt_early = duckdb_engine.get_team_history_count("test_sport", "Arsenal", "2024-01-01T00:00:00Z")
        self.assertEqual(cnt_early, 0)

        # Query with mid cutoff (cutoff at 2024-01-15T00:00:00Z -> matches on 10, 11, 12, 13, 14 = 5 matches)
        cnt_mid = duckdb_engine.get_team_history_count("test_sport", "Arsenal", "2024-01-15T00:00:00Z")
        self.assertEqual(cnt_mid, 5)

    def test_3_historical_store_normalized_keys_and_null_preservation(self):
        """historical_store.get_point_in_time_matches queries DuckDB using normalized keys and keeps null stats"""
        pit = get_point_in_time_matches("test_sport", "2024-01-25T00:00:00Z", "Arsenal")
        self.assertEqual(len(pit), 10)
        for m in pit:
            self.assertEqual(m["status"], "completed")
            self.assertIsNone(m["homeXg"])
            self.assertIsNone(m["awayXg"])
            self.assertIsNone(m["homeCorners"])
            self.assertIsNone(m["awayCorners"])
            self.assertIn("homeTeamKey", m)
            self.assertIn("awayTeamKey", m)

    def test_4_dataset_verification_enforces_required_fields_and_completed(self):
        """_verify_dataset rejects missing fields, incomplete rows, or non-completed matches"""
        service = historical_ingestion_service

        # 0 rows fails
        with self.assertRaises(RuntimeError):
            service._verify_dataset("football", [])

        # Missing required field
        bad_row = {
            "match_id": "m1",
            "match_date": "2024-01-01T00:00:00Z",
            "home_team": "Team A",
            "away_team": "Team B",
            "home_team_key": "team_a",
            # missing away_team_key
            "home_score": 1,
            "away_score": 0,
            "status": "completed",
        }
        with self.assertRaises(ValueError):
            service._verify_dataset("football", [bad_row])

        # Status not completed
        uncompleted_row = {
            "match_id": "m1",
            "match_date": "2024-01-01T00:00:00Z",
            "home_team": "Team A",
            "away_team": "Team B",
            "home_team_key": "team_a",
            "away_team_key": "team_b",
            "home_score": 1,
            "away_score": 0,
            "status": "live",
        }
        with self.assertRaises(ValueError):
            service._verify_dataset("football", [uncompleted_row])

        # Valid row succeeds
        valid_row = {
            "match_id": "m1",
            "match_date": "2024-01-01T00:00:00Z",
            "home_team": "Team A",
            "away_team": "Team B",
            "home_team_key": "team_a",
            "away_team_key": "team_b",
            "home_score": 1,
            "away_score": 0,
            "status": "completed",
        }
        service._verify_dataset("football", [valid_row])

    def test_5_pipeline_rejects_insufficient_history_below_5_matches(self):
        """Fixtures where either team has < 5 historical matches get validationStatus='insufficient_data'"""
        cutoff = "2024-02-01T00:00:00Z"

        # Team with 0 matches
        pit_no_data = build_football_features("Unknown FC", "Arsenal", cutoff)
        self.assertFalse(pit_no_data["hasSufficientData"])
        self.assertEqual(pit_no_data["homeMatchesCount"], 0)

        # Test custom fixture through pipeline
        future_kickoff = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        custom_fixtures = [
            {
                "id": "fix_insufficient_1",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Unknown Home Team",
                "awayTeam": "Arsenal",
                "kickoffUtc": future_kickoff,
                "status": "upcoming",
            }
        ]
        res = execute_prediction_pipeline(
            active_model="ELO",
            is_subscriber_feed=True,
            custom_fixtures=custom_fixtures,
        )

        all_res = res.get("allResults", [])
        matched = [r for r in all_res if r["fixtureId"] == "fix_insufficient_1"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["validationStatus"], "insufficient_data")
        # Must NOT be published
        self.assertNotIn("fix_insufficient_1", [p["id"] for p in res.get("publishedFeed", [])])

    async def test_6_sync_service_refresh_diagnostics(self):
        """Refresh records diagnostics: row counts, team counts, rejected count"""
        record = await sync_service.execute_refresh()
        self.assertIsNotNone(record.diagnostics)
        diag = record.diagnostics

        self.assertIn("football history rows", diag)
        self.assertIn("basketball history rows", diag)
        self.assertIn("baseball history rows", diag)
        self.assertIn("home team history count", diag)
        self.assertIn("away team history count", diag)
        self.assertIn("fixtures rejected for insufficient history", diag)

        self.assertIsInstance(diag["football history rows"], int)
        self.assertIsInstance(diag["fixtures rejected for insufficient history"], int)

if __name__ == "__main__":
    unittest.main()
