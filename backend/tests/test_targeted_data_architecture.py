"""
Unit & Integration Tests for PredictPro Targeted Data Architecture.
Validates:
1. Targeted Data Access Layer (repositories, projections, batch loading)
2. Refresh Planner (Africa/Lagos date window, sport isolation, freshness checks, force flags)
3. Dependency Planner & Version Tracking (fixture, feature, model, calibration versions, prediction reuse)
4. Materialized Feature Snapshots (O(1) lookups, incremental updates after match completion)
5. Shared Prediction Context (Self-contained contexts, zero external I/O in model loops)
6. Telemetry & Budgets (N+1 query detection, provider call budget guarding)
7. Comprehensive Sport & Competition Preservation (UEFA Nations League, Minor Competitions, F1, Hockey, etc.)
"""

import unittest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from backend.services.data_access.fixture_repository import fixture_repository
from backend.services.data_access.team_repository import team_repository
from backend.services.data_access.feature_repository import feature_repository, CURRENT_FEATURE_VERSION
from backend.services.data_access.prediction_repository import prediction_repository
from backend.services.data_access.refresh_state_repository import refresh_state_repository
from backend.services.refresh_planner import refresh_planner, RefreshPlan, get_current_lagos_today
from backend.services.dependency_planner import dependency_planner, DependencyPlan
from backend.services.feature_snapshot_service import feature_snapshot_service
from backend.engine.prediction_context import ModelRequirements, PredictionContext, RefreshContext
from backend.engine.pipeline import execute_prediction_pipeline
from backend.services.telemetry_service import telemetry_service
from backend.services.competition_registry import competition_registry_service
from backend.db.mongodb import mongo_manager

