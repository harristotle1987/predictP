import unittest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from backend.services.competition_registry import (
    competition_registry_service,
    COMPETITION_CACHE_TTL_SECONDS,
    DEFAULT_BASELINE_COMPETITIONS,
)
from backend.services.sync_service import SyncService
from backend.services.feed_service import get_current_lagos_today, LAGOS_TZ
from backend.db.mongodb import mongo_manager
from backend.db.redis_client import redis_client
try:
    from fastapi.testclient import TestClient
    from backend.app import app
    HAS_FASTAPI = True
except ImportError:
    TestClient = None
    app = None
    HAS_FASTAPI = False
from backend.config import settings

class TestCompetitionAwareRefresh(unittest.TestCase):
    def setUp(self):
        self.sync_service = SyncService()
        self.client = TestClient(app) if HAS_FASTAPI and app is not None else None

    def test_competition_registry_discovery_and_caching(self):
        """
        Verifies that competition discovery returns normalized competitions,
        caches them in Redis/MongoDB, and avoids re-querying provider on subsequent calls.
        """
        # 1. Discover competitions
        comps = asyncio.run(competition_registry_service.get_or_discover_competitions("football", force=True))
        self.assertIsInstance(comps, list)
        self.assertGreater(len(comps), 0)

        # Check required fields
        comp_ids = [c["competition_id"] for c in comps]
        self.assertIn("uefa-nations-league", comp_ids)
        self.assertIn("premier-league", comp_ids)
        self.assertIn("champions-league", comp_ids)
        
        sample = next(c for c in comps if c["competition_id"] == "uefa-nations-league")
        self.assertIn("provider", sample)
        self.assertIn("sport", sample)
        self.assertEqual(sample["sport"], "football")
        self.assertIn("competition_name", sample)
        self.assertIn("country_or_region", sample)
        self.assertIn("level", sample)
        self.assertIn("active", sample)

        # 2. Subsequent call without force uses cache (provider is not called)
        with patch.object(competition_registry_service, "_discover_football_competitions") as mock_disc:
            cached_comps = asyncio.run(competition_registry_service.get_or_discover_competitions("football", force=False))
            mock_disc.assert_not_called()
            self.assertEqual(len(cached_comps), len(comps))

    def test_modular_sport_isolation(self):
        """
        Verifies that requesting a single sport (e.g. football) only invokes football
        and does not trigger basketball, baseball, hockey, or F1.
        """
        today_lagos = get_current_lagos_today()
        with patch.object(self.sync_service, "refresh_football", return_value={"sport": "football", "fixtures": [], "fetched_count": 0, "calls_avoided": 0, "queried_dates": [today_lagos], "errors": []}) as mock_fb, \
             patch.object(self.sync_service, "refresh_basketball") as mock_bk, \
             patch.object(self.sync_service, "refresh_baseball") as mock_bb, \
             patch.object(self.sync_service, "refresh_hockey") as mock_hk, \
             patch.object(self.sync_service, "refresh_f1") as mock_f1:

            record = asyncio.run(self.sync_service.execute_refresh(sports=["football"], date=today_lagos))
            
            mock_fb.assert_called_once()
            mock_bk.assert_not_called()
            mock_bb.assert_not_called()
            mock_hk.assert_not_called()
            mock_f1.assert_not_called()

            self.assertEqual(record.diagnostics["requested_sports"], ["football"])

    def test_date_first_fetching_and_freshness_skip(self):
        """
        Verifies date-first targeted fetching and that fresh data avoids duplicate provider calls
        unless force=True.
        """
        today_lagos = get_current_lagos_today()
        test_fixture = {
            "id": "fb_fresh_test_1",
            "source_event_id": "fresh_test_1",
            "sport": "football",
            "league": "Premier League",
            "competition_id": "premier-league",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": f"{today_lagos}T15:00:00Z",
            "scheduled_at": f"{today_lagos}T15:00:00Z",
            "status": "scheduled",
            "currentScore": {"home": 0, "away": 0, "display": "0 - 0"},
        }
        mongo_manager.operational_events.update_one({"id": "fb_fresh_test_1"}, {"$set": test_fixture}, upsert=True)

        # Mark as fresh
        asyncio.run(self.sync_service.mark_sport_date_fresh("football", today_lagos, 1))
        self.assertTrue(asyncio.run(self.sync_service.is_sport_date_fresh("football", today_lagos)))

        # Normal refresh should avoid provider call
        with patch("backend.providers.football_adapter.football_adapter.fetch_fixtures") as mock_fetch:
            res = asyncio.run(self.sync_service.refresh_football([today_lagos], force=False))
            mock_fetch.assert_not_called()
            self.assertEqual(res["calls_avoided"], 1)

        # Force refresh should bypass freshness cache and call provider
        with patch("backend.providers.football_adapter.football_adapter.fetch_fixtures", return_value=[test_fixture]) as mock_fetch:
            res_force = asyncio.run(self.sync_service.refresh_football([today_lagos], force=True))
            mock_fetch.assert_called_once()
            self.assertEqual(res_force["calls_avoided"], 0)

    def test_fixture_deduplication(self):
        """
        Verifies that multiple occurrences of the same fixture across providers or calls
        are deduplicated based on canonical identity.
        """
        today_lagos = get_current_lagos_today()
        f1 = {
            "id": "fb_prov1_100",
            "source_event_id": "100",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Liverpool",
            "awayTeam": "Manchester City",
            "kickoffUtc": f"{today_lagos}T16:30:00Z",
            "status": "scheduled",
            "currentScore": {"home": 0, "away": 0, "display": "0 - 0"},
        }
        f2 = {
            "id": "fb_prov2_100",
            "source_event_id": "100",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Liverpool",
            "awayTeam": "Manchester City",
            "kickoffUtc": f"{today_lagos}T16:30:00Z",
            "status": "live",
            "currentScore": {"home": 1, "away": 0, "display": "1 - 0"},
        }

        with patch.object(self.sync_service, "refresh_football", return_value={"sport": "football", "fixtures": [f1, f2], "fetched_count": 2, "calls_avoided": 0, "queried_dates": [today_lagos], "errors": []}):
            with patch("backend.services.sync_service.execute_prediction_pipeline", return_value={"publishedFeed": [], "allResults": [], "diagnostics": {"fixturesEligible": 1}}):
                record = asyncio.run(self.sync_service.execute_refresh(sports=["football"], date=today_lagos, force=True))
                self.assertEqual(record.diagnostics["fixtures_deduplicated_count"], 1)
                self.assertEqual(record.matches_synced, 1)

    def test_admin_refresh_authentication_and_target_body(self):
        """
        Verifies that /api/admin/refresh enforces ADMIN_API_KEY and accepts targeted JSON payload.
        """
        # 1. Unauthenticated -> 401
        if not self.client:
            self.skipTest("FastAPI not installed in runtime environment")
        res_unauth = self.client.post("/api/admin/refresh", json={"sports": ["football"], "force": True})
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Authenticated with targeted parameters -> 200
        admin_key = settings.admin_api_key or "test_admin_key"
        with patch.object(settings, "admin_api_key", admin_key):
            with patch.object(self.sync_service, "execute_refresh") as mock_exec:
                mock_exec.return_value = MagicMock(model_dump=lambda: {
                    "id": "run_test123",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "completed",
                    "matches_synced": 5,
                    "predictions_published": 5,
                    "duration_ms": 120.0,
                    "errors": [],
                    "diagnostics": {
                        "refresh_type": "force",
                        "requested_sports": ["football"],
                    },
                })
                res_auth = self.client.post(
                    "/api/admin/refresh",
                    json={"sports": ["football"], "force": True},
                    headers={"x-admin-api-key": admin_key},
                )
                self.assertEqual(res_auth.status_code, 200)

    def test_public_competitions_endpoint(self):
        """
        Verifies that /api/competitions is publicly readable and returns registered competitions.
        """
        if not self.client:
            self.skipTest("FastAPI not installed in runtime environment")
        res = self.client.get("/api/competitions?sport=football")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["sport"], "football")
        self.assertIn("competitions", data)
        self.assertGreater(data["count"], 0)

if __name__ == "__main__":
    unittest.main()
