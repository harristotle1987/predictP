"""
Model Repository.
Manages model configurations, active production models, and model version metadata.
"""

from typing import Dict, Any, Optional
from backend.db.database_router import database_router
from backend.db.redis_client import redis_client

DEFAULT_PRODUCTION_MODEL = "ELO + POISSON"

class ModelRepository:
    def __init__(self):
        pass

    async def get_active_model(self, sport: str = "football") -> str:
        """Retrieves the active model for a sport, checking Redis then operational store."""
        cache_key = f"predictpro:model:{sport}"
        try:
            cached = await redis_client.get_str(cache_key)
            if cached and cached.strip():
                return cached.strip()
        except Exception:
            pass

        try:
            model = await database_router.model_config.get_active_model(sport)
            if model:
                try:
                    await redis_client.set_str(cache_key, model, ex_seconds=3600)
                except Exception:
                    pass
                return model
        except Exception as e:
            print(f"[ModelRepository] get_active_model notice: {e}")

        return DEFAULT_PRODUCTION_MODEL

    async def set_active_model(self, model: str, sport: str = "football") -> bool:
        """Sets the active model and updates Redis + operational database."""
        cache_key = f"predictpro:model:{sport}"
        try:
            await redis_client.set_str(cache_key, model, ex_seconds=3600)
        except Exception:
            pass

        try:
            await database_router.model_config.set_active_model(model, sport=sport)
            return True
        except Exception as e:
            print(f"[ModelRepository] set_active_model notice: {e}")
            return False

model_repository = ModelRepository()
