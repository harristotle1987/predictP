"""
Targeted Data Access Facade.
Provides a consolidated interface for repositories, schema verification, and batch querying.
"""

from .fixture_repository import fixture_repository
from .team_repository import team_repository
from .feature_repository import feature_repository
from .prediction_repository import prediction_repository
from .model_repository import model_repository
from .calibration_repository import calibration_repository
from .refresh_state_repository import refresh_state_repository

class TargetedDataAccess:
    fixtures = fixture_repository
    teams = team_repository
    features = feature_repository
    predictions = prediction_repository
    models = model_repository
    calibrations = calibration_repository
    refresh_state = refresh_state_repository

    @classmethod
    def init_indexes(cls):
        """Initializes database indexes for all repositories."""
        cls.fixtures.ensure_indexes()
        cls.features.ensure_indexes()
        cls.predictions.ensure_indexes()

targeted_data_access = TargetedDataAccess()
