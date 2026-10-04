"""
Fixture Repository.
Provides targeted, projected, and batched queries for fixtures/operational events.
Prohibits full-collection scans in normal runtime paths.
"""

from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone
from backend.db.database_router import database_router
from backend.db.redis_client import redis_client

SERVING_FIXTURE_PROJECTION = {
    "_id": 0,
    "id": 1,
    "sport": 1,
    "league": 1,
    "competition_id": 1,
    "homeTeam": 1,
    "awayTeam": 1,
    "kickoffUtc": 1,
    "scheduled_at": 1,
    "status": 1,
    "currentScore": 1,
    "venue": 1,
    "referee": 1,
    "source_event_id": 1,
    "fetched_at": 1,
    "fixture_version": 1,
}

class FixtureRepository:
    def __init__(self):
        pass

    async def get_by_id(self, fixture_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single fixture by canonical ID with Redis cache-aside."""
        if not fixture_id:
            return None
            
        cache_key = f"predictpro:fixture:{fixture_id}"
        try:
            cached = await redis_client.get_json(cache_key)
            if cached and isinstance(cached, dict):
                return cached
        except Exception:
            pass

        try:
            doc = await database_router.fixtures.get_by_id(fixture_id)
            if doc:
                try:
                    await redis_client.set_json(cache_key, doc, ex_seconds=900)
                except Exception:
                    pass
                return doc
        except Exception as e:
            print(f"[FixtureRepository] get_by_id error: {e}")
        return None

    async def get_by_ids(self, fixture_ids: List[str]) -> List[Dict[str, Any]]:
        """Batch-load multiple fixtures by ID with deduplication and targeted query."""
        if not fixture_ids:
            return []
        try:
            return await database_router.fixtures.get_by_ids(fixture_ids)
        except Exception as e:
            print(f"[FixtureRepository] get_by_ids error: {e}")
            return []

    async def get_live_fixtures(self, sport: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch currently live / in_play fixtures."""
        try:
            return await database_router.fixtures.get_live_fixtures(sport)
        except Exception as e:
            print(f"[FixtureRepository] get_live_fixtures error: {e}")
            return []

    async def get_recent_completed(self, sport: Optional[str] = None, hours: int = 48) -> List[Dict[str, Any]]:
        """Fetch recently completed fixtures within time boundary."""
        try:
            return await database_router.fixtures.get_recent_completed(sport, hours)
        except Exception as e:
            print(f"[FixtureRepository] get_recent_completed error: {e}")
            return []

    async def get_by_date_and_sport(
        self,
        date_str: str,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Targeted date query: uses indexed date prefix match or date range,
        never loading historical collections or unrelated dates.
        """
        try:
            return await database_router.fixtures.get_by_date_and_sport(date_str, sport, league, limit)
        except Exception as e:
            print(f"[FixtureRepository] get_by_date_and_sport error: {e}")
            return []

    async def get_fixtures(
        self,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        date: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Operational fixture catalogue query with safe pagination and filters.
        """
        try:
            return await database_router.fixtures.get_fixtures(
                sport=sport,
                league=league,
                date=date,
                status=status,
                limit=limit,
                offset=offset,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as e:
            print(f"[FixtureRepository] get_fixtures error: {e}")
            return []

    async def count_fixtures(
        self,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        date: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        """Counts total operational fixtures matching query filters."""
        try:
            return await database_router.fixtures.count_fixtures(
                sport=sport,
                league=league,
                date=date,
                status=status,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as e:
            print(f"[FixtureRepository] count_fixtures error: {e}")
            return 0

    async def prune_expired_fixtures(self, retention_days: int = 14) -> int:
        """Prunes completed fixtures outside operational retention window."""
        try:
            return await database_router.fixtures.prune_expired_fixtures(retention_days=retention_days)
        except Exception as e:
            print(f"[FixtureRepository] prune_expired_fixtures error: {e}")
            return 0

    async def upsert_fixtures(self, fixtures: List[Dict[str, Any]]) -> int:
        """Batch-upsert operational fixtures with targeted field updates."""
        if not fixtures:
            return 0
        try:
            count = await database_router.fixtures.upsert_fixtures(fixtures)
            for fix in fixtures:
                f_id = fix.get("id")
                if f_id:
                    try:
                        await redis_client.set_json(f"predictpro:fixture:{f_id}", fix, ex_seconds=900)
                    except Exception:
                        pass
            return count
        except Exception as e:
            print(f"[FixtureRepository] upsert fixture error: {e}")
            return 0

    def ensure_indexes(self):
        """Ensures high-performance indexes exist for operational queries."""
        try:
            from backend.db.mongodb import mongo_manager
            col = mongo_manager.db["operational_events"]
            if col is not None:
                col.create_index([("id", 1)], unique=True)
                col.create_index([("sport", 1), ("kickoffUtc", 1)])
                col.create_index([("kickoffUtc", 1)])
                col.create_index([("status", 1)])
                col.create_index([("competition_id", 1)])
        except Exception as e:
            print(f"[FixtureRepository] index creation notice: {e}")

fixture_repository = FixtureRepository()
