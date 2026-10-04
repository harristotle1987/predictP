"""
Shared Prediction Context and Model Requirements.
Loads shared data once per refresh/batch and passes self-contained contexts to prediction engines.
Prohibits database, Redis, and provider calls inside model loops.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.services.data_access.feature_repository import CURRENT_FEATURE_VERSION
from backend.services.feature_snapshot_service import feature_snapshot_service
from backend.services.data_access.calibration_repository import calibration_repository

@dataclass
class ModelRequirements:
    """Explicit data requirements for each statistical/ML prediction engine."""
    required_feature_keys: List[str] = field(default_factory=list)
    requires_competition_baseline: bool = True
    requires_calibrations: List[str] = field(default_factory=list)

    @classmethod
    def for_model(cls, model_name: str, sport: str = "football") -> "ModelRequirements":
        m_upper = model_name.upper()
        reqs = cls()
        if "ELO" in m_upper:
            reqs.required_feature_keys.extend(["elo_rating", "home_adv"])
        if "POISSON" in m_upper:
            reqs.required_feature_keys.extend(["attack_strength", "defense_strength", "avg_goals"])
        if "GLICKO" in m_upper or "GLICKO2" in m_upper:
            reqs.required_feature_keys.extend(["glicko_rating", "glicko_rd", "glicko_vol"])
        if "GRADIENT" in m_upper or "BOOSTING" in m_upper:
            reqs.required_feature_keys.extend(["form_points", "rolling_goals_scored", "rolling_goals_conceded"])

        if sport == "football":
            reqs.requires_calibrations = ["MATCH_WINNER", "TOTAL_GOALS", "BOTH_TEAMS_TO_SCORE"]
        else:
            reqs.requires_calibrations = ["MATCH_WINNER", "TOTAL_POINTS"]
        return reqs

@dataclass
class PredictionContext:
    """
    Self-contained execution context for a single fixture.
    Engines evaluate this context without any external I/O.
    """
    fixture_id: str
    sport: str
    league: str
    home_team: str
    away_team: str
    kickoff_utc: str
    model_version: str
    home_snapshot: Dict[str, Any]
    away_snapshot: Dict[str, Any]
    competition_baseline: Dict[str, Any] = field(default_factory=dict)
    calibrations: Dict[str, Any] = field(default_factory=dict)
    fixture_version: int = 1
    feature_version: str = CURRENT_FEATURE_VERSION

    def build_engine_features(self) -> Dict[str, Any]:
        """Assembles normalized features for ensemble engines from pre-loaded snapshots."""
        home_f = self.home_snapshot.get("features", {})
        away_f = self.away_snapshot.get("features", {})
        return {
            "home": home_f,
            "away": away_f,
            "matchDate": self.kickoff_utc,
            "league": self.league,
            "sport": self.sport,
            "competition_baseline": self.competition_baseline,
        }

class RefreshContext:
    """
    Batch context loader. Gathers all unique teams, snapshots, and calibrations
    in one combined batch operation prior to model execution.
    """

    def __init__(
        self,
        fixtures: List[Dict[str, Any]],
        active_model: str,
        sport: str = "football",
    ):
        self.fixtures = fixtures
        self.active_model = active_model
        self.sport = sport
        self._contexts: List[PredictionContext] = []

    async def initialize(self) -> List[PredictionContext]:
        """Batches data loading for all fixtures, producing decoupled contexts."""
        if not self.fixtures:
            return []

        # 1. Collect all unique team names
        team_names: List[str] = []
        for fix in self.fixtures:
            h = fix.get("homeTeam")
            a = fix.get("awayTeam")
            if h:
                team_names.append(h)
            if a:
                team_names.append(a)

        # 2. Batch-load snapshots in ONE call
        snapshots_map = await feature_snapshot_service.batch_load_snapshots(
            team_names, sport=self.sport
        )

        # 3. Load calibrations in advance
        reqs = ModelRequirements.for_model(self.active_model, sport=self.sport)
        calibrations_map: Dict[str, Any] = {}
        for mkt in reqs.requires_calibrations:
            cal = await calibration_repository.get_calibration(self.sport, self.active_model, mkt)
            if cal:
                calibrations_map[mkt] = cal

        # 4. Construct self-contained contexts
        contexts: List[PredictionContext] = []
        for fix in self.fixtures:
            f_id = fix.get("id", "")
            h_name = (fix.get("homeTeam") or "").strip()
            a_name = (fix.get("awayTeam") or "").strip()
            cutoff = fix.get("kickoffUtc") or fix.get("scheduled_at") or datetime.now(timezone.utc).isoformat()
            
            h_snap = snapshots_map.get(h_name.lower(), {})
            a_snap = snapshots_map.get(a_name.lower(), {})

            ctx = PredictionContext(
                fixture_id=f_id,
                sport=self.sport,
                league=fix.get("league", ""),
                home_team=h_name,
                away_team=a_name,
                kickoff_utc=cutoff,
                model_version=self.active_model,
                home_snapshot=h_snap,
                away_snapshot=a_snap,
                calibrations=calibrations_map,
                fixture_version=fix.get("fixture_version", 1),
                feature_version=CURRENT_FEATURE_VERSION,
            )
            contexts.append(ctx)

        self._contexts = contexts
        return contexts

    @property
    def contexts(self) -> List[PredictionContext]:
        return self._contexts
