"""
Neon Prediction Repository.
Provides direct access to Neon PostgreSQL prediction persistence.
"""
from typing import List, Dict, Any, Optional
from backend.db.neon_adapter import NeonPredictionRepository, neon_adapter

__all__ = ["NeonPredictionRepository", "neon_prediction_repository"]

neon_prediction_repository = neon_adapter.predictions
