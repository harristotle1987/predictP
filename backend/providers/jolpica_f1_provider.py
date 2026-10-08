import asyncio
from typing import Dict, Any, List, Optional
try:
    import httpx
except ImportError:
    httpx = None

class JolpicaF1Provider:
    """
    F1 Reconciliation and structured metadata source (successor to Ergast API).
    Provides structured point-in-time standings, race results, and driver/constructor stats.
    Uses real live data from Jolpica F1 API (https://api.jolpi.ca/ergast/f1).
    Never uses sample or fabricated fallback data.
    """
    def __init__(self):
        self.base_url = "https://api.jolpi.ca/ergast/f1"
        self.headers = {"User-Agent": "PredictPro/1.0 (contact: harristotle84@gmail.com)"}

    async def _fetch_json(self, url: str) -> Optional[Dict[str, Any]]:
        if httpx:
            try:
                async with httpx.AsyncClient(timeout=12.0, headers=self.headers) as client:
                    res = await client.get(url)
                    if res.status_code == 200:
                        return res.json()
            except Exception:
                pass
        try:
            import urllib.request
            import json
            req = urllib.request.Request(url, headers=self.headers)
            def _req():
                with urllib.request.urlopen(req, timeout=12.0) as resp:
                    return json.loads(resp.read().decode())
            return await asyncio.to_thread(_req)
        except Exception as e:
            print(f"[Jolpica F1] Request failed for {url}: {e}")
            return None

    async def get_driver_standings(self, year: int) -> List[Dict[str, Any]]:
        """
        Retrieves real driver standings from Jolpica F1 API.
        If real data cannot be retrieved, fails/abstains with an empty list.
        """
        data = await self._fetch_json(f"{self.base_url}/{year}/driverStandings.json")
        if data:
            standings = (data.get("MRData", {})
                         .get("StandingsTable", {})
                         .get("StandingsLists", [{}])[0]
                         .get("DriverStandings", []))
            if standings:
                return standings
        return []

    async def get_constructor_standings(self, year: int) -> List[Dict[str, Any]]:
        """
        Retrieves real constructor standings from Jolpica F1 API.
        If real data cannot be retrieved, fails/abstains with an empty list.
        """
        data = await self._fetch_json(f"{self.base_url}/{year}/constructorStandings.json")
        if data:
            standings = (data.get("MRData", {})
                         .get("StandingsTable", {})
                         .get("StandingsLists", [{}])[0]
                         .get("ConstructorStandings", []))
            if standings:
                return standings
        return []

    async def get_race_results(self, year: int, round_num: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieves race results for a specific round or all rounds of a season.
        """
        endpoint = f"{self.base_url}/{year}/{round_num}/results.json" if round_num else f"{self.base_url}/{year}/results.json?limit=100"
        data = await self._fetch_json(endpoint)
        if data:
            return data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        return []

    async def get_season_results(self, year: int) -> List[Dict[str, Any]]:
        """
        Retrieves all race results for a season with proper Ergast/Jolpica pagination.
        """
        all_races: List[Dict[str, Any]] = []
        offset = 0
        limit = 100
        total = 1
        while offset < total and offset < 1000:
            url = f"{self.base_url}/{year}/results.json?limit={limit}&offset={offset}"
            data = await self._fetch_json(url)
            if not data:
                break
            mr_data = data.get("MRData", {})
            total = int(mr_data.get("total", 0))
            races = mr_data.get("RaceTable", {}).get("Races", [])
            if not races:
                break
            all_races.extend(races)
            offset += limit
        return all_races

    async def get_qualifying_results(self, year: int, round_num: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieves qualifying session results for a season or round.
        """
        endpoint = f"{self.base_url}/{year}/{round_num}/qualifying.json" if round_num else f"{self.base_url}/{year}/qualifying.json?limit=100"
        data = await self._fetch_json(endpoint)
        if data:
            return data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
        return []

jolpica_f1_provider = JolpicaF1Provider()