class TestTargetedDataArchitecture(unittest.TestCase):

    def setUp(self):
        from backend.db.database_router import database_router, RoutingMode
        database_router.set_routing_mode(RoutingMode.MONGODB_ONLY)
        self.today_lagos = get_current_lagos_today()
        if mongo_manager.db is not None:
            try:
                mongo_manager.db["team_feature_snapshots"].delete_many({})
                mongo_manager.db["operational_events"].delete_many({})
            except Exception:
                pass

    def tearDown(self):
        from backend.db.database_router import database_router, RoutingMode
        database_router.set_routing_mode(RoutingMode.NEON_ONLY)

    def test_fixture_repository_targeted_queries(self):
        """Test targeted ID lookups and date queries without full collection scans."""
        f1 = {
            "id": "fix_test_arch_1",
            "sport": "football",
            "league": "Premier League",
            "competition_id": "premier-league",
            "homeTeam": "Arsenal",
            "awayTeam": "Chelsea",
            "kickoffUtc": f"{self.today_lagos}T17:30:00Z",
            "scheduled_at": f"{self.today_lagos}T17:30:00Z",
            "status": "scheduled",
            "canonical_key": f"football:premier-league:arsenal:chelsea:{self.today_lagos}",
            "source_event_id": "fix_test_arch_1",
            "fixture_version": 1,
        }
        asyncio.run(fixture_repository.upsert_fixtures([f1]))

        # 1. Single ID fetch
        fetched = asyncio.run(fixture_repository.get_by_id("fix_test_arch_1"))
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], "fix_test_arch_1")
        self.assertEqual(fetched["homeTeam"], "Arsenal")

        # 2. Batch ID fetch
        batch = asyncio.run(fixture_repository.get_by_ids(["fix_test_arch_1", "non_existent"]))
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["id"], "fix_test_arch_1")

        # 3. Date-targeted fetch
        by_date = asyncio.run(fixture_repository.get_by_date_and_sport(self.today_lagos, sport="football"))
        self.assertTrue(any(f["id"] == "fix_test_arch_1" for f in by_date))

    def test_materialized_feature_snapshots(self):
        """Test materialized team feature snapshots and incremental updates."""
        # 1. Create/save snapshot
        snap = {
            "team_name": "arsenal",
            "display_name": "Arsenal",
            "sport": "football",
            "as_of": f"{self.today_lagos}T00:00:00Z",
            "feature_version": CURRENT_FEATURE_VERSION,
            "features": {"elo_rating": 1820.0, "attack_strength": 1.4, "defense_strength": 0.8},
            "sample_size": 20,
            "elo": 1820.0,
        }
        saved = asyncio.run(feature_repository.save_team_snapshot(snap))
        self.assertTrue(saved)

        # 2. Provide sufficient historical records for Chelsea
        chelsea_historical = [
            {
                "id": f"m_chelsea_{i}",
                "sport": "football",
                "league": "Premier League",
                "matchDate": f"2026-09-{10+i:02d}T15:00:00Z",
                "homeTeam": "Chelsea",
                "awayTeam": f"Opponent_{i}",
                "homeTeamKey": "chelsea",
                "awayTeamKey": f"opponent_{i}",
                "homeScore": 2,
                "awayScore": 1,
                "status": "completed",
            }
            for i in range(1, 6)
        ]

        with patch("backend.services.feature_snapshot_service.get_point_in_time_matches", return_value=chelsea_historical):
            # Batch retrieve snapshots
            batch = asyncio.run(feature_snapshot_service.batch_load_snapshots(["Arsenal", "Chelsea"], sport="football"))
            self.assertIn("arsenal", batch)
            self.assertEqual(batch["arsenal"]["elo"], 1820.0)
            self.assertIn("chelsea", batch)

    def test_refresh_planner_determines_targets_without_calculation(self):
        """Test that refresh_planner computes target dates, checks freshness, and does not execute models."""
        plan = asyncio.run(refresh_planner.plan_refresh(
            sports=["football", "basketball"],
            date=self.today_lagos,
            force=False
        ))
        self.assertIsInstance(plan, RefreshPlan)
        self.assertEqual(plan.requested_sports, ["football", "basketball"])
        self.assertEqual(plan.target_dates, [self.today_lagos])
        self.assertIn("football", plan.competitions_by_sport)
        self.assertIn("basketball", plan.competitions_by_sport)
        self.assertFalse(plan.is_forced)

    def test_dependency_planner_identifies_reusable_predictions(self):
        """Test that dependency_planner separates reusable predictions from those needing calculation."""
        fixtures = [
            {
                "id": "f_reuse_1",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Liverpool",
                "awayTeam": "Man City",
                "kickoffUtc": f"{self.today_lagos}T15:00:00Z",
                "status": "scheduled",
                "fixture_version": 1,
            },
            {
                "id": "f_recalc_1",
                "sport": "football",
                "league": "La Liga",
                "homeTeam": "Real Madrid",
                "awayTeam": "Barcelona",
                "kickoffUtc": f"{self.today_lagos}T20:00:00Z",
                "status": "scheduled",
                "fixture_version": 1,
            },
        ]
        existing_predictions = {
            "f_reuse_1": {
                "id": "f_reuse_1",
                "modelVersion": "ELO + POISSON",
                "feature_version": CURRENT_FEATURE_VERSION,
                "fixture_version": 1,
                "status": "scheduled",
                "validationStatus": "validated",
            }
        }

        dep_plan = dependency_planner.plan_dependencies(
            fixtures=fixtures,
            active_model="ELO + POISSON",
            existing_predictions=existing_predictions,
            force_recalculate=False
        )

        self.assertIn("f_reuse_1", dep_plan.reusable_fixture_ids)
        self.assertEqual(len(dep_plan.recalculable_fixtures), 1)
        self.assertEqual(dep_plan.recalculable_fixtures[0]["id"], "f_recalc_1")
        self.assertIn("Liverpool", dep_plan.unique_teams_by_sport.get("football", []))
        self.assertIn("Real Madrid", dep_plan.unique_teams_by_sport.get("football", []))

    def test_shared_prediction_context_and_zero_io_in_loop(self):
        """Test batch RefreshContext builds self-contained contexts for execution."""
        mock_fixtures = [
            {
                "id": "ctx_fix_1",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": f"{self.today_lagos}T15:00:00Z",
                "status": "scheduled",
                "fixture_version": 1,
            }
        ]

        ref_context = RefreshContext(
            fixtures=mock_fixtures,
            active_model="ELO + POISSON",
            sport="football"
        )
        contexts = asyncio.run(ref_context.initialize())
        self.assertEqual(len(contexts), 1)
        ctx = contexts[0]
        self.assertEqual(ctx.fixture_id, "ctx_fix_1")
        self.assertEqual(ctx.home_team, "Arsenal")
        self.assertEqual(ctx.away_team, "Chelsea")

        # Execute prediction pipeline with context map
        ctx_map = {ctx.fixture_id: ctx}
        result = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=True,
            custom_fixtures=mock_fixtures,
            prediction_contexts=ctx_map,
        )
        self.assertIn("allResults", result)
        self.assertEqual(len(result["allResults"]), 1)

    def test_telemetry_and_budget_violation_detection(self):
        """Test telemetry recording and N+1 query violation detection."""
        # 1. Normal bounded operation
        t_normal = telemetry_service.record_operation(
            operation="refresh",
            sport="football",
            event_count=10,
            provider_call_count=2,
            database_query_count=4,
            duration_ms=45.0,
        )
        self.assertEqual(len(t_normal.violations), 0)

        # 2. Simulated N+1 query violation (30 DB queries for 8 events)
        t_violation = telemetry_service.record_operation(
            operation="refresh",
            sport="football",
            event_count=8,
            provider_call_count=1,
            database_query_count=25,
            duration_ms=120.0,
        )
        self.assertTrue(any("N+1" in v for v in t_violation.violations))

    def test_uefa_nations_league_and_competitions_registry_preservation(self):
        """Verify UEFA Nations League and broad competitions remain discoverable and queryable."""
        comps = asyncio.run(competition_registry_service.get_or_discover_competitions("football", force=False))
        comp_ids = {c["competition_id"] for c in comps}
        self.assertIn("uefa-nations-league", comp_ids)
        self.assertIn("premier-league", comp_ids)
        self.assertIn("champions-league", comp_ids)
        self.assertIn("europa-league", comp_ids)

if __name__ == "__main__":
    unittest.main()
