"""
Dependency Planner.
Tracks dependencies (teams, features, models, calibrations) and versions (fixture_version,
feature_version, model_version, calibration_version) to enable safe prediction reuse.
"""

from typing import List, Dict, Any, Optional, Set
from backend.models.schemas import BaseModel, Field

CURRENT_FIXTURE_VERSION = 1
CURRENT_FEATURE_VERSION = "2.0.0"
CURRENT_CALIBRATION_VERSION = "2.0.0"

class DependencyPlan(BaseModel):
    total_fixtures: int = 0
    unique_teams_by_sport: Dict[str, List[str]] = Field(default_factory=dict)
    reusable_fixture_ids: List[str] = Field(default_factory=list)
    recalculable_fixtures: List[Dict[str, Any]] = Field(default_factory=list)
    required_calibrations: List[Dict[str, str]] = Field(default_factory=list)
    fixture_version: int = CURRENT_FIXTURE_VERSION
    feature_version: str = CURRENT_FEATURE_VERSION
    model_version: str = "ELO + POISSON"
    calibration_version: str = CURRENT_CALIBRATION_VERSION

class DependencyPlanner:
    """
    Plans dependency loading and determines which predictions need calculation vs reuse.
    """

    @staticmethod
    def plan_dependencies(
        fixtures: List[Dict[str, Any]],
        active_model: str,
        existing_predictions: Optional[Dict[str, Dict[str, Any]]] = None,
        force_recalculate: bool = False,
    ) -> DependencyPlan:
        unique_teams: Dict[str, Set[str]] = {}
        reusable_ids: List[str] = []
        recalc_fixtures: List[Dict[str, Any]] = []
        required_calibrations_set: Set[str] = set()

        existing = existing_predictions or {}

        for fix in fixtures:
            f_id = fix.get("id", "")
            sport = (fix.get("sport") or "football").lower().strip()
            if sport == "ice_hockey":
                sport = "hockey"
            home = (fix.get("homeTeam") or "").strip()
            away = (fix.get("awayTeam") or "").strip()

            if sport not in unique_teams:
                unique_teams[sport] = set()
            if home:
                unique_teams[sport].add(home)
            if away:
                unique_teams[sport].add(away)

            # Check if prediction can be safely reused
            can_reuse = False
            if not force_recalculate and f_id in existing:
                prev_pred = existing[f_id]
                # Compare versions and state
                prev_model = prev_pred.get("modelVersion")
                prev_feat_ver = prev_pred.get("feature_version", CURRENT_FEATURE_VERSION)
                prev_fix_ver = prev_pred.get("fixture_version", CURRENT_FIXTURE_VERSION)
                prev_status = prev_pred.get("status")
                current_status = str(fix.get("status", "scheduled")).lower()

                if (
                    prev_model == active_model
                    and prev_feat_ver == CURRENT_FEATURE_VERSION
                    and prev_fix_ver == fix.get("fixture_version", CURRENT_FIXTURE_VERSION)
                    and prev_status == current_status
                ):
                    can_reuse = True

            if can_reuse:
                reusable_ids.append(f_id)
            else:
                recalc_fixtures.append(fix)
                # Map required calibration
                required_calibrations_set.add(f"{sport}:{active_model}:MATCH_WINNER")
                if sport == "football":
                    required_calibrations_set.add(f"{sport}:{active_model}:TOTAL_GOALS")
                    required_calibrations_set.add(f"{sport}:{active_model}:BOTH_TEAMS_TO_SCORE")

        # Format calibrations
        calibrations_list: List[Dict[str, str]] = []
        for item in required_calibrations_set:
            parts = item.split(":")
            if len(parts) == 3:
                calibrations_list.append({"sport": parts[0], "model": parts[1], "market": parts[2]})

        return DependencyPlan(
            total_fixtures=len(fixtures),
            unique_teams_by_sport={k: sorted(list(v)) for k, v in unique_teams.items()},
            reusable_fixture_ids=reusable_ids,
            recalculable_fixtures=recalc_fixtures,
            required_calibrations=calibrations_list,
            fixture_version=CURRENT_FIXTURE_VERSION,
            feature_version=CURRENT_FEATURE_VERSION,
            model_version=active_model,
            calibration_version=CURRENT_CALIBRATION_VERSION,
        )

dependency_planner = DependencyPlanner()
