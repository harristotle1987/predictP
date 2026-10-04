import os
import sys
import unittest
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None
    pq = None

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.db.duckdb_engine import duckdb_engine
from backend.services.historical_ingestion_service import FOOTBALL_SCHEMA
from backend.engine.historical_store import get_point_in_time_matches
from backend.engine.feature_builders import build_football_features
from backend.engine.football_elo_engine import run_football_elo_engine as run_elo_engine
from backend.engine.football_poisson_engine import run_football_poisson_engine as run_poisson_engine
from backend.engine.glicko2_engine import run_glicko2_engine
from backend.engine.gradient_boosting_engine import run_gradient_boosting_engine
from backend.utils.text_normalize import normalize_team_name

@unittest.skipIf(pa is None, "pyarrow not installed")
class Step3NormalizedIdentityTestSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Create a parquet file with 6 matches for Manchester United, using alternating raw names "Manchester United" and "Manchester United FC"
        # and 6 matches for Liverpool, using "Liverpool FC" and "Liverpool"
        test_parquet = os.path.join("/tmp", "test_step3_history.parquet")
        schema = FOOTBALL_SCHEMA
        rows = []

        # 10 matches for Man Utd vs Chelsea
        for i in range(10):
            mu_name = "Manchester United" if i % 2 == 0 else "Manchester United FC"
            rows.append({
                "match_id": f"mu_match_{i}",
                "match_date": f"2024-01-{10+i:02d}T15:00:00Z",
                "league": "Premier League",
                "home_team": mu_name,
                "away_team": "Chelsea FC",
                "home_team_key": normalize_team_name(mu_name),
                "away_team_key": normalize_team_name("Chelsea FC"),
                "home_score": 2,
                "away_score": 1,
                "status": "completed",
                "home_xg": 1.8,
                "away_xg": 1.0,
                "home_corners": 6,
                "away_corners": 4,
                "source": "test",
            })

        # 10 matches for Liverpool vs Arsenal
        for i in range(10):
            liv_name = "Liverpool FC" if i % 2 == 0 else "Liverpool"
            rows.append({
                "match_id": f"liv_match_{i}",
                "match_date": f"2024-01-{10+i:02d}T15:00:00Z",
                "league": "Premier League",
                "home_team": liv_name,
                "away_team": "Arsenal FC",
                "home_team_key": normalize_team_name(liv_name),
                "away_team_key": normalize_team_name("Arsenal FC"),
                "home_score": 3,
                "away_score": 0,
                "status": "completed",
                "home_xg": 2.2,
                "away_xg": 0.5,
                "home_corners": 8,
                "away_corners": 3,
                "source": "test",
            })

        # 10 matches for Chelsea vs Arsenal so opponents also have history
        for i in range(10):
            rows.append({
                "match_id": f"opp_match_{i}",
                "match_date": f"2024-01-{10+i:02d}T15:00:00Z",
                "league": "Premier League",
                "home_team": "Chelsea FC",
                "away_team": "Arsenal FC",
                "home_team_key": normalize_team_name("Chelsea FC"),
                "away_team_key": normalize_team_name("Arsenal FC"),
                "home_score": 1,
                "away_score": 1,
                "status": "completed",
                "home_xg": 1.1,
                "away_xg": 1.1,
                "home_corners": 5,
                "away_corners": 5,
                "source": "test",
            })

        from backend.config import settings
        real_p = os.path.join(settings.parquet_cache_dir, "football_v1_history.parquet")
        if os.path.exists(real_p):
            duckdb_engine.register_parquet_view("football_matches", real_p)
        else:
            table = pa.Table.from_pylist(rows, schema=schema)
            pq.write_table(table, test_parquet)
            duckdb_engine.register_parquet_view("football_matches", test_parquet)

    def test_normalization_identity(self):
        """Verify 'Manchester United' and 'Manchester United FC' normalize to the same identity key"""
        key1 = normalize_team_name("Manchester United")
        key2 = normalize_team_name("Manchester United FC")
        self.assertEqual(key1, key2)
        self.assertEqual(key1, "manchester united")

    def test_historical_store_resolves_different_display_names(self):
        """Verify historical store finds matches regardless of whether queried with 'Manchester United' or 'Manchester United FC'"""
        cutoff = "2024-01-25T00:00:00Z"
        matches_raw = get_point_in_time_matches(cutoff, "football", "Manchester United")
        matches_fc = get_point_in_time_matches(cutoff, "football", "Manchester United FC")

        self.assertGreaterEqual(len(matches_raw), 10)
        self.assertEqual(len(matches_raw), len(matches_fc))
        self.assertEqual([m["id"] for m in matches_raw], [m["id"] for m in matches_fc])

        for m in matches_raw:
            self.assertTrue(m["homeTeamKey"] == "manchester united" or m["awayTeamKey"] == "manchester united")

    def test_feature_builder_satisfies_5_match_threshold(self):
        """Verify feature_builders recognizes 5+ matches for both 'Manchester United' and 'Liverpool'"""
        cutoff = "2024-01-25T00:00:00Z"
        fb = build_football_features("Manchester United", "Liverpool FC", cutoff)
        self.assertTrue(fb["hasSufficientData"])
        self.assertGreaterEqual(fb["homeMatchesCount"], 5)
        self.assertGreaterEqual(fb["awayMatchesCount"], 5)

        fb_alt = build_football_features("Manchester United FC", "Liverpool", cutoff)
        self.assertTrue(fb_alt["hasSufficientData"])
        self.assertGreaterEqual(fb_alt["homeMatchesCount"], 5)
        self.assertGreaterEqual(fb_alt["awayMatchesCount"], 5)

    def test_model_engines_recognize_normalized_team_identities(self):
        """Verify ELO, Poisson, Glicko2, and GB engines recognize 5+ matches despite naming differences"""
        cutoff = "2024-01-25T00:00:00Z"

        elo_res = run_elo_engine("football", "Manchester United FC", "Liverpool", cutoff)
        self.assertTrue(elo_res["hasSufficientData"])

        poisson_res = run_poisson_engine("football", "Manchester United", "Liverpool FC", cutoff)
        self.assertTrue(poisson_res["hasSufficientData"])

        glicko_res = run_glicko2_engine("football", "Manchester United FC", "Liverpool FC", cutoff)
        self.assertTrue(glicko_res["hasSufficientData"])

        fb = build_football_features("Manchester United FC", "Liverpool FC", cutoff)
        gb_res = run_gradient_boosting_engine("football", "Manchester United", "Liverpool", cutoff, fb)
        self.assertTrue(gb_res["hasSufficientData"])

if __name__ == "__main__":
    unittest.main()
