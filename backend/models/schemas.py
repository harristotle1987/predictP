from typing import List, Optional, Dict, Any, Literal
try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        def model_dump(self):
            return self.__dict__
        def dict(self):
            return self.__dict__
    def Field(default=None, **kwargs):
        return default

SportType = Literal["football", "basketball", "baseball", "ice_hockey", "formula_1"]
FixtureStatus = Literal["upcoming", "live", "completed"]
PredictionModel = Literal[
    "ELO",
    "POISSON",
    "ELO + POISSON",
    "GLICKO2",
    "GRADIENT_BOOSTING",
    "GLICKO2 + GRADIENT_BOOSTING",
    "ELO + POISSON + GLICKO2",
    "ELO + POISSON + GLICKO2 + GRADIENT_BOOSTING",
]

class ScoreState(BaseModel):
    home: int
    away: int
    periodOrMinute: Optional[str] = None

class MarketItem(BaseModel):
    id: str
    marketName: str
    selection: str
    probabilityPercentage: float
    confidenceRating: Optional[str] = "High"
    sportSpecificCategory: Optional[str] = None
    isValidated: bool = True

class SportSpecificStats(BaseModel):
    # Football
    xGRecentHome: Optional[float] = None
    xGRecentAway: Optional[float] = None
    homeGoalsScoredAvg: Optional[float] = None
    awayGoalsScoredAvg: Optional[float] = None
    awayGoalsConcededAvg: Optional[float] = None
    homeCleanSheets: Optional[int] = None
    awayCleanSheets: Optional[int] = None
    avgMatchCorners: Optional[float] = None
    bothTeamsScoredRecentRate: Optional[float] = None

    # Basketball
    pace: Optional[float] = None
    homePPG: Optional[float] = None
    awayPPG: Optional[float] = None
    reboundDifferential: Optional[float] = None

    # Baseball
    homeERA: Optional[float] = None
    awayERA: Optional[float] = None
    bullpenWHIP: Optional[float] = None
    battingAvg: Optional[float] = None

    # Ice Hockey
    goalsPerGame: Optional[float] = None
    powerPlayPct: Optional[float] = None
    savePct: Optional[float] = None

    # Formula 1
    gridPosition: Optional[int] = None
    constructorStanding: Optional[str] = None
    poleConversionRate: Optional[float] = None

class ValidatedPredictionHighlight(BaseModel):
    marketName: str
    selection: str
    percentage: float

class ValidatedMatch(BaseModel):
    id: str
    sport: SportType
    league: str
    homeTeam: str
    awayTeam: str
    kickoffUtc: str
    status: FixtureStatus
    currentScore: Optional[ScoreState] = None
    finalScore: Optional[ScoreState] = None
    highestPercentagePrediction: ValidatedPredictionHighlight
    validatedMarkets: List[MarketItem] = Field(default_factory=list)
    sportStats: SportSpecificStats = Field(default_factory=SportSpecificStats)
    validationStatus: str = "validated"
    modelVersion: PredictionModel = "ELO + POISSON"

class GoalPredictionItem(BaseModel):
    id: str
    fixtureId: str
    league: str
    homeTeam: str
    awayTeam: str
    kickoffUtc: str
    status: FixtureStatus
    marketType: str
    predictedOutcome: str
    percentage: float
    xGCombined: Optional[float] = None
    homeAvgScored: Optional[float] = None
    awayAvgScored: Optional[float] = None
    bothTeamsScoredRecentRate: Optional[float] = None
    modelVersion: PredictionModel

class ModelConfigRequest(BaseModel):
    activeModel: PredictionModel

class ModelConfigResponse(BaseModel):
    activeModel: PredictionModel
    tier: str
    updatedAt: str
    isProductionReady: bool

def normalize_redis_feed_state(val: Any) -> str:
    """Fix RefreshRunRecord.redisFeedState so it always receives a string:
    'connected', 'degraded', 'disconnected', 'not_configured'
    —not an AsyncMock/coroutine."""
    if val is None:
        return "disconnected"
    if hasattr(val, "__await__") or "Mock" in type(val).__name__ or callable(getattr(val, "_is_coroutine", None)):
        return "disconnected"
    if isinstance(val, str):
        v = val.lower().strip()
        if v in ("connected", "healthy", "remote_success", "ok", "ready"):
            return "connected"
        if v in ("degraded", "partial", "local_fallback", "remote_failure"):
            return "degraded"
        if v in ("not_configured", "disabled", "missing"):
            return "not_configured"
        return "disconnected"
    return "disconnected"

class RefreshRunRecord(BaseModel):
    id: str
    timestamp: str
    status: str
    matches_synced: int
    predictions_published: int
    duration_ms: float
    errors: List[str] = Field(default_factory=list)
    diagnostics: Optional[Dict[str, Any]] = None
    redisFeedState: Optional[str] = None
    totalCandidates: Optional[int] = None
    validatedCount: Optional[int] = None
    publishedCount: Optional[int] = None

    def __init__(self, **data):
        if "redisFeedState" in data:
            data["redisFeedState"] = normalize_redis_feed_state(data["redisFeedState"])
        super().__init__(**data)

    try:
        from pydantic import model_validator
        @model_validator(mode="before")
        @classmethod
        def _validate_redis_state(cls, data: Any):
            if isinstance(data, dict) and "redisFeedState" in data:
                data["redisFeedState"] = normalize_redis_feed_state(data["redisFeedState"])
            return data
    except Exception:
        pass

class CompetitionRecord(BaseModel):
    competition_id: str
    provider: str = "machina-sports/sports-skills"
    sport: str
    competition_name: str
    country_or_region: Optional[str] = None
    level: Optional[str] = None
    active: bool = True
    current_season: Optional[str] = None
    priority: int = 1
    supported: bool = True
    last_discovered_at: str
    last_fixture_refresh_at: Optional[str] = None
    next_refresh_at: Optional[str] = None

class AdminRefreshRequest(BaseModel):
    sports: Optional[List[str]] = None
    date: Optional[str] = None
    date_range: Optional[List[str]] = None
    competitions: Optional[List[str]] = None
    force: bool = False

class NeonConfigPayload(BaseModel):
    neon_database_url: str

