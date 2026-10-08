import unittest
import asyncio
from backend.providers.api_sports_provider import ApiSportsProvider

class TestNoFakeFixtures(unittest.TestCase):
    """
    Tests enforcing that ApiSportsProvider never generates fake fixtures
    or uses hardcoded SCHEDULED_MATCHUPS.
    """

    def test_scheduled_matchups_does_not_exist(self):
        import backend.providers.api_sports_provider as mod
        self.assertFalse(hasattr(mod, "SCHEDULED_MATCHUPS"), "SCHEDULED_MATCHUPS must be removed")
        self.assertFalse(hasattr(ApiSportsProvider, "SCHEDULED_MATCHUPS"), "ApiSportsProvider must not have SCHEDULED_MATCHUPS")

    def test_unconfigured_provider_returns_empty(self):
        provider = ApiSportsProvider()
        provider.api_key = None  # Ensure no API key
        
        async def _run():
            comps = await provider.discover_competitions("football")
            self.assertEqual(comps, [])
            
            fixtures = await provider.fetch_competition_fixtures(
                sport="football",
                competition_id="39",
                start_date="2026-10-08",
                end_date="2026-10-15"
            )
            self.assertEqual(fixtures, [])
            
            all_fixtures = await provider.fetch_fixtures("football", date_str="2026-10-08")
            self.assertEqual(all_fixtures, [])

        asyncio.run(_run())

    def test_contract_compliance(self):
        """Ensure provider contract fields are strictly maintained when fixtures are parsed."""
        provider = ApiSportsProvider()
        provider.api_key = "test_key"
        
        # Mock httpx response to test contract
        class MockResponse:
            status_code = 200
            def json(self):
                return {
                    "response": [
                        {
                            "fixture": {
                                "id": 12345,
                                "date": "2026-10-10T15:00:00+00:00",
                                "status": {"short": "NS"}
                            },
                            "league": {
                                "id": 39,
                                "name": "Premier League",
                                "season": "2026"
                            },
                            "teams": {
                                "home": {"name": "Arsenal"},
                                "away": {"name": "Chelsea"}
                            }
                        }
                    ]
                }

        class MockAsyncClient:
            def __init__(self, *args, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def get(self, *args, **kwargs):
                return MockResponse()

        import backend.providers.api_sports_provider as mod
        import sys
        
        class MockHttpxModule:
            AsyncClient = MockAsyncClient

        orig_httpx = getattr(mod, "httpx", None)
        mod.httpx = MockHttpxModule()
        try:
            async def _run():
                fixtures = await provider.fetch_competition_fixtures(
                    sport="football",
                    competition_id="39",
                    start_date="2026-10-08",
                    end_date="2026-10-15"
                )
                self.assertEqual(len(fixtures), 1)
                normalized = fixtures[0]
                self.assertEqual(normalized["provider"], "api_sports")
                self.assertEqual(normalized["provider_competition_id"], "39")
                self.assertEqual(normalized["provider_fixture_id"], "12345")
                self.assertEqual(normalized["sport"], "football")
                self.assertEqual(normalized["status"], "scheduled")
                self.assertEqual(normalized["home_team"], "Arsenal")
                self.assertEqual(normalized["away_team"], "Chelsea")

            asyncio.run(_run())
        finally:
            mod.httpx = orig_httpx

if __name__ == "__main__":
    unittest.main()
