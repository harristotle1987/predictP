"""
Team Repository.
Provides targeted queries for team profiles, aliases, and league associations.
Prohibits unindexed collection scans.
"""

from typing import List, Dict, Any, Optional, Set
from backend.db.mongodb import mongo_manager
from backend.db.redis_client import redis_client

class TeamRepository:
    def __init__(self):
        pass

    def _get_collection(self):
        try:
            return mongo_manager.db["teams"]
        except Exception:
            return None

    async def get_by_name(self, team_name: str, sport: str = "football") -> Optional[Dict[str, Any]]:
        """Fetch team profile with Redis cache-aside."""
        if not team_name:
            return None
        norm_name = team_name.lower().strip()
        cache_key = f"predictpro:team:{sport}:{norm_name}"
        try:
            cached = await redis_client.get_json(cache_key)
            if cached and isinstance(cached, dict):
                return cached
        except Exception:
            pass

        col = self._get_collection()
        if col is None:
            return {"name": team_name, "sport": sport, "normalized_name": norm_name}

        try:
            doc = col.find_one(
                {"$or": [{"normalized_name": norm_name}, {"name": team_name}], "sport": sport},
                {"_id": 0}
            )
            if doc:
                try:
                    await redis_client.set_json(cache_key, doc, ex_seconds=3600)
                except Exception:
                    pass
                return doc
        except Exception as e:
            print(f"[TeamRepository] get_by_name notice: {e}")

        return {"name": team_name, "sport": sport, "normalized_name": norm_name}

    async def get_batch_by_names(self, team_names: List[str], sport: str = "football") -> Dict[str, Dict[str, Any]]:
        """Batch-load team profiles for a set of teams."""
        if not team_names:
            return {}
        unique_names = list(set([n.strip() for n in team_names if n and n.strip()]))
        res: Dict[str, Dict[str, Any]] = {}

        for name in unique_names:
            norm = name.lower()
            res[norm] = {"name": name, "sport": sport, "normalized_name": norm}

        col = self._get_collection()
        if col is None:
            return res

        try:
            norm_list = [n.lower() for n in unique_names]
            docs = list(col.find(
                {"$or": [{"normalized_name": {"$in": norm_list}}, {"name": {"$in": unique_names}}], "sport": sport},
                {"_id": 0}
            ).limit(len(unique_names) * 2))
            for doc in docs:
                k = (doc.get("normalized_name") or doc.get("name", "")).lower()
                if k:
                    res[k] = doc
        except Exception as e:
            print(f"[TeamRepository] get_batch_by_names notice: {e}")

        return res

team_repository = TeamRepository()
