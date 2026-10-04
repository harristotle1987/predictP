import asyncio
from typing import Dict, Any, List, Optional
from backend.providers.football_adapter import football_adapter
from backend.providers.basketball_adapter import basketball_adapter
from backend.providers.baseball_adapter import baseball_adapter
from backend.providers.sports_skills_hockey_provider import hockey_adapter
from backend.providers.sports_skills_f1_provider import f1_adapter

PROVIDERS_MAP = {
    "football": football_adapter,
    "basketball": basketball_adapter,
    "baseball": baseball_adapter,
    "hockey": hockey_adapter,
    "ice_hockey": hockey_adapter,
    "formula_1": f1_adapter,
    "f1": f1_adapter,
}

class ProviderRouter:
    def get_provider(self, sport: str):
        norm = (sport or "").lower().strip()
        provider = PROVIDERS_MAP.get(norm)
        if not provider:
            raise ValueError(f"Unknown or unsupported sport provider: {sport}")
        return provider

    async def fetch_fixtures(self, sport: str, date_str: str = None) -> List[Dict[str, Any]]:
        provider = self.get_provider(sport)
        return await provider.fetch_fixtures(date_str=date_str)

    async def fetch_all_fixtures(self, date_str: str = None) -> Dict[str, Any]:
        """
        Fetches fixtures across all 5 supported sports concurrently.
        Preserves detailed error responses for diagnostic auditing.
        """
        results: Dict[str, Any] = {}
        sports_to_fetch = [
            ("football", football_adapter, "sports_skills.football.get_daily_schedule()"),
            ("basketball", basketball_adapter, "sports_skills.nba_data.get_scoreboard()"),
            ("baseball", baseball_adapter, "sports_skills.mlb_data.get_scoreboard()"),
            ("hockey", hockey_adapter, "sports_skills.nhl_data.get_scoreboard()"),
            ("formula_1", f1_adapter, "sports_skills.f1 / jolpica.f1"),
        ]

        for sport_key, adapter, method_label in sports_to_fetch:
            try:
                fixtures = await adapter.fetch_fixtures(date_str=date_str)
                results[sport_key] = {
                    "sport": sport_key,
                    "provider": "machina-sports/sports-skills",
                    "endpoint/method": method_label,
                    "status": "success",
                    "fixture_count": len(fixtures),
                    "count": len(fixtures),
                    "fixtures": fixtures,
                    "error": None,
                }
            except Exception as err:
                results[sport_key] = {
                    "sport": sport_key,
                    "provider": "machina-sports/sports-skills",
                    "endpoint/method": method_label,
                    "status": "PROVIDER_ERROR",
                    "fixture_count": 0,
                    "count": 0,
                    "fixtures": [],
                    "error": str(err),
                }

        return results

    async def run_smoke_test(self) -> List[Dict[str, Any]]:
        """
        Executes a live provider smoke test across all 5 sports and returns diagnostic metrics.
        """
        reports: List[Dict[str, Any]] = []
        sports_to_test = [
            ("football", football_adapter, "sports_skills.football.get_daily_schedule()"),
            ("basketball", basketball_adapter, "sports_skills.nba_data.get_scoreboard()"),
            ("baseball", baseball_adapter, "sports_skills.mlb_data.get_scoreboard()"),
            ("hockey", hockey_adapter, "sports_skills.nhl_data.get_scoreboard()"),
            ("formula_1", f1_adapter, "sports_skills.f1 / jolpica.f1"),
        ]

        for sport, adapter, method_label in sports_to_test:
            try:
                fixtures = await adapter.fetch_fixtures()
                first_fixture = fixtures[0] if fixtures else None
                reports.append({
                    "sport": sport,
                    "provider": "machina-sports/sports-skills",
                    "endpoint/method": method_label,
                    "fixture_count": len(fixtures),
                    "fixtures returned": len(fixtures),
                    "first fixture": f"{first_fixture.get('homeTeam')} vs {first_fixture.get('awayTeam')} ({first_fixture.get('league')})" if first_fixture else "None",
                    "status": "PASS" if len(fixtures) > 0 else "NO_GAMES_SCHEDULED",
                    "error": None,
                })
            except Exception as e:
                reports.append({
                    "sport": sport,
                    "provider": "machina-sports/sports-skills",
                    "endpoint/method": method_label,
                    "fixture_count": 0,
                    "fixtures returned": 0,
                    "first fixture": "None",
                    "status": f"PROVIDER_ERROR: {str(e)}",
                    "error": str(e),
                })

        return reports

provider_router = ProviderRouter()
