import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any
try:
    import httpx
except ImportError:
    httpx = None
from backend.providers.base import BaseSportsSkillsAdapter
from backend.config import settings

JOLPICA_BASE_URL = "https://api.jolpi.ca/ergast/f1"
JOLPICA_HEADERS = {"User-Agent": "PredictPro/1.0 (contact: harristotle84@gmail.com)"}

class SportsSkillsF1Provider(BaseSportsSkillsAdapter):
    def __init__(self):
        super().__init__("formula_1")
        self._cached_races: List[Dict[str, Any]] = []
        self._last_cached_time: float = 0.0

    async def fetch_fixtures(self, date_str: str = None) -> List[Dict[str, Any]]:
        import time
        now = time.time()
        if self._cached_races and (now - self._last_cached_time) < 3600.0:
            if date_str:
                return [f for f in self._cached_races if f.get("kickoffUtc", "")[:10] == date_str]
            return list(self._cached_races)

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        year = now_dt.year
        fixtures: List[Dict[str, Any]] = []
        errors: List[str] = []

        # 1. Primary: Try Jolpica F1 Ergast API
        try:
            data = None
            if httpx:
                try:
                    async with httpx.AsyncClient(timeout=2.5, headers=JOLPICA_HEADERS) as client:
                        resp = await client.get(f"{JOLPICA_BASE_URL}/{year}.json")
                        if resp.status_code == 200:
                            data = resp.json()
                except Exception:
                    data = None

            if not data:
                import urllib.request
                import json
                req = urllib.request.Request(f"{JOLPICA_BASE_URL}/{year}.json", headers=JOLPICA_HEADERS)
                def _fetch_jolpica():
                    with urllib.request.urlopen(req, timeout=2.5) as uresp:
                        return json.loads(uresp.read().decode())
                data = await asyncio.to_thread(_fetch_jolpica)

            if isinstance(data, dict):
                races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
                for race in races:
                    r_round = int(race.get("round", 1))
                    race_name = race.get("raceName", "Grand Prix")
                    circuit = race.get("Circuit", {}).get("circuitName", "F1 Circuit")
                    date_val = race.get("date", "")
                    time_val = race.get("time", "13:00:00Z").replace("Z", "")
                    
                    try:
                        start_dt = datetime.fromisoformat(f"{date_val}T{time_val}+00:00")
                        race_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        race_iso = f"{date_val}T13:00:00Z" if date_val else now_iso
                        start_dt = now_dt

                    status = "scheduled"
                    display_score = "Upcoming"
                    if start_dt < now_dt:
                        status = "completed"
                        display_score = "Race Concluded"

                    score_obj = None
                    if status == "completed":
                        score_obj = {
                            "home": 0,
                            "away": 0,
                            "display": display_score,
                        }

                    fixtures.append({
                        "id": f"f1_{year}_{r_round:02d}",
                        "source_event_id": str(r_round),
                        "sport": "formula_1",
                        "league": f"Formula 1 · {race_name}",
                        "competition_id": f"f1_{year}",
                        "competitionTier": "primary",
                        "homeTeam": "F1 Drivers Field",
                        "awayTeam": "F1 Constructors Field",
                        "home_team_id": "f1_drivers",
                        "away_team_id": "f1_constructors",
                        "circuit": circuit,
                        "kickoffUtc": race_iso,
                        "scheduled_at": race_iso,
                        "status": status,
                        "currentScore": score_obj,
                        "finalScore": score_obj if status == "completed" else None,
                        "source_updated_at": now_iso,
                        "provider": "jolpica-f1",
                    })
        except Exception as e:
            errors.append(f"Jolpica F1 API error: {e}")

        # 2. Secondary: If Jolpica returned no races, try SportsSkills F1 / FastF1
        if not fixtures:
            try:
                import sports_skills.fastf1 as ss_f1
                res = await asyncio.wait_for(
                    asyncio.to_thread(ss_f1.get_race_schedule, year=year),
                    timeout=8.0
                )
                races = res.get("data", []) if isinstance(res, dict) else []
                for r in races:
                    event_name = r.get("event_name", "Grand Prix")
                    if "Testing" in event_name:
                        continue

                    raw_date = r.get("event_date") or now_iso
                    try:
                        dt = datetime.fromisoformat(raw_date.replace(" ", "T"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        race_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        race_iso = f"{raw_date[:10]}T13:00:00Z"
                        dt = now_dt

                    status = "scheduled"
                    if dt < now_dt:
                        status = "completed"

                    score_obj = None
                    if status == "completed":
                        score_obj = {
                            "home": 0,
                            "away": 0,
                            "display": "Race Concluded",
                        }

                    r_round = int(r.get("round_number", 1))
                    fixtures.append({
                        "id": f"f1_{year}_{r_round:02d}",
                        "source_event_id": str(r_round),
                        "sport": "formula_1",
                        "league": f"Formula 1 · {event_name}",
                        "competition_id": f"f1_{year}",
                        "competitionTier": "primary",
                        "homeTeam": "F1 Drivers Field",
                        "awayTeam": "F1 Constructors Field",
                        "home_team_id": "f1_drivers",
                        "away_team_id": "f1_constructors",
                        "kickoffUtc": race_iso,
                        "scheduled_at": race_iso,
                        "status": status,
                        "currentScore": score_obj,
                        "finalScore": score_obj if status == "completed" else None,
                        "source_updated_at": now_iso,
                        "provider": "machina-sports/sports-skills",
                    })
            except Exception as e:
                errors.append(f"SportsSkills F1 error: {e}")

        if fixtures:
            self._cached_races = list(fixtures)
            self._last_cached_time = now
            if date_str:
                matched_races = [f for f in fixtures if f.get("kickoffUtc", "")[:10] == date_str]
                if matched_races:
                    return matched_races
                # Fall back to confirmed provider schedule for this date
                from backend.providers.api_sports_provider import api_sports_provider
                return await api_sports_provider.fetch_fixtures("formula_1", date_str=date_str)
            return fixtures

        from backend.providers.api_sports_provider import api_sports_provider
        return await api_sports_provider.fetch_fixtures("formula_1", date_str=date_str)

    async def get_jolpica_race_results(self, season: int, round_num: int) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0, headers=JOLPICA_HEADERS) as client:
            resp = await client.get(f"{JOLPICA_BASE_URL}/{season}/{round_num}/results/")
            return resp.json() if resp.status_code == 200 else {}

    async def get_jolpica_driver_standings(self, season: int) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0, headers=JOLPICA_HEADERS) as client:
            resp = await client.get(f"{JOLPICA_BASE_URL}/{season}/driverstandings/")
            return resp.json() if resp.status_code == 200 else {}

    async def get_jolpica_constructor_standings(self, season: int) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0, headers=JOLPICA_HEADERS) as client:
            resp = await client.get(f"{JOLPICA_BASE_URL}/{season}/constructorstandings/")
            return resp.json() if resp.status_code == 200 else {}

sports_skills_f1_provider = SportsSkillsF1Provider()
f1_adapter = sports_skills_f1_provider
