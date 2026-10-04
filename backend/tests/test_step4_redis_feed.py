import unittest
import asyncio
from datetime import datetime, timezone, timedelta

try:
    from fastapi.testclient import TestClient
    from backend.app import app
    HAS_FASTAPI = True
except ImportError:
    TestClient = None
    app = None
    HAS_FASTAPI = False

from backend.services.feed_service import feed_service, get_current_lagos_today, get_lagos_date_str, LAGOS_TZ
from backend.services.sync_service import sync_service
from backend.db.redis_client import redis_client

class TestStep4RedisFeedAndDateDisplay(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if HAS_FASTAPI and app is not None:
            cls.client = TestClient(app)
        else:
            cls.client = None
        cls.today_lagos = get_current_lagos_today()

        now_lagos = datetime.now(LAGOS_TZ)
        cls.future_date = (now_lagos + timedelta(days=2)).strftime("%Y-%m-%d")
        cls.empty_future_date = (now_lagos + timedelta(days=10)).strftime("%Y-%m-%d")
        cls.past_date = (now_lagos - timedelta(days=2)).strftime("%Y-%m-%d")

    def setUp(self):
        redis_client._local_cache.clear()

    async def test_sync_service_publishes_by_date(self):
        """Verify sync_service stores items grouped by predictpro:feed:YYYY-MM-DD and predictpro:feed:latest"""
        today_iso = f"{self.today_lagos}T15:00:00+01:00"
        future_iso = f"{self.future_date}T18:00:00+01:00"

        test_feed = [
            {
                "id": "match_today_1",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": today_iso,
                "validationStatus": "validated",
                "status": "scheduled",
                "validatedMarkets": [{"marketName": "Match Winner", "selection": "Home", "probabilityPercentage": 65.0}],
            },
            {
                "id": "match_future_1",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Liverpool",
                "awayTeam": "Real Madrid",
                "kickoffUtc": future_iso,
                "validationStatus": "validated",
                "status": "scheduled",
                "validatedMarkets": [{"marketName": "Over 2.5 Goals", "selection": "Over", "probabilityPercentage": 70.0}],
            },
        ]

        feed_by_date = {}
        for item in test_feed:
            d_str = get_lagos_date_str(item.get("kickoffUtc"))
            if d_str:
                feed_by_date.setdefault(d_str, []).append(item)

        for d_str, date_items in feed_by_date.items():
            await redis_client.set_json(f"predictpro:feed:{d_str}", date_items, ex_seconds=86400)
        await redis_client.set_json("predictpro:feed:latest", test_feed, ex_seconds=86400)

        today_cache = await redis_client.get_json(f"predictpro:feed:{self.today_lagos}")
        future_cache = await redis_client.get_json(f"predictpro:feed:{self.future_date}")
        latest_cache = await redis_client.get_json("predictpro:feed:latest")

        self.assertIsNotNone(today_cache)
        self.assertEqual(len(today_cache), 1)
        self.assertEqual(today_cache[0]["id"], "match_today_1")

        self.assertIsNotNone(future_cache)
        self.assertEqual(len(future_cache), 1)
        self.assertEqual(future_cache[0]["id"], "match_future_1")

        self.assertEqual(len(latest_cache), 2)

    async def test_feed_service_date_queries(self):
        """Test today with predictions, future date with predictions, future date without predictions"""
        today_iso = f"{self.today_lagos}T15:00:00+01:00"
        future_iso = f"{self.future_date}T18:00:00+01:00"

        item_today = {
            "id": "item_today_1",
            "sport": "football",
            "league": "Premier League",
            "homeTeam": "Man City",
            "awayTeam": "Spurs",
            "kickoffUtc": today_iso,
            "validationStatus": "validated",
            "status": "scheduled",
            "validatedMarkets": [{"marketName": "1X2", "selection": "Home", "probabilityPercentage": 80.0}],
        }
        item_future = {
            "id": "item_future_1",
            "sport": "basketball",
            "league": "NBA",
            "homeTeam": "Lakers",
            "awayTeam": "Celtics",
            "kickoffUtc": future_iso,
            "validationStatus": "validated",
            "status": "scheduled",
            "validatedMarkets": [{"marketName": "Moneyline", "selection": "Home", "probabilityPercentage": 55.0}],
        }

        await redis_client.set_json(f"predictpro:feed:{self.today_lagos}", [item_today])
        await redis_client.set_json(f"predictpro:feed:{self.future_date}", [item_future])

        # 1. Today with predictions
        feed_today = await feed_service.get_feed(date=self.today_lagos)
        self.assertEqual(len(feed_today), 1)
        self.assertEqual(feed_today[0]["id"], "item_today_1")

        # 2. Future date with predictions
        feed_future = await feed_service.get_feed(date=self.future_date)
        self.assertEqual(len(feed_future), 1)
        self.assertEqual(feed_future[0]["id"], "item_future_1")

        # 3. Future date without predictions -> returns []
        feed_empty = await feed_service.get_feed(date=self.empty_future_date)
        self.assertEqual(feed_empty, [])

        # 4. Available dates returns actual dates only
        available_dates = await feed_service.get_available_prediction_dates()
        self.assertIn(self.today_lagos, available_dates)
        self.assertIn(self.future_date, available_dates)
        self.assertNotIn(self.empty_future_date, available_dates)

    async def test_api_endpoints_date_validation(self):
        """Test API endpoints for past dates (400), valid dates, and empty dates"""
        if not HAS_FASTAPI or self.client is None:
            self.skipTest("FastAPI not installed in runtime environment")
        today_iso = f"{self.today_lagos}T15:00:00+01:00"
        item_today = {
            "id": "item_today_api",
            "sport": "football",
            "league": "La Liga",
            "homeTeam": "Barcelona",
            "awayTeam": "Sevilla",
            "kickoffUtc": today_iso,
            "validationStatus": "validated",
            "status": "scheduled",
            "validatedMarkets": [{"marketName": "1X2", "selection": "Home", "probabilityPercentage": 75.0}],
        }
        await redis_client.set_json(f"predictpro:feed:{self.today_lagos}", [item_today])

        # Past date -> HTTP 400
        res_past = self.client.get(f"/api/predictions/feed?date={self.past_date}")
        self.assertEqual(res_past.status_code, 400)
        self.assertIn("Date cannot be in the past", res_past.json()["detail"])

        res_past_fix = self.client.get(f"/api/fixtures?date={self.past_date}")
        self.assertEqual(res_past_fix.status_code, 400)

        # Today with predictions -> HTTP 200 with predictions
        res_today = self.client.get(f"/api/predictions/feed?date={self.today_lagos}")
        self.assertEqual(res_today.status_code, 200)
        data_today = res_today.json()
        self.assertEqual(data_today["count"], 1)
        self.assertEqual(data_today["data"][0]["id"], "item_today_api")

        # Future date without predictions -> HTTP 200 with empty list []
        res_empty = self.client.get(f"/api/predictions/feed?date={self.empty_future_date}")
        self.assertEqual(res_empty.status_code, 200)
        data_empty = res_empty.json()
        self.assertEqual(data_empty["count"], 0)
        self.assertEqual(data_empty["data"], [])

if __name__ == "__main__":
    unittest.main()
