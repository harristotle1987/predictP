"""
Calibration Repository.
Manages calibration parameters, Platt scaling, isotonic mappings, and Brier score tracking per sport/model/market.
"""

from typing import Dict, Any, Optional
from backend.db.database_router import database_router
from backend.db.redis_client import redis_client

class CalibrationRepository:
    def __init__(self):
        pass

    async def get_calibration(
        self, sport: str, model_name: str, market_type: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch calibration profile for a specific sport, model, and market."""
        cache_key = f"predictpro:calibration:{sport}:{model_name}:{market_type}"
        try:
            cached = await redis_client.get_json(cache_key)
            if cached and isinstance(cached, dict):
                return cached
        except Exception:
            pass

        try:
            doc = await database_router.calibrations.get_calibration(sport, model_name, market_type)
            if doc:
                try:
                    await redis_client.set_json(cache_key, doc, ex_seconds=7200)
                except Exception:
                    pass
                return doc
        except Exception as e:
            print(f"[CalibrationRepository] get_calibration notice: {e}")

        return None

    async def save_calibration(
        self, sport: str, model_name: str, market_type: str, data: Dict[str, Any]
    ) -> bool:
        """Saves or updates calibration parameters."""
        cache_key = f"predictpro:calibration:{sport}:{model_name}:{market_type}"
        try:
            await redis_client.set_json(cache_key, data, ex_seconds=7200)
        except Exception:
            pass

        try:
            return await database_router.calibrations.save_calibration(sport, model_name, market_type, data)
        except Exception as e:
            print(f"[CalibrationRepository] save_calibration notice: {e}")
            return False

calibration_repository = CalibrationRepository()
