"""
Refresh Planner.
Plans targeted refresh tasks BEFORE fetching provider data or executing models.
Evaluates the Africa/Lagos window, cached competitions, and sport/date freshness.
Never calculates predictions.
"""

import uuid
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone, timedelta
from backend.models.schemas import BaseModel, Field

from backend.services.competition_registry import competition_registry_service
from backend.services.data_access.refresh_state_repository import refresh_state_repository
from backend.config import settings

OPERATIONAL_FIXTURE_HORIZON_DAYS = int(getattr(settings, "operational_fixture_horizon_days", 7))
LAGOS_TZ = timezone(timedelta(hours=1))

def get_current_lagos_today() -> str:
    return datetime.now(LAGOS_TZ).strftime("%Y-%m-%d")

class RefreshPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:12]}")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    requested_sports: List[str]
    target_dates: List[str]
    competitions_by_sport: Dict[str, List[str]] = Field(default_factory=dict)
    stale_dates_by_sport: Dict[str, List[str]] = Field(default_factory=dict)
    fresh_dates_by_sport: Dict[str, List[str]] = Field(default_factory=dict)
    planned_provider_calls: int = 0
    avoided_provider_calls: int = 0
    is_forced: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

class RefreshPlanner:
    """
    Plans targeted refresh jobs across sports, dates, and competitions.
    """

    @staticmethod
    def compute_lagos_date_window(
        date: Optional[str] = None,
        date_range: Optional[List[str]] = None,
        horizon_days: Optional[int] = None,
    ) -> List[str]:
        """Computes authoritative Africa/Lagos target date window."""
        if date_range and len(date_range) > 0:
            return sorted(list(set(date_range)))
        if date:
            return [date]

        if horizon_days is None:
            horizon_days = getattr(settings, "operational_fixture_horizon_days", 7)
        horizon_days = max(1, horizon_days)

        now_lagos = datetime.now(LAGOS_TZ)
        today = now_lagos.strftime("%Y-%m-%d")
        window = [today]
        for i in range(1, horizon_days):
            next_d = (now_lagos + timedelta(days=i)).strftime("%Y-%m-%d")
            window.append(next_d)
        return window

    async def plan_refresh(
        self,
        sports: Optional[List[str]] = None,
        date: Optional[str] = None,
        date_range: Optional[List[str]] = None,
        competitions: Optional[List[str]] = None,
        force: bool = False,
        horizon_days: Optional[int] = None,
    ) -> RefreshPlan:
        """
        Creates a structured, deterministic RefreshPlan for the requested parameters.
        """
        all_supported_sports = ["football", "basketball", "baseball", "hockey", "formula_1"]
        if not sports:
            active_sports = all_supported_sports
        else:
            active_sports = [
                "hockey" if s.lower() == "ice_hockey"
                else "formula_1" if s.lower() == "f1"
                else s.lower()
                for s in sports
            ]

        target_dates = self.compute_lagos_date_window(date=date, date_range=date_range, horizon_days=horizon_days)

        competitions_by_sport: Dict[str, List[str]] = {}
        stale_dates_by_sport: Dict[str, List[str]] = {}
        fresh_dates_by_sport: Dict[str, List[str]] = {}
        planned_calls = 0
        avoided_calls = 0

        for sp in active_sports:
            # 1. Retrieve cached/discovered competitions
            cached_comps = await competition_registry_service.get_or_discover_competitions(sport=sp, force=force)
            comp_ids = [c["competition_id"] for c in cached_comps if c.get("active", True)]
            if competitions:
                # Filter to requested competitions
                comp_ids = [c for c in comp_ids if c in competitions]
            competitions_by_sport[sp] = comp_ids

            # 2. Check freshness for each date
            stale_dates: List[str] = []
            fresh_dates: List[str] = []

            for d in target_dates:
                if force:
                    stale_dates.append(d)
                    planned_calls += 1
                else:
                    is_fresh = await refresh_state_repository.is_sport_date_fresh(sp, d)
                    if is_fresh:
                        fresh_dates.append(d)
                        avoided_calls += 1
                    else:
                        stale_dates.append(d)
                        planned_calls += 1

            stale_dates_by_sport[sp] = stale_dates
            fresh_dates_by_sport[sp] = fresh_dates

        return RefreshPlan(
            requested_sports=active_sports,
            target_dates=target_dates,
            competitions_by_sport=competitions_by_sport,
            stale_dates_by_sport=stale_dates_by_sport,
            fresh_dates_by_sport=fresh_dates_by_sport,
            planned_provider_calls=planned_calls,
            avoided_provider_calls=avoided_calls,
            is_forced=force,
            metadata={
                "lagos_today": get_current_lagos_today(),
                "all_competitions_count": sum(len(c) for c in competitions_by_sport.values()),
            }
        )

refresh_planner = RefreshPlanner()
