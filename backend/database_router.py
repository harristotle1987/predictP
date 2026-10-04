"""
Root re-export of DatabaseRouter for module path consistency.
"""
from backend.db.database_router import (
    database_router,
    DatabaseRouter,
    RoutedFixtureRepository,
    RoutedPredictionRepository,
    RoutedPredictionResultRepository,
    RoutedRefreshStateRepository,
    RoutedModelConfigRepository,
    RoutedCalibrationRepository,
    RoutedModelGovernanceRepository,
    RoutedFeatureRepository,
)

__all__ = [
    "database_router",
    "DatabaseRouter",
    "RoutedFixtureRepository",
    "RoutedPredictionRepository",
    "RoutedPredictionResultRepository",
    "RoutedRefreshStateRepository",
    "RoutedModelConfigRepository",
    "RoutedCalibrationRepository",
    "RoutedModelGovernanceRepository",
    "RoutedFeatureRepository",
]
