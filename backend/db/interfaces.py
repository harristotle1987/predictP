"""
Database Abstraction Layer - Interfaces and Contracts.
Defines clean repository abstractions for PredictPro operational data access.
Decouples application code from raw database drivers (MongoDB Atlas primary, Neon PostgreSQL secondary/failover).
Historical Parquet datasets and DuckDB remain strictly analytical and are NOT part of this operational layer.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


class RoutingMode(str, Enum):
    AUTO = "AUTO"
    MONGODB_ONLY = "MONGODB_ONLY"
    NEON_ONLY = "NEON_ONLY"


class FailoverState(str, Enum):
    MONGODB_PRIMARY = "MONGODB_PRIMARY"
    NEON_FAILOVER = "NEON_FAILOVER"
    RECOVERY_RECONCILIATION = "RECOVERY_RECONCILIATION"
    MONGODB_RESTORED = "MONGODB_RESTORED"

    # Backward-compatible aliases
    PRIMARY_HEALTHY = "MONGODB_PRIMARY"
    PRIMARY_DEGRADED = "MONGODB_PRIMARY"
    FAILOVER_ACTIVE = "NEON_FAILOVER"
    FAILOVER_PENDING = "RECOVERY_RECONCILIATION"
    FAILOVER_INELIGIBLE = "NEON_FAILOVER"


class DatabaseError(Exception):
    """Base exception for database abstraction operations."""
    pass


class DatabaseUnavailableError(DatabaseError):
    """Raised when the target database is unreachable or connection check fails."""
    pass


class FailoverNotPermittedError(DatabaseError):
    """Raised when failover is requested but secondary database is not validated or failover is inactive."""
    pass


class BudgetExceededError(DatabaseError):
    """Raised when Neon query/budget limits would be exceeded."""
    pass


@dataclass
class ReplicatedRecord:
    """
    Standard identity & version contract for replicated operational records.
    Every replicated record maps identically between MongoDB and Neon PostgreSQL.
    """
    stable_id: str
    source_system: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: int = 1


# =========================================================================
# Repository Interfaces
# =========================================================================

class IFixtureRepository(ABC):
    """Operations for fixtures and operational events."""

    @abstractmethod
    async def get_by_id(self, fixture_id: str) -> Optional[Dict[str, Any]]:
        """Fetch single fixture by stable ID."""
        pass

    @abstractmethod
    async def get_by_ids(self, fixture_ids: List[str]) -> List[Dict[str, Any]]:
        """Batch fetch multiple fixtures by stable IDs."""
        pass

    @abstractmethod
    async def get_live_fixtures(self, sport: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch active live/in_play fixtures."""
        pass

    @abstractmethod
    async def get_recent_completed(self, sport: Optional[str] = None, hours: int = 48) -> List[Dict[str, Any]]:
        """Fetch recently completed fixtures within bounded time window."""
        pass

    @abstractmethod
    async def get_by_date_and_sport(
        self,
        date_str: str,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Fetch fixtures for a specific date and optional sport/league filter."""
        pass

    @abstractmethod
    async def get_fixtures(
        self,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        date: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Paginated, bounded operational fixture catalogue query."""
        pass

    @abstractmethod
    async def count_fixtures(
        self,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        date: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        """Count operational fixtures matching filters."""
        pass

    @abstractmethod
    async def prune_expired_fixtures(self, retention_days: int = 14) -> int:
        """Prunes completed fixtures older than retention window while protecting live and recent fixtures."""
        pass

    @abstractmethod
    async def upsert_fixtures(self, fixtures: List[Dict[str, Any]]) -> int:
        """Upsert operational fixtures with stable IDs and updated timestamps."""
        pass


class IPredictionRepository(ABC):
    """Operations for published predictions and subscriber feeds."""

    @abstractmethod
    async def get_by_id(self, prediction_id: str) -> Optional[Dict[str, Any]]:
        """Fetch single prediction by ID."""
        pass

    @abstractmethod
    async def get_by_fixture_id(self, fixture_id: str) -> List[Dict[str, Any]]:
        """Fetch predictions for a fixture ID."""
        pass

    @abstractmethod
    async def get_daily_feed(
        self, date_str: str, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Fetch validated published predictions for a given date."""
        pass

    @abstractmethod
    async def get_published_feed(
        self, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch published predictions feed with projection."""
        pass

    @abstractmethod
    async def save_predictions(self, predictions: List[Dict[str, Any]]) -> int:
        """Persist predictions with stable IDs and version metadata."""
        pass


class IPredictionResultRepository(ABC):
    """Operations for evaluation outcomes and prediction verification."""

    @abstractmethod
    async def get_by_id(self, result_id: str) -> Optional[Dict[str, Any]]:
        """Fetch prediction result by ID."""
        pass

    @abstractmethod
    async def get_by_fixture_id(self, fixture_id: str) -> List[Dict[str, Any]]:
        """Fetch prediction results for a fixture ID."""
        pass

    @abstractmethod
    async def get_recent_results(self, sport: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch recent evaluated prediction results."""
        pass

    @abstractmethod
    async def save_results(self, results: List[Dict[str, Any]]) -> int:
        """Persist evaluated prediction outcomes."""
        pass


class IRefreshStateRepository(ABC):
    """Operations for refresh runs, audit logs, and sport/date freshness."""

    @abstractmethod
    async def save_run(self, run_record: Dict[str, Any]) -> bool:
        """Saves a completed or failed refresh run audit record."""
        pass

    @abstractmethod
    async def get_latest_run(self) -> Optional[Dict[str, Any]]:
        """Retrieves the most recent refresh run audit record."""
        pass

    @abstractmethod
    async def get_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieves recent refresh run audit records."""
        pass

    @abstractmethod
    async def is_sport_date_fresh(self, sport: str, date_str: str) -> bool:
        """Checks whether sport fixtures for date are fresh."""
        pass

    @abstractmethod
    async def mark_sport_date_fresh(self, sport: str, date_str: str, fixture_count: int = 0) -> None:
        """Marks sport fixtures for date as fresh with expiration."""
        pass


class IModelConfigRepository(ABC):
    """Operations for active model configuration and challenger state."""

    @abstractmethod
    async def get_active_model(self, sport: str = "football") -> str:
        """Retrieves active model name for sport."""
        pass

    @abstractmethod
    async def set_active_model(self, model: str, sport: str = "football", tier: str = "production") -> Dict[str, Any]:
        """Sets active model name for sport."""
        pass

    @abstractmethod
    async def get_model_config(self, sport: str = "football") -> Dict[str, Any]:
        """Retrieves full model configuration."""
        pass


class ICalibrationRepository(ABC):
    """Operations for calibration parameters, Platt scaling, and Brier scores."""

    @abstractmethod
    async def get_calibration(self, sport: str, model_name: str, market_type: str) -> Optional[Dict[str, Any]]:
        """Fetches calibration parameters for sport/model/market."""
        pass

    @abstractmethod
    async def save_calibration(
        self, sport: str, model_name: str, market_type: str, data: Dict[str, Any]
    ) -> bool:
        """Saves calibration parameters."""
        pass


class IModelGovernanceRepository(ABC):
    """Operations for model governance, validation gates, and promotion criteria."""

    @abstractmethod
    async def get_governance_record(self, sport: str, gate_name: str) -> Optional[Dict[str, Any]]:
        """Retrieves governance gate record."""
        pass

    @abstractmethod
    async def save_governance_record(
        self, sport: str, gate_name: str, status: str, criteria: Dict[str, Any], notes: Optional[str] = None
    ) -> bool:
        """Saves governance gate evaluation record."""
        pass

    @abstractmethod
    async def get_all_governance_records(self, sport: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves all governance gate records."""
        pass


class IFeatureRepository(ABC):
    """Operations for materialized team feature snapshots and point-in-time rating snapshots."""

    @abstractmethod
    async def get_team_snapshot(
        self, team_name: str, sport: str = "football", as_of: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the latest materialized feature snapshot for a team."""
        pass

    @abstractmethod
    async def get_batch_team_snapshots(
        self, team_names: List[str], sport: str = "football", as_of: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Batch-load feature snapshots for a collection of teams."""
        pass

    @abstractmethod
    async def save_team_snapshot(self, snapshot: Dict[str, Any]) -> bool:
        """Upsert a materialized feature snapshot."""
        pass


# =========================================================================
# Database Adapter Contract
# =========================================================================

class IDatabaseAdapter(ABC):
    """
    Contract for a complete operational database backend (MongoDB Atlas or Neon PostgreSQL).
    Provides typed repository access and connection lifecycle management.
    """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Returns the backend identifier ('mongodb' or 'neon')."""
        pass

    @abstractmethod
    def check_connection(self) -> Dict[str, Any]:
        """Performs a real ping/health check against the underlying database."""
        pass

    @abstractmethod
    def is_healthy(self) -> bool:
        """Returns True if the backend is connected and healthy."""
        pass

    @property
    @abstractmethod
    def fixtures(self) -> IFixtureRepository:
        pass

    @property
    @abstractmethod
    def predictions(self) -> IPredictionRepository:
        pass

    @property
    @abstractmethod
    def prediction_results(self) -> IPredictionResultRepository:
        pass

    @property
    @abstractmethod
    def refresh_state(self) -> IRefreshStateRepository:
        pass

    @property
    @abstractmethod
    def model_config(self) -> IModelConfigRepository:
        pass

    @property
    @abstractmethod
    def calibrations(self) -> ICalibrationRepository:
        pass

    @property
    @abstractmethod
    def governance(self) -> IModelGovernanceRepository:
        pass

    @property
    @abstractmethod
    def features(self) -> IFeatureRepository:
        pass
