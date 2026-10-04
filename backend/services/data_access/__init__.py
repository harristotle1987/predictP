"""
Targeted Data Access Layer for PredictPro.
Provides decoupled repository interfaces and high-performance scoped access to MongoDB, Redis,
and analytical stores, prohibiting full-table scans during normal serving and refresh flows.
"""

from .fixture_repository import FixtureRepository, fixture_repository
from .team_repository import TeamRepository, team_repository
from .feature_repository import FeatureRepository, feature_repository
from .prediction_repository import PredictionRepository, prediction_repository
from .model_repository import ModelRepository, model_repository
from .calibration_repository import CalibrationRepository, calibration_repository
from .refresh_state_repository import RefreshStateRepository, refresh_state_repository

__all__ = [
    "FixtureRepository",
    "fixture_repository",
    "TeamRepository",
    "team_repository",
    "FeatureRepository",
    "feature_repository",
    "PredictionRepository",
    "prediction_repository",
    "ModelRepository",
    "model_repository",
    "CalibrationRepository",
    "calibration_repository",
    "RefreshStateRepository",
    "refresh_state_repository",
]
