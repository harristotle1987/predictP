import asyncio
from typing import Dict, Any, List, Optional
from backend.providers.football_adapter import football_adapter
from backend.providers.basketball_adapter import basketball_adapter
from backend.providers.baseball_adapter import baseball_adapter
from backend.providers.sports_skills_hockey_provider import hockey_adapter
from backend.providers.sports_skills_f1_provider import f1_adapter
from backend.providers.api_sports_provider import api_sports_provider
from backend.services.competition_registry import competition_registry_service

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

    async def discover_competitions(self, sport: str) -> List[Dict[str, Any]]:
        """
        Discovers competitions available for the given sport.
        Combines SportsSkills (preferred source) and API-Sports for broad competition coverage.
        Do NOT loop through DEFAULT_BASELINE_COMPETITIONS and assume leagues exist.
        """
        sport_norm = (sport or "").lower().strip()
        if sport_norm in ("ice_hockey", "nhl"):
            sport_norm = "hockey"
        elif sport_norm in ("f1", "formula1"):
            sport_norm = "formula_1"

        competitions: List[Dict[str, Any]] = []
        seen_names = set()

        # 1. Preferred source: SportsSkills
        try:
            ss_comps = await competition_registry_service.get_or_discover_competitions(sport=sport_norm)
            for c in ss_comps:
                c_name = str(c.get("competition_name") or c.get("name") or "").lower().strip()
                if c_name and c_name not in seen_names:
                    seen_names.add(c_name)
                    competitions.append(c)
        except Exception as ss_err:
            print(f"[ProviderRouter] SportsSkills discovery notice for {sport_norm}: {ss_err}")

        # 2. Broad competition discovery: API-Sports
        try:
            api_comps = await api_sports_provider.discover_competitions(sport=sport_norm)
            for c in api_comps:
                c_name = str(c.get("competition_name") or c.get("name") or "").lower().strip()
                if c_name and c_name not in seen_names:
                    seen_names.add(c_name)
                    competitions.append(c)
        except Exception as api_err:
            print(f"[ProviderRouter] API-Sports discovery notice for {sport_norm}: {api_err}")

        return competitions

    async def discover_all_competitions(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Discovers competitions across all 5 sports concurrently.
        """
        sports = ["football", "basketball", "baseball", "hockey", "formula_1"]
        results: Dict[str, List[Dict[str, Any]]] = {}
        for s in sports:
            results[s] = await self.discover_competitions(s)
        return results

    async def fetch_competition_fixtures(
        self,
        sport: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Scans EVERY discovered competition for a sport:
        discover competitions -> for EVERY discovered competition -> fetch real schedule -> normalize -> deduplicate.
        """
        sport_norm = (sport or "").lower().strip()
        if sport_norm in ("ice_hockey", "nhl"):
            sport_norm = "hockey"
        elif sport_norm in ("f1", "formula1"):
            sport_norm = "formula_1"

        competitions = await self.discover_competitions(sport_norm)
        all_fixtures: List[Dict[str, Any]] = []
        seen_ids = set()

        for comp in competitions:
            c_id = comp.get("provider_competition_id") or comp.get("competition_id") or comp.get("id")
            if not c_id:
                continue

            try:
                comp_fixtures = await api_sports_provider.fetch_competition_fixtures(
                    sport=sport_norm,
                    competition_id=c_id,
                    start_date=start_date,
                    end_date=end_date,
                )
                for f in comp_fixtures:
                    f_id = f.get("provider_fixture_id") or f.get("id") or f.get("fixture_id")
                    if f_id and f_id not in seen_ids:
                        seen_ids.add(f_id)
                        all_fixtures.append(f)
            except Exception as comp_err:
                print(f"[ProviderRouter] fetch_competition_fixtures error for {sport_norm}/{c_id}: {comp_err}")

        # Also attempt primary adapter if no fixtures returned
        if not all_fixtures:
            try:
                adapter = self.get_provider(sport_norm)
                kwargs = {}
                if start_date:
                    kwargs["date_str"] = start_date
                adapter_fixtures = await adapter.fetch_fixtures(**kwargs)
                for f in adapter_fixtures:
                    f_id = f.get("provider_fixture_id") or f.get("id") or f.get("fixture_id")
                    if f_id and f_id not in seen_ids:
                        seen_ids.add(f_id)
                        all_fixtures.append(f)
            except Exception as e:
                print(f"[ProviderRouter] Primary adapter notice for {sport_norm}: {e}")

        return all_fixtures

    async def fetch_fixtures(self, sport: str, date_str: str = None, competition_id: str = None) -> List[Dict[str, Any]]:
        if competition_id:
            return await api_sports_provider.fetch_competition_fixtures(sport, competition_id=competition_id, start_date=date_str, end_date=date_str)

        return await self.fetch_competition_fixtures(sport, start_date=date_str, end_date=date_str)

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
                    "provider": getattr(adapter, "provider_name", "machina-sports/sports-skills"),
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
                    "provider": getattr(adapter, "provider_name", "machina-sports/sports-skills"),
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
                    "provider": getattr(adapter, "provider_name", "machina-sports/sports-skills"),
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
                    "provider": getattr(adapter, "provider_name", "machina-sports/sports-skills"),
                    "endpoint/method": method_label,
                    "fixture_count": 0,
                    "fixtures returned": 0,
                    "first fixture": "None",
                    "status": f"PROVIDER_ERROR: {str(e)}",
                    "error": str(e),
                })

        return reports

provider_router = ProviderRouter()
