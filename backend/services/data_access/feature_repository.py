"""
Feature Repository.
Manages materialized team feature snapshots and point-in-time rating snapshots.
Prevents unindexed historical scans on normal prediction and serving paths.
Routed strictly through DatabaseRouter (MongoDB Atlas primary -> Neon PostgreSQL secondary).
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from backend.db.database_router import database_router
from backend.db.redis_client import redis_client

CURRENT_FEATURE_VERSION = "2.0.0"

class FeatureRepository:
    def __init__(self):
        pass

    async def get_team_snapshot(
        self, team_name: str, sport: str = "football", as_of: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the latest materialized feature snapshot for a team."""
        if not team_name:
            return None
        norm_name = team_name.lower().strip()
        cache_key = f"predictpro:feature:{sport}:{norm_name}"

        # If no cutoff or current cutoff, try Redis cache
        if not as_of:
            try:
                cached = await redis_client.get_json(cache_key)
                if cached and isinstance(cached, dict):
                    return cached
            except Exception:
                pass

        try:
            doc = await database_router.features.get_team_snapshot(norm_name, sport, as_of)
            if doc:
                if not as_of:
                    try:
                        await redis_client.set_json(cache_key, doc, ex_seconds=1800)
                    except Exception:
                        pass
                return doc
        except Exception as e:
            print(f"[FeatureRepository] get_team_snapshot error: {e}")

        return None

    async def get_batch_team_snapshots(
        self, team_names: List[str], sport: str = "football", as_of: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Batch-load feature snapshots for a collection of teams in one single query.
        Eliminates N+1 database lookups inside the model execution loop.
        """
        if not team_names:
            return {}

        try:
            return await database_router.features.get_batch_team_snapshots(team_names, sport, as_of)
        except Exception as e:
            print(f"[FeatureRepository] get_batch_team_snapshots error: {e}")
            return {}

    async def save_team_snapshot(self, snapshot: Dict[str, Any]) -> bool:
        """Upsert a materialized feature snapshot through DatabaseRouter."""
        if not snapshot or "team_name" not in snapshot:
            return False

        t_name = snapshot["team_name"].lower().strip()
        sport = snapshot.get("sport", "football")
        as_of = snapshot.get("as_of") or datetime.now(timezone.utc).isoformat()
        
        snapshot["team_name"] = t_name
        snapshot["sport"] = sport
        snapshot["as_of"] = as_of
        snapshot["feature_version"] = snapshot.get("feature_version", CURRENT_FEATURE_VERSION)
        snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            saved = await database_router.features.save_team_snapshot(snapshot)
            if saved:
                try:
                    await redis_client.set_json(f"predictpro:feature:{sport}:{t_name}", snapshot, ex_seconds=1800)
                except Exception:
                    pass
            return saved
        except Exception as e:
            print(f"[FeatureRepository] save_team_snapshot error: {e}")
            return False

    def ensure_indexes(self):
        # Index maintenance handled at database adapter startup
        pass

feature_repository = FeatureRepository()

