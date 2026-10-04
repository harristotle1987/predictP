"""
PredictPro Data Sources — Pydantic Schemas for FastAPI
Phase 1, Phase 2, Phase 3 Data Sources, Collections, and Ingestion Schemas
"""

from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field

SportType = Literal["football", "basketball", "baseball"]
OperationalEventStatus = Literal["scheduled", "live", "completed", "postponed", "canceled"]
MatchResult = Literal["home_win", "away_win", "draw"]

AdminPermission = Literal[
    "data_sources:read",
    "data_sources:sync",
    "historical:ingest",
    "historical:validate",
    "historical:publish",
    "historical:rollback",
    "features:rebuild",
    "models:train",
    "models:publish",
    "audit:read",
]

class OperationalScore(BaseModel):
    home: int
    away: int
    period_or_minute: Optional[str] = None

class SportSpecificDetails(BaseModel):
    home_xg: Optional[float] = None
    away_xg: Optional[float] = None
    home_corners: Optional[int] = None
    away_corners: Optional[int] = None
    quarter_scores: Optional[Dict[str, List[int]]] = None
    pace: Optional[float] = None
    home_hits: Optional[int] = None
    away_hits: Optional[int] = None

class OperationalEvent(BaseModel):
    id: str
    event_id: str
    sport: SportType
    source: str = "sports-skills"
    source_event_id: str
    source_key: str
    competition_id: str
    competition_name: str
    season: str
    scheduled_at: str
    status: OperationalEventStatus
    home_team_id: str
    home_team_name: str
    away_team_id: str
    away_team_name: str
    current_score: Optional[OperationalScore] = None
    final_score: Optional[OperationalScore] = None
    sport_details: SportSpecificDetails = Field(default_factory=SportSpecificDetails)
    is_stale: bool = False
    last_seen_at: str
    source_updated_at: str
    ingested_at: str

class HistoricalIngestionRequest(BaseModel):
    sport: SportType
    sources: Optional[List[str]] = None

class HistoricalIngestionRun(BaseModel):
    run_id: str
    sport: SportType
    sources: List[str]
    status: Literal["pending", "running", "completed", "failed"]
    source_versions: Dict[str, str]
    row_counts: Dict[str, int]
    validation_results: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)
    created_at: str
    completed_at: Optional[str] = None

class HistoricalDatasetManifest(BaseModel):
    sport: SportType
    dataset_version: str
    active: bool
    source_commits: Dict[str, str]
    checksums: Dict[str, str]
    row_count: int
    coverage: Dict[str, Any]
    r2_parquet_paths: List[str]
    r2_manifest_path: str
    published_at: str
    published_by: str
    checksum_sha256: str

class DataQualityReport(BaseModel):
    report_id: str
    sport: SportType
    dataset_version: str
    passed: bool
    duplicate_matches_detected: int
    chronology_errors: int
    future_leakage_detected: int
    referential_integrity_errors: int
    schema_violations: int
    total_checked: int
    details: List[str] = Field(default_factory=list)
    created_at: str

class SourceSyncRun(BaseModel):
    sync_id: str
    source: str = "sports-skills"
    sports: List[SportType]
    status: Literal["completed", "partial", "failed"]
    events_synced: int
    events_created: int
    events_updated: int
    events_stale: int
    errors: List[str] = Field(default_factory=list)
    duration_ms: float
    started_at: str
    completed_at: str
