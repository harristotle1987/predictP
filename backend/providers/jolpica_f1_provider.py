try:
    import httpx
except ImportError:
    httpx = None
from typing import Dict, Any, List, Optional

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

    async def get_driver_standings(self, year: int) -> List[Dict[str, Any]]:
        """
        Retrieves real driver standings from Jolpica F1 API.
        If real data cannot be retrieved, fails/abstains with an empty list.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=self.headers) as client:
                res = await client.get(f"{self.base_url}/{year}/driverStandings.json")
                if res.status_code == 200:
                    data = res.json()
                    standings = (data.get("MRData", {})
                                 .get("StandingsTable", {})
                                 .get("StandingsLists", [{}])[0]
                                 .get("DriverStandings", []))
                    if standings:
                        return standings
        except Exception as e:
            print(f"[Jolpica F1] Driver standings retrieval failed for {year}: {e}")

        return []

    async def get_constructor_standings(self, year: int) -> List[Dict[str, Any]]:
        """
        Retrieves real constructor standings from Jolpica F1 API.
        If real data cannot be retrieved, fails/abstains with an empty list.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=self.headers) as client:
                res = await client.get(f"{self.base_url}/{year}/constructorStandings.json")
                if res.status_code == 200:
                    data = res.json()
                    standings = (data.get("MRData", {})
                                 .get("StandingsTable", {})
                                 .get("StandingsLists", [{}])[0]
                                 .get("ConstructorStandings", []))
                    if standings:
                        return standings
        except Exception as e:
            print(f"[Jolpica F1] Constructor standings retrieval failed for {year}: {e}")

        return []

    async def get_race_results(self, year: int, round_num: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieves race results for a specific round or all rounds of a season.
        """
        try:
            endpoint = f"{self.base_url}/{year}/{round_num}/results.json" if round_num else f"{self.base_url}/{year}/results.json?limit=100"
            async with httpx.AsyncClient(timeout=12.0, headers=self.headers) as client:
                res = await client.get(endpoint)
                if res.status_code == 200:
                    races = res.json().get("MRData", {}).get("RaceTable", {}).get("Races", [])
                    return races
        except Exception as e:
            print(f"[Jolpica F1] Race results retrieval failed for {year} round {round_num}: {e}")
        return []

    async def get_season_results(self, year: int) -> List[Dict[str, Any]]:
        """
        Retrieves all race results for a season with proper Ergast/Jolpica pagination.
        """
        all_races: List[Dict[str, Any]] = []
        offset = 0
        limit = 100
        total = 1
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=self.headers) as client:
                while offset < total and offset < 1000:
                    url = f"{self.base_url}/{year}/results.json?limit={limit}&offset={offset}"
                    res = await client.get(url)
                    if res.status_code != 200:
                        break
                    mr_data = res.json().get("MRData", {})
                    total = int(mr_data.get("total", 0))
                    races = mr_data.get("RaceTable", {}).get("Races", [])
                    if not races:
                        break
                    all_races.extend(races)
                    offset += limit
        except Exception as e:
            print(f"[Jolpica F1] Season results retrieval failed for {year}: {e}")

        return all_races

    async def get_qualifying_results(self, year: int, round_num: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieves qualifying session results for a season or round.
        """
        try:
            endpoint = f"{self.base_url}/{year}/{round_num}/qualifying.json" if round_num else f"{self.base_url}/{year}/qualifying.json?limit=100"
            async with httpx.AsyncClient(timeout=12.0, headers=self.headers) as client:
                res = await client.get(endpoint)
                if res.status_code == 200:
                    return res.json().get("MRData", {}).get("RaceTable", {}).get("Races", [])
        except Exception as e:
            print(f"[Jolpica F1] Qualifying results retrieval failed for {year} round {round_num}: {e}")
        return []

jolpica_f1_provider = JolpicaF1Provider()
