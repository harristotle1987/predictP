import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from backend.db.mongodb import mongo_manager
from backend.db.redis_client import redis_client
from backend.services.freshness_policy import freshness_policy

COMPETITION_CACHE_TTL_SECONDS = 43200  # 12 hours

# Authoritative baseline registry used for fallbacks and seeding
DEFAULT_BASELINE_COMPETITIONS: Dict[str, List[Dict[str, Any]]] = {
    "football": [
        {
            "competition_id": "premier-league",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Premier League",
            "country_or_region": "England",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "champions-league",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "UEFA Champions League",
            "country_or_region": "Europe",
            "level": "continental_tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "europa-league",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "UEFA Europa League",
            "country_or_region": "Europe",
            "level": "continental_tier_2",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "conference-league",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "UEFA Conference League",
            "country_or_region": "Europe",
            "level": "continental_tier_3",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "uefa-nations-league",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "UEFA Nations League",
            "country_or_region": "Europe",
            "level": "international",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "la-liga",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "La Liga",
            "country_or_region": "Spain",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "serie-a",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Serie A",
            "country_or_region": "Italy",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "bundesliga",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Bundesliga",
            "country_or_region": "Germany",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "ligue-1",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Ligue 1",
            "country_or_region": "France",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "mls",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Major League Soccer (MLS)",
            "country_or_region": "USA",
            "level": "tier_1",
            "active": True,
            "current_season": "2026",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "eredivisie",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Eredivisie",
            "country_or_region": "Netherlands",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "primeira-liga",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Primeira Liga",
            "country_or_region": "Portugal",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "championship",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "EFL Championship",
            "country_or_region": "England",
            "level": "tier_2",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "scottish-premiership",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Scottish Premiership",
            "country_or_region": "Scotland",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "belgian-pro-league",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Belgian Pro League",
            "country_or_region": "Belgium",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "super-lig",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Süper Lig",
            "country_or_region": "Turkey",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "copa-libertadores",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "Copa Libertadores",
            "country_or_region": "South America",
            "level": "continental_tier_1",
            "active": True,
            "current_season": "2026",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "world-cup",
            "provider": "machina-sports/sports-skills",
            "sport": "football",
            "competition_name": "FIFA World Cup",
            "country_or_region": "International",
            "level": "international",
            "active": True,
            "current_season": "2026",
            "priority": 1,
            "supported": True,
        },
    ],
    "basketball": [
        {
            "competition_id": "nba",
            "provider": "machina-sports/sports-skills",
            "sport": "basketball",
            "competition_name": "NBA",
            "country_or_region": "USA",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "wnba",
            "provider": "machina-sports/sports-skills",
            "sport": "basketball",
            "competition_name": "WNBA",
            "country_or_region": "USA",
            "level": "tier_1",
            "active": True,
            "current_season": "2026",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "cbb",
            "provider": "machina-sports/sports-skills",
            "sport": "basketball",
            "competition_name": "NCAA College Basketball (CBB)",
            "country_or_region": "USA",
            "level": "college",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
        {
            "competition_id": "euroleague",
            "provider": "machina-sports/sports-skills",
            "sport": "basketball",
            "competition_name": "EuroLeague Basketball",
            "country_or_region": "Europe",
            "level": "continental_tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
    ],
    "baseball": [
        {
            "competition_id": "mlb",
            "provider": "machina-sports/sports-skills",
            "sport": "baseball",
            "competition_name": "Major League Baseball (MLB)",
            "country_or_region": "USA",
            "level": "tier_1",
            "active": True,
            "current_season": "2026",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "npb",
            "provider": "machina-sports/sports-skills",
            "sport": "baseball",
            "competition_name": "Nippon Professional Baseball (NPB)",
            "country_or_region": "Japan",
            "level": "tier_1",
            "active": True,
            "current_season": "2026",
            "priority": 2,
            "supported": True,
        },
    ],
    "hockey": [
        {
            "competition_id": "nhl",
            "provider": "machina-sports/sports-skills",
            "sport": "hockey",
            "competition_name": "National Hockey League (NHL)",
            "country_or_region": "USA/Canada",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 1,
            "supported": True,
        },
        {
            "competition_id": "khl",
            "provider": "machina-sports/sports-skills",
            "sport": "hockey",
            "competition_name": "Kontinental Hockey League (KHL)",
            "country_or_region": "International",
            "level": "tier_1",
            "active": True,
            "current_season": "2026/2027",
            "priority": 2,
            "supported": True,
        },
    ],
    "formula_1": [
        {
            "competition_id": "f1",
            "provider": "machina-sports/sports-skills",
            "sport": "formula_1",
            "competition_name": "FIA Formula One World Championship",
            "country_or_region": "International",
            "level": "tier_1",
            "active": True,
            "current_season": "2026",
            "priority": 1,
            "supported": True,
        },
    ],
}


class CompetitionRegistryService:
    """
    Manages competition discovery, caching, and matching across all supported sports.
    Avoids scraping competition discovery endpoints on every refresh cycle.
    """

    def __init__(self):
        self._memory_cache: Dict[str, Dict[str, Any]] = {}

    def _get_collection(self):
        try:
            return mongo_manager.db["competition_registry"]
        except Exception:
            return None

    def _get_f1_events_collection(self):
        try:
            return mongo_manager.db["f1_events_registry"]
        except Exception:
            return None

    async def get_or_discover_competitions(
        self, sport: str = "football", force: bool = False
    ) -> List[Dict[str, Any]]:
        sport_norm = sport.lower().strip()
        if sport_norm in ("ice_hockey",):
            sport_norm = "hockey"
        elif sport_norm in ("f1",):
            sport_norm = "formula_1"

        cache_key = f"predictpro:competitions:{sport_norm}"
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. If not forcing, check Redis cache
        if not force:
            try:
                cached_redis = await redis_client.get_json(cache_key)
                if cached_redis and isinstance(cached_redis, list) and len(cached_redis) > 0:
                    return cached_redis
            except Exception as e:
                print(f"[CompetitionRegistry] Redis cache read notice: {e}")

            # 2. Check MongoDB for fresh records
            col = self._get_collection()
            if col is not None:
                try:
                    stored = list(col.find({"sport": sport_norm, "active": True}, {"_id": 0}).limit(100))
                    if stored:
                        latest_discovery = max(
                            [s.get("last_discovered_at", "") for s in stored if s.get("last_discovered_at")],
                            default=""
                        )
                        if latest_discovery:
                            if freshness_policy.is_fresh(latest_discovery, sport_norm, "registry"):
                                await redis_client.set_json(cache_key, stored, ex_seconds=COMPETITION_CACHE_TTL_SECONDS)
                                return stored
                except Exception as e:
                    print(f"[CompetitionRegistry] MongoDB read notice: {e}")

        # 3. Cache expired, not present, or force=True -> Discover from provider
        discovered: List[Dict[str, Any]] = []

        if sport_norm == "football":
            discovered = await self._discover_football_competitions(now_iso)
        elif sport_norm == "basketball":
            discovered = await self._discover_basketball_competitions(now_iso)
        elif sport_norm == "baseball":
            discovered = await self._discover_baseball_competitions(now_iso)
        elif sport_norm == "hockey":
            discovered = await self._discover_hockey_competitions(now_iso)
        elif sport_norm == "formula_1":
            discovered = await self._discover_f1_events(now_iso)
        else:
            discovered = [dict(c) for c in DEFAULT_BASELINE_COMPETITIONS.get(sport_norm, [])]
            for c in discovered:
                c["last_discovered_at"] = now_iso

        # If discovery failed or returned empty: fall back to MongoDB last-known or baseline
        if not discovered:
            col = self._get_collection()
            if col is not None:
                try:
                    last_known = list(col.find({"sport": sport_norm}, {"_id": 0}).limit(100))
                    if last_known:
                        discovered = last_known
                except Exception:
                    pass

        if not discovered:
            discovered = [dict(c) for c in DEFAULT_BASELINE_COMPETITIONS.get(sport_norm, [])]
            for c in discovered:
                c["last_discovered_at"] = now_iso

        # 4. Upsert discovered competitions into MongoDB
        col = self._get_collection()
        if col is not None:
            for comp in discovered:
                try:
                    c_id = comp.get("competition_id")
                    if c_id:
                        col.update_one(
                            {"competition_id": c_id, "sport": sport_norm},
                            {"$set": comp},
                            upsert=True,
                        )
                except Exception as e:
                    print(f"[CompetitionRegistry] MongoDB upsert notice: {e}")

        # 5. Cache in Redis
        try:
            await redis_client.set_json(cache_key, discovered, ex_seconds=COMPETITION_CACHE_TTL_SECONDS)
        except Exception as e:
            print(f"[CompetitionRegistry] Redis write notice: {e}")

        return discovered

    async def _discover_football_competitions(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered: List[Dict[str, Any]] = []
        try:
            from sports_skills.football._connector import LEAGUES
            for slug, league in LEAGUES.items():
                name = league.get("name", slug.replace("-", " ").title())
                country = league.get("country", "International")
                is_primary = slug in (
                    "premier-league", "la-liga", "serie-a", "bundesliga", "ligue-1",
                    "champions-league", "europa-league", "conference-league",
                    "uefa-nations-league", "world-cup", "copa-libertadores"
                )
                discovered.append({
                    "competition_id": slug,
                    "provider": "machina-sports/sports-skills",
                    "sport": "football",
                    "competition_name": name,
                    "country_or_region": country,
                    "level": "tier_1" if is_primary else "tier_2",
                    "active": True,
                    "current_season": "2026/2027",
                    "priority": 1 if is_primary else 2,
                    "supported": True,
                    "last_discovered_at": now_iso,
                    "last_fixture_refresh_at": None,
                    "next_refresh_at": None,
                })
        except Exception:
            pass

        # Ensure baseline competitions are represented
        existing_ids = {c["competition_id"] for c in discovered}
        for base in DEFAULT_BASELINE_COMPETITIONS.get("football", []):
            if base["competition_id"] not in existing_ids:
                item = dict(base)
                item["last_discovered_at"] = now_iso
                discovered.append(item)

        return discovered

    async def _discover_basketball_competitions(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered = [dict(c) for c in DEFAULT_BASELINE_COMPETITIONS.get("basketball", [])]
        for c in discovered:
            c["last_discovered_at"] = now_iso
        return discovered

    async def _discover_baseball_competitions(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered = [dict(c) for c in DEFAULT_BASELINE_COMPETITIONS.get("baseball", [])]
        for c in discovered:
            c["last_discovered_at"] = now_iso
        return discovered

    async def _discover_hockey_competitions(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered = [dict(c) for c in DEFAULT_BASELINE_COMPETITIONS.get("hockey", [])]
        for c in discovered:
            c["last_discovered_at"] = now_iso
        return discovered

    async def _discover_f1_events(self, now_iso: str) -> List[Dict[str, Any]]:
        discovered = [dict(c) for c in DEFAULT_BASELINE_COMPETITIONS.get("formula_1", [])]
        for c in discovered:
            c["last_discovered_at"] = now_iso
        return discovered

    def match_competition_name(self, league_name: str, competitions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not league_name:
            return None
        clean = league_name.lower().strip()
        for comp in competitions:
            c_name = comp.get("competition_name", "").lower()
            c_id = comp.get("competition_id", "").lower().replace("-", " ")
            if clean == c_name or clean == c_id:
                return comp
            if c_name in clean or clean in c_name:
                return comp
        return None

    def update_fixture_refresh_timestamp(self, competition_id: str, sport: str):
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        col = self._get_collection()
        if col is not None:
            try:
                col.update_one(
                    {"competition_id": competition_id, "sport": sport},
                    {"$set": {"last_fixture_refresh_at": now_iso}},
                )
            except Exception:
                pass


competition_registry_service = CompetitionRegistryService()
