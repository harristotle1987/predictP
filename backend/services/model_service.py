from datetime import datetime, timezone
from typing import Dict, Any
import asyncio
import concurrent.futures
from backend.models.schemas import PredictionModel, ModelConfigResponse
from backend.db.database_router import database_router

PRODUCTION_MODELS = {"ELO", "POISSON", "ELO + POISSON", "F1RatingEngine + F1ProbabilityEngine", "F1"}
DEFAULT_MODEL: PredictionModel = "ELO + POISSON"

def run_sync(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    else:
        return asyncio.run(coro)

class ModelService:
    def get_active_model(self) -> str:
        try:
            doc = run_sync(database_router.model_config.get_active_model())
            if doc:
                return doc
        except Exception as e:
            print(f"[ModelService] Failed to read active model: {e}")
        return DEFAULT_MODEL

    def set_active_model(self, model: PredictionModel) -> ModelConfigResponse:
        from backend.db.failover_manager import failover_manager
        from backend.db.interfaces import FailoverState

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        is_prod = model in PRODUCTION_MODELS
        tier = "production" if is_prod else "evaluation"

        # Model Governance Rule: Challenger models cannot become active production during failover
        if failover_manager.failover_state == FailoverState.NEON_FAILOVER and not is_prod:
            raise ValueError("Challenger model cannot become production during database failover")

        try:
            run_sync(database_router.model_config.set_active_model(model, sport="football", tier=tier))
        except Exception as e:
            print(f"[ModelService] Failed to persist active model: {e}")
            raise

        return ModelConfigResponse(
            activeModel=model,
            tier=tier,
            updatedAt=now_iso,
            isProductionReady=is_prod,
        )

model_service = ModelService()
