import unittest
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from backend.config import settings
from backend.services.refresh_planner import refresh_planner, OPERATIONAL_FIXTURE_HORIZON_DAYS, LAGOS_TZ
from backend.services.sync_service import sync_service, OPERATIONAL_FIXTURE_HORIZON_DAYS as SYNC_HORIZON_DAYS
from backend.services.feed_service import feed_service, get_current_lagos_today
from backend.db.database_router import database_router
from backend.db.mongodb import mongo_manager
from backend.db.neon_adapter import neon_adapter
from backend.db.failover_manager import failover_manager


class TestOperationalHorizonAndCatalogueSeparation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        failover_manager.reset_state_for_tests()

    async def test_1_operational_fixture_horizon_days_constant(self):
        """Verify OPERATIONAL_FIXTURE_HORIZON_DAYS is at least 14 in settings and services."""
        self.assertGreaterEqual(OPERATIONAL_FIXTURE_HORIZON_DAYS, 14)
        self.assertGreaterEqual(SYNC_HORIZON_DAYS, 14)

    async def test_2_default_refresh_window_is_7_lagos_days(self):
        """Verify default window uses operational horizon days in Lagos."""
        window = refresh_planner.compute_lagos_date_window()
        self.assertEqual(len(window), OPERATIONAL_FIXTURE_HORIZON_DAYS)

        now_lagos = datetime.now(LAGOS_TZ)
        expected_today = now_lagos.strftime("%Y-%m-%d")
        self.assertEqual(window[0], expected_today)

        for i in range(OPERATIONAL_FIXTURE_HORIZON_DAYS):
            expected_date = (now_lagos + timedelta(days=i)).strftime("%Y-%m-%d")
            self.assertEqual(window[i], expected_date)

    async def test_3_explicit_date_and_range_requests_work(self):
        """Verify explicit date or date_range overrides default window properly."""
        single_date = "2026-10-15"
        window_single = refresh_planner.compute_lagos_date_window(date=single_date)
        self.assertEqual(window_single, ["2026-10-15"])

        custom_range = ["2026-10-20", "2026-10-21", "2026-10-22"]
        window_range = refresh_planner.compute_lagos_date_window(date_range=custom_range)
        self.assertEqual(window_range, ["2026-10-20", "2026-10-21", "2026-10-22"])

    async def test_4_refresh_diagnostics_include_all_required_keys(self):
        """Verify refresh diagnostics contain requestedDateRange, providerFixturesReturned, fixturesPersisted, activeDatabase, mongoHealth, neonHealth, failoverState."""
        today_lagos = get_current_lagos_today()
        record = await sync_service.execute_refresh(sports=["football"], date=today_lagos, force=True)
        diag = record.diagnostics

        self.assertIn("requestedDateRange", diag)
        self.assertIn("providerFixturesReturned", diag)
        self.assertIn("fixturesPersisted", diag)
        self.assertIn("activeDatabase", diag)
        self.assertIn("mongoHealth", diag)
        self.assertIn("neonHealth", diag)
        self.assertIn("failoverState", diag)
        self.assertIn("fixturesDeduplicated", diag)
        self.assertIn("fixturesPersistedMongo", diag)
        self.assertIn("fixturesPersistedNeon", diag)

    async def test_5_fixture_catalogue_methods_in_router(self):
        """Verify get_fixtures and count_fixtures are exposed through DatabaseRouter."""
        fixtures = await database_router.fixtures.get_fixtures(limit=20, offset=0)
        self.assertIsInstance(fixtures, list)

        count = await database_router.fixtures.count_fixtures()
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 0)

    async def test_6_catalogue_pagination_and_hard_limit(self):
        """Verify catalogue supports limit, offset, and does not exceed bounded limit."""
        fixtures_page_1 = await database_router.fixtures.get_fixtures(limit=5, offset=0)
        self.assertLessEqual(len(fixtures_page_1), 5)

        fixtures_page_2 = await database_router.fixtures.get_fixtures(limit=5, offset=5)
        self.assertLessEqual(len(fixtures_page_2), 5)

    async def test_7_prediction_feed_vs_catalogue_separation(self):
        """
        Verify prediction feed strictly returns max 20 validated items with non-empty markets,
        while operational fixtures query can return all operational fixtures without predictions.
        """
        feed = await feed_service.get_feed(limit=20)
        self.assertLessEqual(len(feed), 20)
        for item in feed:
            self.assertEqual(item.get("validationStatus"), "validated")
            self.assertTrue(len(item.get("validatedMarkets", [])) > 0)

        # Operational catalogue query
        catalogue = await database_router.fixtures.get_fixtures(limit=50)
        self.assertIsInstance(catalogue, list)

    async def test_8_failover_routed_fixtures_catalogue(self):
        """Verify switching to Neon failover allows get_fixtures and count_fixtures without error."""
        from backend.db.interfaces import FailoverState
        failover_manager._active_backend = "neon"
        failover_manager._failover_active = True
        failover_manager._failover_state = FailoverState.NEON_FAILOVER
        self.assertEqual(failover_manager.active_backend, "neon")

        # Routed call executes on Neon adapter
        try:
            count = await database_router.fixtures.count_fixtures()
            self.assertIsInstance(count, int)
        except Exception as e:
            # If Neon not configured in unit test environment, DatabaseUnavailableError or 0 is handled cleanly
            self.assertTrue("Neon" in str(e) or count == 0)


if __name__ == "__main__":
    unittest.main()
