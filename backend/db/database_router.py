"""
Database Router.
Central router dispatching operational database operations to either MongoDB Atlas (Primary)
or Neon PostgreSQL (Secondary/Failover).
FastAPI exclusively controls routing mode; frontend cannot choose active database.
Enforces fail-closed rules and exposes routing status, health, and transitions.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from backend.config import settings
from backend.db.interfaces import (
    RoutingMode,
    IDatabaseAdapter,
    IFixtureRepository,
    IPredictionRepository,
    IPredictionResultRepository,
    IRefreshStateRepository,
    IModelConfigRepository,
    ICalibrationRepository,
    IModelGovernanceRepository,
    IFeatureRepository,
    DatabaseUnavailableError,
)
from backend.db.mongodb_adapter import mongodb_adapter
from backend.db.neon_adapter import neon_adapter
from backend.db.failover_manager import failover_manager


class RoutedFixtureRepository(IFixtureRepository):
    """Dispatches fixture queries to the currently routed operational database."""

    def __init__(self, router: "DatabaseRouter"):
        self.router = router

    async def get_by_id(self, fixture_id: str):
        adapter = self.router.get_read_adapter()
        return await adapter.fixtures.get_by_id(fixture_id)

    async def get_by_ids(self, fixture_ids):
        adapter = self.router.get_read_adapter()
        return await adapter.fixtures.get_by_ids(fixture_ids)

    async def get_live_fixtures(self, sport: Optional[str] = None):
        adapter = self.router.get_read_adapter()
        return await adapter.fixtures.get_live_fixtures(sport)

    async def get_recent_completed(self, sport: Optional[str] = None, hours: int = 48):
        adapter = self.router.get_read_adapter()
        return await adapter.fixtures.get_recent_completed(sport, hours)

    async def get_by_date_and_sport(self, date_str: str, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 50):
        adapter = self.router.get_read_adapter()
        return await adapter.fixtures.get_by_date_and_sport(date_str, sport, league, limit)

    async def get_fixtures(self, sport: Optional[str] = None, league: Optional[str] = None, date: Optional[str] = None, status: Optional[str] = None, limit: int = 50, offset: int = 0, start_date: Optional[str] = None, end_date: Optional[str] = None):
        adapter = self.router.get_read_adapter()
        return await adapter.fixtures.get_fixtures(sport=sport, league=league, date=date, status=status, limit=limit, offset=offset, start_date=start_date, end_date=end_date)

    async def count_fixtures(self, sport: Optional[str] = None, league: Optional[str] = None, date: Optional[str] = None, status: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None):
        adapter = self.router.get_read_adapter()
        return await adapter.fixtures.count_fixtures(sport=sport, league=league, date=date, status=status, start_date=start_date, end_date=end_date)

    async def prune_expired_fixtures(self, retention_days: int = 14):
        adapter = self.router.get_write_adapter()
        return await adapter.fixtures.prune_expired_fixtures(retention_days=retention_days)

    async def upsert_fixtures(self, fixtures):
        adapter = self.router.get_write_adapter()
        return await adapter.fixtures.upsert_fixtures(fixtures)


class RoutedPredictionRepository(IPredictionRepository):
    """Dispatches prediction queries to the currently routed operational database."""

    def __init__(self, router: "DatabaseRouter"):
        self.router = router

    async def get_by_id(self, prediction_id: str):
        adapter = self.router.get_read_adapter()
        return await adapter.predictions.get_by_id(prediction_id)

    async def get_by_fixture_id(self, fixture_id: str):
        adapter = self.router.get_read_adapter()
        return await adapter.predictions.get_by_fixture_id(fixture_id)

    async def get_daily_feed(self, date_str: str, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 20):
        adapter = self.router.get_read_adapter()
        return await adapter.predictions.get_daily_feed(date_str, sport, league, limit)

    async def get_published_feed(self, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 50):
        adapter = self.router.get_read_adapter()
        return await adapter.predictions.get_published_feed(sport, league, limit)

    async def save_predictions(self, predictions):
        adapter = self.router.get_write_adapter()
        return await adapter.predictions.save_predictions(predictions)


class RoutedPredictionResultRepository(IPredictionResultRepository):
    """Dispatches prediction result queries to the currently routed operational database."""

    def __init__(self, router: "DatabaseRouter"):
        self.router = router

    async def get_by_id(self, result_id: str):
        adapter = self.router.get_read_adapter()
        return await adapter.prediction_results.get_by_id(result_id)

    async def get_by_fixture_id(self, fixture_id: str):
        adapter = self.router.get_read_adapter()
        return await adapter.prediction_results.get_by_fixture_id(fixture_id)

    async def get_recent_results(self, sport: Optional[str] = None, limit: int = 100):
        adapter = self.router.get_read_adapter()
        return await adapter.prediction_results.get_recent_results(sport, limit)

    async def save_results(self, results):
        adapter = self.router.get_write_adapter()
        return await adapter.prediction_results.save_results(results)


class RoutedRefreshStateRepository(IRefreshStateRepository):
    """Dispatches refresh state queries to the currently routed operational database."""

    def __init__(self, router: "DatabaseRouter"):
        self.router = router

    async def save_run(self, run_record: Dict[str, Any]):
        adapter = self.router.get_write_adapter()
        return await adapter.refresh_state.save_run(run_record)

    async def get_latest_run(self):
        adapter = self.router.get_read_adapter()
        return await adapter.refresh_state.get_latest_run()

    async def get_runs(self, limit: int = 20):
        adapter = self.router.get_read_adapter()
        return await adapter.refresh_state.get_runs(limit)

    async def is_sport_date_fresh(self, sport: str, date_str: str):
        adapter = self.router.get_read_adapter()
        return await adapter.refresh_state.is_sport_date_fresh(sport, date_str)

    async def mark_sport_date_fresh(self, sport: str, date_str: str, fixture_count: int = 0):
        adapter = self.router.get_write_adapter()
        return await adapter.refresh_state.mark_sport_date_fresh(sport, date_str, fixture_count)


class RoutedModelConfigRepository(IModelConfigRepository):
    """Dispatches model configuration queries to the currently routed operational database."""

    def __init__(self, router: "DatabaseRouter"):
        self.router = router

    async def get_active_model(self, sport: str = "football"):
        adapter = self.router.get_read_adapter()
        return await adapter.model_config.get_active_model(sport)

    async def set_active_model(self, model: str, sport: str = "football", tier: str = "production"):
        adapter = self.router.get_write_adapter()
        return await adapter.model_config.set_active_model(model, sport, tier)

    async def get_model_config(self, sport: str = "football"):
        adapter = self.router.get_read_adapter()
        return await adapter.model_config.get_model_config(sport)


class RoutedCalibrationRepository(ICalibrationRepository):
    """Dispatches calibration queries to the currently routed operational database."""

    def __init__(self, router: "DatabaseRouter"):
        self.router = router

    async def get_calibration(self, sport: str, model_name: str, market_type: str):
        adapter = self.router.get_read_adapter()
        return await adapter.calibrations.get_calibration(sport, model_name, market_type)

    async def save_calibration(self, sport: str, model_name: str, market_type: str, data: Dict[str, Any]):
        adapter = self.router.get_write_adapter()
        return await adapter.calibrations.save_calibration(sport, model_name, market_type, data)


class RoutedModelGovernanceRepository(IModelGovernanceRepository):
    """Dispatches model governance queries to the currently routed operational database."""

    def __init__(self, router: "DatabaseRouter"):
        self.router = router

    async def get_governance_record(self, sport: str, gate_name: str):
        adapter = self.router.get_read_adapter()
        return await adapter.governance.get_governance_record(sport, gate_name)

    async def save_governance_record(self, sport: str, gate_name: str, status: str, criteria: Dict[str, Any], notes: Optional[str] = None):
        adapter = self.router.get_write_adapter()
        return await adapter.governance.save_governance_record(sport, gate_name, status, criteria, notes)

    async def get_all_governance_records(self, sport: Optional[str] = None):
        adapter = self.router.get_read_adapter()
        return await adapter.governance.get_all_governance_records(sport)


class RoutedFeatureRepository(IFeatureRepository):
    """Dispatches feature snapshot queries to the currently routed operational database."""

    def __init__(self, router: "DatabaseRouter"):
        self.router = router

    async def get_team_snapshot(self, team_name: str, sport: str = "football", as_of: Optional[str] = None):
        adapter = self.router.get_read_adapter()
        return await adapter.features.get_team_snapshot(team_name, sport, as_of)

    async def get_batch_team_snapshots(self, team_names: List[str], sport: str = "football", as_of: Optional[str] = None):
        adapter = self.router.get_read_adapter()
        return await adapter.features.get_batch_team_snapshots(team_names, sport, as_of)

    async def save_team_snapshot(self, snapshot: Dict[str, Any]):
        adapter = self.router.get_write_adapter()
        return await adapter.features.save_team_snapshot(snapshot)


class DatabaseRouter:
    """
    Central operational database router.
    Supports AUTO, MONGODB_ONLY, and NEON_ONLY routing modes.
    AUTO initially remains MongoDB-primary.
    Exposes active backend, health, read/write targets, failover state, and transition reasons.
    """

    def __init__(self):
        raw_mode = getattr(settings, "database_routing_mode", "NEON_ONLY")
        try:
            self._routing_mode = RoutingMode(raw_mode.upper())
        except Exception:
            self._routing_mode = RoutingMode.NEON_ONLY

        self._fixtures = RoutedFixtureRepository(self)
        self._predictions = RoutedPredictionRepository(self)
        self._prediction_results = RoutedPredictionResultRepository(self)
        self._refresh_state = RoutedRefreshStateRepository(self)
        self._model_config = RoutedModelConfigRepository(self)
        self._calibrations = RoutedCalibrationRepository(self)
        self._governance = RoutedModelGovernanceRepository(self)
        self._features = RoutedFeatureRepository(self)

    @property
    def routing_mode(self) -> RoutingMode:
        return self._routing_mode

    def set_routing_mode(self, mode: RoutingMode) -> None:
        """Sets routing mode. Backend owned by FastAPI server."""
        self._routing_mode = mode

    def get_active_database_name(self) -> str:
        if self._routing_mode == RoutingMode.MONGODB_ONLY:
            return "mongodb"
        if self._routing_mode == RoutingMode.NEON_ONLY:
            return "neon"
        resolved = failover_manager.resolve_backend(self._routing_mode.value)
        return "mongodb" if resolved in ("mongodb", "mongo") else "neon"

    def get_adapter(self, backend_name: str = "") -> IDatabaseAdapter:
        if backend_name in ("mongodb", "mongo"):
            return mongodb_adapter
        if backend_name == "neon":
            return neon_adapter
        return self.get_read_adapter()

    def get_read_adapter(self) -> IDatabaseAdapter:
        if self._routing_mode == RoutingMode.MONGODB_ONLY:
            return mongodb_adapter
        if self._routing_mode == RoutingMode.NEON_ONLY:
            neon_adapter.assert_operational_connection()
            return neon_adapter
        resolved = failover_manager.resolve_backend(self._routing_mode.value)
        if resolved in ("mongodb", "mongo"):
            return mongodb_adapter
        neon_adapter.assert_operational_connection()
        return neon_adapter

    def get_write_adapter(self) -> IDatabaseAdapter:
        if self._routing_mode == RoutingMode.MONGODB_ONLY:
            return mongodb_adapter
        if self._routing_mode == RoutingMode.NEON_ONLY:
            neon_adapter.assert_operational_connection()
            return neon_adapter
        resolved = failover_manager.resolve_backend(self._routing_mode.value)
        if resolved in ("mongodb", "mongo"):
            return mongodb_adapter
        neon_adapter.assert_operational_connection()
        return neon_adapter

    def get_status(self) -> Dict[str, Any]:
        """
        Returns full router status:
        - active backend
        - backend health
        - read backend
        - write backend
        - failover state
        - last transition
        - reason for transition
        """
        health_info = failover_manager.check_health()
        
        # Try resolving read/write backend
        try:
            resolved_backend = failover_manager.resolve_backend(self._routing_mode.value)
            read_target = resolved_backend
            write_target = resolved_backend
        except Exception as e:
            read_target = f"unavailable ({e})"
            write_target = f"unavailable ({e})"

        return {
            "routingMode": self._routing_mode.value,
            "activeBackend": failover_manager.active_backend,
            "readBackend": read_target,
            "writeBackend": write_target,
            "failoverState": failover_manager.failover_state.value,
            "failoverActive": failover_manager.failover_active,
            "lastTransition": failover_manager.last_transition,
            "reasonForTransition": failover_manager.transition_reason,
            "backendHealth": health_info.get("backends", {}),
            "neonBudget": health_info.get("neonBudget", {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Repository accessors
    @property
    def fixtures(self) -> IFixtureRepository:
        return self._fixtures

    @property
    def predictions(self) -> IPredictionRepository:
        return self._predictions

    @property
    def prediction_results(self) -> IPredictionResultRepository:
        return self._prediction_results

    @property
    def refresh_state(self) -> IRefreshStateRepository:
        return self._refresh_state

    @property
    def model_config(self) -> IModelConfigRepository:
        return self._model_config

    @property
    def calibrations(self) -> ICalibrationRepository:
        return self._calibrations

    @property
    def governance(self) -> IModelGovernanceRepository:
        return self._governance

    @property
    def features(self) -> IFeatureRepository:
        return self._features


# Global Singleton DatabaseRouter
database_router = DatabaseRouter()
