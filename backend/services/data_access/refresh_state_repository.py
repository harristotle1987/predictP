"""
Refresh State Repository.
Manages refresh runs, diagnostics, audit logs, and sport/date freshness state.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from backend.db.database_router import database_router
from backend.db.redis_client import redis_client

FRESHNESS_TTL_SECONDS = 900  # 15 minutes operational window

class RefreshStateRepository:
    def __init__(self):
        pass

    async def save_run(self, run_record: Dict[str, Any]) -> bool:
        """Saves a completed or failed refresh run audit record."""
        try:
            return await database_router.refresh_state.save_run(run_record)
        except Exception as e:
            print(f"[RefreshStateRepository] save_run notice: {e}")
            return False

    async def get_latest_run(self) -> Optional[Dict[str, Any]]:
        """Retrieves the most recent refresh run audit record."""
        try:
            return await database_router.refresh_state.get_latest_run()
        except Exception as e:
            print(f"[RefreshStateRepository] get_latest_run notice: {e}")
            return None

    async def is_sport_date_fresh(self, sport: str, date_str: str) -> bool:
        """Checks whether the sport's fixtures for a specific date are fresh."""
        cache_key = f"predictpro:freshness:{sport}:{date_str}"
        try:
            cached = await redis_client.get_json(cache_key)
            if cached and isinstance(cached, dict):
                return True
        except Exception:
            pass

        try:
            return await database_router.refresh_state.is_sport_date_fresh(sport, date_str)
        except Exception as e:
            print(f"[RefreshStateRepository] is_sport_date_fresh notice: {e}")
            return False

    async def mark_sport_date_fresh(self, sport: str, date_str: str, fixture_count: int = 0) -> bool:
        """Records that a sport and date was recently refreshed."""
        cache_key = f"predictpro:freshness:{sport}:{date_str}"
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "sport": sport,
            "date": date_str,
            "fetched_at": now_iso,
            "fixture_count": fixture_count,
        }

        try:
            await redis_client.set_json(cache_key, payload, ex_seconds=FRESHNESS_TTL_SECONDS)
        except Exception:
            pass

        try:
            await database_router.refresh_state.mark_sport_date_fresh(sport, date_str, fixture_count)
            return True
        except Exception as e:
            print(f"[RefreshStateRepository] mark_sport_date_fresh notice: {e}")
            return True

refresh_state_repository = RefreshStateRepository()
