"""
Prediction Repository.
Manages persistent storage, projections, and Redis publication for predictions and daily subscriber feeds.
Enforces that normal serving reads from Redis with targeted MongoDB fallback.
"""

from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone
from backend.db.database_router import database_router
from backend.db.redis_client import redis_client

FEED_PROJECTION = {
    "_id": 0,
    "id": 1,
    "fixtureId": 1,
    "fixture_id": 1,
    "sport": 1,
    "league": 1,
    "competition_id": 1,
    "homeTeam": 1,
    "awayTeam": 1,
    "home_team": 1,
    "away_team": 1,
    "driver": 1,
    "opponent": 1,
    "kickoffUtc": 1,
    "scheduled_at": 1,
    "date": 1,
    "event_date_lagos": 1,
    "status": 1,
    "currentScore": 1,
    "finalScore": 1,
    "isBestOfDay": 1,
    "bestMarket": 1,
    "markets": 1,
    "market": 1,
    "selection": 1,
    "percentage": 1,
    "raw_probability": 1,
    "calibrated_probability": 1,
    "highestPercentagePrediction": 1,
    "validatedMarkets": 1,
    "sportStats": 1,
    "validationStatus": 1,
    "modelVersion": 1,
    "model": 1,
    "model_version": 1,
    "model_name": 1,
    "modelMetadata": 1,
    "metadata": 1,
    "training_window": 1,
    "training_match_count": 1,
    "feature_timestamp": 1,
    "prediction_timestamp": 1,
    "calibratedPercentage": 1,
    "confidenceScore": 1,
    "published": 1,
    "published_at": 1,
    "prediction_version": 1,
    "created_at": 1,
    "updated_at": 1,
}

class PredictionRepository:
    def __init__(self):
        pass

    async def get_daily_feed(
        self, date_str: str, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Reads the published daily feed from Redis (fast serving path)
        with targeted operational database fallback if cache is missed or Redis fails.
        Enforces 20-item subscriber limit and sorting:
        1. best-of-day first
        2. highest calibrated probability
        3. kickoff time
        """
        cache_key = f"predictpro:feed:{date_str}"
        items: List[Dict[str, Any]] = []
        is_cache_hit = False

        try:
            cached = await redis_client.get_json(cache_key)
            if cached and isinstance(cached, list) and len(cached) > 0:
                items = cached
                is_cache_hit = True
        except Exception as e:
            print(f"[PredictionRepository] Redis feed read notice: {e}")

        # Fallback to database_router (MongoDB primary or Neon failover)
        if not is_cache_hit:
            try:
                items = await database_router.predictions.get_daily_feed(date_str, sport=sport, league=league, limit=50)
                if items:
                    try:
                        await redis_client.set_json(cache_key, items, ex_seconds=1800)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[PredictionRepository] Database feed read notice: {e}")
                items = []

        # Gate: Filter strictly to validated items with non-empty validatedMarkets
        validated = [
            m for m in items
            if m.get("validationStatus") == "validated" and m.get("validatedMarkets")
        ]

        if sport and sport.lower() != "all":
            validated = [i for i in validated if i.get("sport", "").lower() == sport.lower()]
        if league and league.lower() != "all":
            validated = [i for i in validated if i.get("league", "").lower() == league.lower()]

        # Sort by:
        # 1. best-of-day first
        # 2. highest calibrated probability
        # 3. kickoff time
        def _sort_key(item: Dict[str, Any]):
            is_best = 0 if item.get("isBestOfDay") else 1
            prob_val = 0.0
            if item.get("calibratedPercentage") is not None:
                prob_val = float(item["calibratedPercentage"])
            elif item.get("percentage") is not None:
                prob_val = float(item["percentage"])
            elif isinstance(item.get("highestPercentagePrediction"), dict) and item["highestPercentagePrediction"].get("percentage") is not None:
                prob_val = float(item["highestPercentagePrediction"]["percentage"])
            kickoff = str(item.get("kickoffUtc") or item.get("scheduled_at") or "")
            return (is_best, -prob_val, kickoff)

        sorted_items = sorted(validated, key=_sort_key)
        max_allowed = min(20, max(1, limit))
        return sorted_items[:max_allowed]

    async def get_by_fixture_id(self, fixture_id: str) -> Optional[Dict[str, Any]]:
        """Fetch all markets/prediction record for a specific fixture."""
        if not fixture_id:
            return None
        try:
            records = await database_router.predictions.get_by_fixture_id(fixture_id)
            return records[0] if records else None
        except Exception as e:
            print(f"[PredictionRepository] get_by_fixture_id notice: {e}")
            return None

    async def save_predictions(self, predictions: List[Dict[str, Any]]) -> int:
        """Batch-upsert predictions to active operational database."""
        if not predictions:
            return 0
        try:
            return await database_router.predictions.save_predictions(predictions)
        except Exception as e:
            print(f"[PredictionRepository] save prediction error: {e}")
            return 0

    async def publish_daily_feed(self, date_str: str, feed_items: List[Dict[str, Any]]) -> bool:
        """
        Publishes the validated daily feed:
        1. Saves validated published predictions to active operational database FIRST.
        2. Only after successful persistence publishes them to Redis.
        3. If Redis fails:
           - do NOT delete the database predictions
           - do NOT change validationStatus
           - do NOT report that the prediction itself failed
           - mark only Redis publication as degraded.
        """
        cache_key = f"predictpro:feed:{date_str}"
        
        # 1. Persist in active operational database FIRST
        try:
            await database_router.predictions.save_predictions(feed_items)
        except Exception as e:
            print(f"[PredictionRepository] Operational store persist error: {e}")
            return False

        # 2. Only after successful persistence publish to Redis
        try:
            w_res = await redis_client.set_json(cache_key, feed_items, ex_seconds=86400)
            if not w_res:
                print(f"[PredictionRepository] Redis feed publish degraded: {getattr(w_res, 'state', 'DEGRADED')}")
        except Exception as e:
            print(f"[PredictionRepository] Redis feed publish notice: {e}")

        # Returns True because database persistence succeeded
        return True

    def ensure_indexes(self):
        try:
            from backend.db.mongodb import mongo_manager
            p_col = mongo_manager.db["predictions"]
            if p_col is not None:
                p_col.create_index([("fixtureId", 1)], unique=True)
                p_col.create_index([("sport", 1), ("kickoffUtc", 1)])
                p_col.create_index([("validationStatus", 1)])
        except Exception as e:
            print(f"[PredictionRepository] index notice: {e}")

prediction_repository = PredictionRepository()
