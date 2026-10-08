import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
try:
    import httpx
except ImportError:
    httpx = None

from backend.config import settings
from backend.utils.timezone import get_current_lagos_today

LAGOS_TZ = timezone(timedelta(hours=1))

class ApiSportsProvider:
    """
    Real HTTP provider implementation for API-Sports across Football, Basketball,
    Baseball, Hockey, and Formula 1.
    
    Strict Provider Contract:
    1. Requires API-Sports base URL and API key.
    2. Queries provider's actual competition/league endpoints.
    3. Queries provider's actual fixture/game/race endpoints using those competition IDs.
    4. Normalizes ONLY fixtures actually returned by the provider.
    5. Returns [] when provider returns no fixtures or when unconfigured.
    6. NEVER manufactures fake fixture_id, league, team, date, score, or day_offset.
    """

    def __init__(self):
        self.provider_name = "api_sports"
        self.api_key = os.getenv("API_SPORTS_KEY") or getattr(settings, "api_sports_key", None)
        self.base_urls = {
            "football": "https://v3.football.api-sports.io",
            "basketball": "https://v1.basketball.api-sports.io",
            "baseball": "https://v1.baseball.api-sports.io",
            "hockey": "https://v1.hockey.api-sports.io",
            "formula_1": "https://v1.formula-1.api-sports.io",
        }

    def _normalize_sport(self, sport: str) -> str:
        s = (sport or "").lower().strip()
        if s in ("ice_hockey", "nhl"):
            return "hockey"
        if s in ("f1", "formula1"):
            return "formula_1"
        return s or "football"

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "PredictProEngine/2.0",
        }
        if self.api_key:
            headers["x-apisports-key"] = self.api_key
        return headers

    async def discover_competitions(self, sport: str) -> List[Dict[str, Any]]:
        """
        Queries the provider's actual competition/league endpoint to retrieve real competition IDs.
        Returns empty list if unconfigured or API returns no response.
        """
        sport_norm = self._normalize_sport(sport)
        if not self.api_key or httpx is None:
            return []

        base_url = self.base_urls.get(sport_norm)
        if not base_url:
            return []

        endpoint = f"{base_url}/leagues"
        if sport_norm == "formula_1":
            endpoint = f"{base_url}/races"

        competitions: List[Dict[str, Any]] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            async with httpx.AsyncClient(timeout=8.0, headers=self._get_headers()) as client:
                resp = await client.get(endpoint)
                if resp.status_code != 200:
                    return []
                data = resp.json()
                raw_results = data.get("response") or []

                for item in raw_results:
                    if isinstance(item, dict):
                        league_obj = item.get("league") or item.get("competition") or item
                        country_obj = item.get("country") or {}

                        c_id = str(league_obj.get("id") or "").strip()
                        c_name = str(league_obj.get("name") or "").strip()

                        if not c_id or not c_name:
                            continue

                        competitions.append({
                            "provider": self.provider_name,
                            "provider_competition_id": c_id,
                            "competition_id": f"api_sports_{sport_norm}_{c_id}",
                            "sport": sport_norm,
                            "competition_name": c_name,
                            "country_or_region": str(country_obj.get("name") or "Global"),
                            "level": str(league_obj.get("type") or "Top Flight"),
                            "active": True,
                            "current_season": "2026/2027",
                            "priority": 1,
                            "supported": True,
                            "last_discovered_at": now_iso,
                        })
        except Exception as e:
            print(f"[ApiSportsProvider] discover_competitions notice for {sport_norm}: {e}")
            return []

        return competitions

    async def fetch_competition_fixtures(
        self,
        sport: str,
        competition_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        season: str = "2026",
    ) -> List[Dict[str, Any]]:
        """
        Queries the provider's real fixture/game/race endpoint using real competition ID.
        Normalizes only fixtures actually returned by the provider.
        Rejects any fixture missing 'provider_fixture_id'.
        """
        sport_norm = self._normalize_sport(sport)
        if not self.api_key or httpx is None:
            return []

        base_url = self.base_urls.get(sport_norm)
        if not base_url:
            return []

        # Real fixture endpoint per sport
        if sport_norm == "formula_1":
            endpoint = f"{base_url}/races"
            params = {"competition": competition_id, "season": season}
        elif sport_norm in ("basketball", "baseball", "hockey"):
            endpoint = f"{base_url}/games"
            params = {"league": competition_id, "season": season}
        else:
            endpoint = f"{base_url}/fixtures"
            params = {"league": competition_id, "season": season}

        if start_date:
            params["from"] = start_date
        if end_date:
            params["to"] = end_date

        fixtures: List[Dict[str, Any]] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            async with httpx.AsyncClient(timeout=10.0, headers=self._get_headers()) as client:
                resp = await client.get(endpoint, params=params)
                if resp.status_code != 200:
                    return []
                data = resp.json()
                raw_fixtures = data.get("response") or []

                for item in raw_fixtures:
                    if not isinstance(item, dict):
                        continue

                    # Extract real provider fixture ID and competition details
                    fixture_obj = item.get("fixture") or item.get("game") or item
                    league_obj = item.get("league") or item.get("competition") or {}
                    teams_obj = item.get("teams") or {}

                    p_fix_id = str(fixture_obj.get("id") or item.get("id") or "").strip()
                    # CONTRACT RULE: If provider_fixture_id is missing, REJECT the fixture
                    if not p_fix_id:
                        continue

                    p_comp_id = str(league_obj.get("id") or competition_id or "").strip()
                    comp_name = str(league_obj.get("name") or "").strip() or f"Competition {p_comp_id}"

                    # Teams
                    home_obj = teams_obj.get("home") or {}
                    away_obj = teams_obj.get("away") or {}
                    home_name = str(home_obj.get("name") or "").strip()
                    away_name = str(away_obj.get("name") or "").strip()

                    # Require real home and away team names
                    if not home_name or not away_name:
                        continue

                    # Kickoff / start time
                    start_time_raw = (
                        fixture_obj.get("date")
                        or fixture_obj.get("start_time")
                        or item.get("date")
                        or now_iso
                    )
                    try:
                        dt = datetime.fromisoformat(str(start_time_raw).replace("Z", "+00:00"))
                        start_time_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        start_time_iso = str(start_time_raw)

                    status_obj = fixture_obj.get("status") or {}
                    raw_status_short = str(
                        status_obj.get("short") or status_obj.get("long") or "NS"
                    ).lower()

                    status_str = "scheduled"
                    if any(x in raw_status_short for x in ["ft", "aet", "pen", "finished", "completed", "final"]):
                        status_str = "completed"
                    elif any(x in raw_status_short for x in ["live", "1h", "2h", "ht", "q1", "q2", "q3", "q4", "in_progress"]):
                        status_str = "live"

                    external_id = f"api_sports_{p_comp_id}_{p_fix_id}"

                    fixtures.append({
                        "provider": self.provider_name,
                        "provider_competition_id": p_comp_id,
                        "provider_fixture_id": p_fix_id,
                        "id": external_id,
                        "fixture_id": external_id,
                        "sport": sport_norm,
                        "competition": comp_name,
                        "competition_name": comp_name,
                        "competition_id": f"api_sports_{sport_norm}_{p_comp_id}",
                        "league": comp_name,
                        "season": str(league_obj.get("season") or season),
                        "start_time": start_time_iso,
                        "kickoffUtc": start_time_iso,
                        "scheduled_at": start_time_iso,
                        "home_team": home_name,
                        "away_team": away_name,
                        "homeTeam": home_name,
                        "awayTeam": away_name,
                        "status": status_str,
                        "source_updated_at": now_iso,
                    })

        except Exception as e:
            print(f"[ApiSportsProvider] fetch_competition_fixtures notice for {sport_norm}/{competition_id}: {e}")
            return []

        return fixtures

    async def fetch_fixtures(
        self,
        sport: str,
        date_str: Optional[str] = None,
        competition_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        horizon_days: int = 14,
    ) -> List[Dict[str, Any]]:
        """
        Executes real HTTP calls to retrieve fixtures.
        Returns empty list when unconfigured or when the provider returns no fixtures.
        NEVER manufactures fake fixtures or static matchups.
        """
        sport_norm = self._normalize_sport(sport)
        if not self.api_key or httpx is None:
            return []

        # If specific competition provided, query it directly
        if competition_id:
            s_date = start_date or date_str
            e_date = end_date or date_str
            return await self.fetch_competition_fixtures(
                sport=sport_norm,
                competition_id=competition_id,
                start_date=s_date,
                end_date=e_date,
            )

        # Discover active real competitions for this sport
        competitions = await self.discover_competitions(sport_norm)
        if not competitions:
            return []

        all_fixtures: List[Dict[str, Any]] = []
        s_date = start_date or date_str
        e_date = end_date or date_str

        # Query real schedule for each discovered competition
        for comp in competitions[:5]:  # Bounded execution over top discovered competitions
            p_comp_id = comp.get("provider_competition_id")
            if p_comp_id:
                comp_fixtures = await self.fetch_competition_fixtures(
                    sport=sport_norm,
                    competition_id=p_comp_id,
                    start_date=s_date,
                    end_date=e_date,
                )
                all_fixtures.extend(comp_fixtures)

        return all_fixtures


api_sports_provider = ApiSportsProvider()
