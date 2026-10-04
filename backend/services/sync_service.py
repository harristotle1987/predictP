import time
import uuid
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Set
try:
    import httpx
except ImportError:
    httpx = None

from backend.db.redis_client import redis_client
from backend.db.mongodb import mongo_manager
from backend.db.neon_adapter import neon_adapter
from backend.db.duckdb_engine import duckdb_engine
from backend.db.r2_storage import r2_manager
from backend.providers.football_adapter import football_adapter
from backend.providers.basketball_adapter import basketball_adapter
from backend.providers.baseball_adapter import baseball_adapter
from backend.providers.sports_skills_hockey_provider import sports_skills_hockey_provider
from backend.providers.sports_skills_f1_provider import sports_skills_f1_provider
from backend.providers.capability_map import get_provider_capability
from backend.services.model_service import model_service
from backend.services.competition_registry import competition_registry_service
from backend.services.freshness_policy import freshness_policy
from backend.services.call_budget import CallBudgetGuard
from backend.models.schemas import RefreshRunRecord, normalize_redis_feed_state
from backend.engine.pipeline import execute_prediction_pipeline
from backend.engine.evaluation_pipeline import evaluation_pipeline
from backend.services.feed_service import get_lagos_date_str, get_current_lagos_today, LAGOS_TZ
from backend.db.replication_manager import replication_manager
from backend.db.database_router import database_router
from backend.services.refresh_planner import refresh_planner
from backend.services.dependency_planner import dependency_planner
from backend.services.feature_snapshot_service import feature_snapshot_service
from backend.services.telemetry_service import telemetry_service
from backend.config import settings

OPERATIONAL_FIXTURE_HORIZON_DAYS = int(getattr(settings, "operational_fixture_horizon_days", 7))

class SyncService:
    """
    Universal Refresh Architecture (Football, Basketball, Baseball, Hockey, F1).
    - Competition/event aware
    - Date-aware (Africa/Lagos prediction window)
    - Stale-data aware (sport-specific TTL freshness checks)
    - Call-budget guarded against excessive scraping
    - Modular per-sport planners
    - Canonical fixture deduplication
    - Targeted prediction execution
    """

    def __init__(self):
        self._freshness_cache: Dict[str, Dict[str, Any]] = {}

    def _normalize_slug(self, text: str) -> str:
        if not text:
            return ""
        return re.sub(r"[^a-z0-9]", "", str(text).lower())

    def _generate_canonical_fixture_key(self, fix: Dict[str, Any]) -> str:
        from backend.utils.text_normalize import normalize_team_name
        sport = (fix.get("sport") or "football").lower().strip()
        league = self._normalize_slug(fix.get("league") or fix.get("competition_id") or "")
        home = self._normalize_slug(normalize_team_name(fix.get("homeTeam") or fix.get("home_team") or fix.get("driver") or ""))
        away = self._normalize_slug(normalize_team_name(fix.get("awayTeam") or fix.get("away_team") or fix.get("opponent") or ""))
        kickoff = str(fix.get("kickoffUtc") or fix.get("scheduled_at") or "")[:16]
        return f"{sport}:{league}:{home}:{away}:{kickoff}"

    # Alias sync_feed to execute_refresh
    async def sync_feed(self, *args, **kwargs):
        return await self.execute_refresh(*args, **kwargs)

    async def _get_cached_fixtures_for_sport_date(self, sport: str, d_str: str) -> List[Dict[str, Any]]:
        """
        Targeted indexed query for cached operational fixtures for a specific sport and date.
        Replaces broad collection find() with indexed date queries and projections.
        """
        try:
            return await database_router.fixtures.get_by_date_and_sport(d_str, sport=sport, limit=100)
        except Exception as e:
            print(f"[SyncService] Targeted cached fixtures query notice for {sport} {d_str}: {e}")
            return []

    async def is_sport_date_fresh(self, sport: str, date_str: str, status_category: str = "upcoming_today") -> bool:
        cache_key = f"{sport}:{date_str}"
        info = self._freshness_cache.get(cache_key)
        if info:
            last_iso = info.get("fetched_at_iso")
            if freshness_policy.is_fresh(last_iso, sport, status_category) and info.get("fixture_count", 0) > 0:
                return True

        try:
            return await database_router.refresh_state.is_sport_date_fresh(sport, date_str)
        except Exception:
            pass

        return False

    async def mark_sport_date_fresh(self, sport: str, date_str: str, fixture_count: int):
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        cache_key = f"{sport}:{date_str}"
        self._freshness_cache[cache_key] = {
            "fetched_at_iso": now_iso,
            "fixture_count": fixture_count,
        }
        try:
            await database_router.refresh_state.mark_sport_date_fresh(sport, date_str, fixture_count)
        except Exception:
            pass

    # =========================================================================
    # Sport Refresh Planners
    # =========================================================================

    async def refresh_football(
        self,
        date_range: List[str],
        force: bool = False,
        competitions: Optional[List[str]] = None,
        call_budget: Optional[CallBudgetGuard] = None,
    ) -> Dict[str, Any]:
        fixtures: List[Dict[str, Any]] = []
        errors: List[str] = []
        calls_executed = 0
        cache_hits = 0
        queried_dates: List[str] = []

        registry_comps = await competition_registry_service.get_or_discover_competitions("football", force=force)
        comp_map = {c.get("competition_id"): c for c in registry_comps if c.get("competition_id")}
        competitions_checked = len(registry_comps)

        for d_str in date_range:
            if not force and await self.is_sport_date_fresh("football", d_str, "upcoming_today"):
                cache_hits += 1
                if call_budget:
                    call_budget.record_cache_hit()
                try:
                    matched = await self._get_cached_fixtures_for_sport_date("football", d_str)
                    fixtures.extend(matched)
                except Exception as e:
                    errors.append(f"Football cached load notice for {d_str}: {e}")
                continue

            if call_budget and not call_budget.record_call_request():
                errors.append(f"Football refresh for {d_str} skipped: provider call budget exceeded")
                continue

            queried_dates.append(d_str)
            calls_executed += 1
            try:
                date_fixtures = await football_adapter.fetch_fixtures(date_str=d_str)
                for fix in date_fixtures:
                    raw_league = fix.get("league", "")
                    raw_comp_id = fix.get("competition_id", "")
                    matched_comp = comp_map.get(raw_comp_id) or competition_registry_service.match_competition_name(raw_league, registry_comps)
                    if matched_comp:
                        fix["competition_id"] = matched_comp.get("competition_id", raw_comp_id)
                        fix["league"] = matched_comp.get("competition_name", raw_league)
                        fix["country_or_region"] = matched_comp.get("country_or_region")
                        fix["competitionTier"] = "primary" if matched_comp.get("priority", 1) == 1 else "secondary"
                fixtures.extend(date_fixtures)
                await self.mark_sport_date_fresh("football", d_str, len(date_fixtures))
                if call_budget:
                    call_budget.record_call_executed(len(date_fixtures))
            except Exception as e:
                errors.append(f"SportsSkills Football error for date {d_str}: {str(e)}")
                if call_budget:
                    call_budget.record_failure()

        if competitions:
            norm_req = {self._normalize_slug(c) for c in competitions}
            fixtures = [
                f for f in fixtures
                if self._normalize_slug(f.get("competition_id", "")) in norm_req
                or self._normalize_slug(f.get("league", "")) in norm_req
            ]

        relevant_comp_ids = {f.get("competition_id") for f in fixtures if f.get("competition_id")}

        return {
            "sport": "football",
            "fixtures": fixtures,
            "fetched_count": len(fixtures),
            "fixtures_updated": len(fixtures),
            "competitions_checked": competitions_checked,
            "competitions_relevant": len(relevant_comp_ids),
            "provider_calls": calls_executed,
            "cache_hits": cache_hits,
            "calls_avoided": cache_hits,
            "queried_dates": queried_dates,
            "errors": errors,
        }

    async def refresh_basketball(
        self,
        date_range: List[str],
        force: bool = False,
        competitions: Optional[List[str]] = None,
        call_budget: Optional[CallBudgetGuard] = None,
    ) -> Dict[str, Any]:
        fixtures: List[Dict[str, Any]] = []
        errors: List[str] = []
        calls_executed = 0
        cache_hits = 0
        queried_dates: List[str] = []

        registry_comps = await competition_registry_service.get_or_discover_competitions("basketball", force=force)
        competitions_checked = len(registry_comps)

        for d_str in date_range:
            if not force and await self.is_sport_date_fresh("basketball", d_str, "upcoming_today"):
                cache_hits += 1
                if call_budget:
                    call_budget.record_cache_hit()
                try:
                    matched = await self._get_cached_fixtures_for_sport_date("basketball", d_str)
                    fixtures.extend(matched)
                except Exception as e:
                    errors.append(f"Basketball cached load notice for {d_str}: {e}")
                continue

            if call_budget and not call_budget.record_call_request():
                errors.append(f"Basketball refresh for {d_str} skipped: provider call budget exceeded")
                continue

            queried_dates.append(d_str)
            calls_executed += 1
            try:
                date_fixtures = await basketball_adapter.fetch_fixtures(date_str=d_str)
                fixtures.extend(date_fixtures)
                await self.mark_sport_date_fresh("basketball", d_str, len(date_fixtures))
                if call_budget:
                    call_budget.record_call_executed(len(date_fixtures))
            except Exception as e:
                errors.append(f"SportsSkills Basketball error for date {d_str}: {str(e)}")
                if call_budget:
                    call_budget.record_failure()

        relevant_comp_ids = {f.get("competition_id") for f in fixtures if f.get("competition_id")}

        return {
            "sport": "basketball",
            "fixtures": fixtures,
            "fetched_count": len(fixtures),
            "fixtures_updated": len(fixtures),
            "competitions_checked": competitions_checked,
            "competitions_relevant": len(relevant_comp_ids) if fixtures else (1 if len(fixtures) > 0 else 0),
            "provider_calls": calls_executed,
            "cache_hits": cache_hits,
            "calls_avoided": cache_hits,
            "queried_dates": queried_dates,
            "errors": errors,
        }

    async def refresh_baseball(
        self,
        date_range: List[str],
        force: bool = False,
        competitions: Optional[List[str]] = None,
        call_budget: Optional[CallBudgetGuard] = None,
    ) -> Dict[str, Any]:
        fixtures: List[Dict[str, Any]] = []
        errors: List[str] = []
        calls_executed = 0
        cache_hits = 0
        queried_dates: List[str] = []

        registry_comps = await competition_registry_service.get_or_discover_competitions("baseball", force=force)
        competitions_checked = len(registry_comps)

        for d_str in date_range:
            if not force and await self.is_sport_date_fresh("baseball", d_str, "upcoming_today"):
                cache_hits += 1
                if call_budget:
                    call_budget.record_cache_hit()
                try:
                    matched = await self._get_cached_fixtures_for_sport_date("baseball", d_str)
                    fixtures.extend(matched)
                except Exception as e:
                    errors.append(f"Baseball cached load notice for {d_str}: {e}")
                continue

            if call_budget and not call_budget.record_call_request():
                errors.append(f"Baseball refresh for {d_str} skipped: provider call budget exceeded")
                continue

            queried_dates.append(d_str)
            calls_executed += 1
            try:
                date_fixtures = await baseball_adapter.fetch_fixtures(date_str=d_str)
                fixtures.extend(date_fixtures)
                await self.mark_sport_date_fresh("baseball", d_str, len(date_fixtures))
                if call_budget:
                    call_budget.record_call_executed(len(date_fixtures))
            except Exception as e:
                errors.append(f"SportsSkills Baseball error for date {d_str}: {str(e)}")
                if call_budget:
                    call_budget.record_failure()

        relevant_comp_ids = {f.get("competition_id") for f in fixtures if f.get("competition_id")}

        return {
            "sport": "baseball",
            "fixtures": fixtures,
            "fetched_count": len(fixtures),
            "fixtures_updated": len(fixtures),
            "competitions_checked": competitions_checked,
            "competitions_relevant": len(relevant_comp_ids) if fixtures else (1 if len(fixtures) > 0 else 0),
            "provider_calls": calls_executed,
            "cache_hits": cache_hits,
            "calls_avoided": cache_hits,
            "queried_dates": queried_dates,
            "errors": errors,
        }

    async def refresh_hockey(
        self,
        date_range: List[str],
        force: bool = False,
        competitions: Optional[List[str]] = None,
        call_budget: Optional[CallBudgetGuard] = None,
    ) -> Dict[str, Any]:
        fixtures: List[Dict[str, Any]] = []
        errors: List[str] = []
        calls_executed = 0
        cache_hits = 0
        queried_dates: List[str] = []

        registry_comps = await competition_registry_service.get_or_discover_competitions("hockey", force=force)
        competitions_checked = len(registry_comps)

        for d_str in date_range:
            if not force and await self.is_sport_date_fresh("hockey", d_str, "upcoming_today"):
                cache_hits += 1
                if call_budget:
                    call_budget.record_cache_hit()
                try:
                    matched = await self._get_cached_fixtures_for_sport_date("hockey", d_str)
                    fixtures.extend(matched)
                except Exception as e:
                    errors.append(f"Hockey cached load notice for {d_str}: {e}")
                continue

            if call_budget and not call_budget.record_call_request():
                errors.append(f"Hockey refresh for {d_str} skipped: provider call budget exceeded")
                continue

            queried_dates.append(d_str)
            calls_executed += 1
            try:
                date_fixtures = await sports_skills_hockey_provider.fetch_fixtures(date_str=d_str)
                fixtures.extend(date_fixtures)
                await self.mark_sport_date_fresh("hockey", d_str, len(date_fixtures))
                if call_budget:
                    call_budget.record_call_executed(len(date_fixtures))
            except Exception as e:
                errors.append(f"SportsSkills Hockey error for date {d_str}: {str(e)}")
                if call_budget:
                    call_budget.record_failure()

        relevant_comp_ids = {f.get("competition_id") for f in fixtures if f.get("competition_id")}

        return {
            "sport": "hockey",
            "fixtures": fixtures,
            "fetched_count": len(fixtures),
            "fixtures_updated": len(fixtures),
            "competitions_checked": competitions_checked,
            "competitions_relevant": len(relevant_comp_ids) if fixtures else (1 if len(fixtures) > 0 else 0),
            "provider_calls": calls_executed,
            "cache_hits": cache_hits,
            "calls_avoided": cache_hits,
            "queried_dates": queried_dates,
            "errors": errors,
        }

    async def refresh_f1(
        self,
        date_range: List[str],
        force: bool = False,
        call_budget: Optional[CallBudgetGuard] = None,
    ) -> Dict[str, Any]:
        fixtures: List[Dict[str, Any]] = []
        errors: List[str] = []
        calls_executed = 0
        cache_hits = 0
        queried_dates: List[str] = []
        primary_date = date_range[0] if date_range else get_current_lagos_today()

        if not force and await self.is_sport_date_fresh("formula_1", primary_date, "upcoming_future"):
            cache_hits += 1
            if call_budget:
                call_budget.record_cache_hit()
            try:
                fixtures.extend(await self._get_cached_fixtures_for_sport_date("formula_1", primary_date))
            except Exception as e:
                errors.append(f"F1 cached load notice: {e}")
        else:
            if call_budget and not call_budget.record_call_request():
                errors.append("F1 refresh skipped: provider call budget exceeded")
            else:
                queried_dates.append(primary_date)
                calls_executed += 1
                try:
                    date_fixtures = await sports_skills_f1_provider.fetch_fixtures(date_str=primary_date)
                    fixtures.extend(date_fixtures)
                    await self.mark_sport_date_fresh("formula_1", primary_date, len(date_fixtures))
                    if call_budget:
                        call_budget.record_call_executed(len(date_fixtures))
                except Exception as e:
                    errors.append(f"F1 Provider error: {str(e)}")
                    if call_budget:
                        call_budget.record_failure()

        return {
            "sport": "formula_1",
            "fixtures": fixtures,
            "fetched_count": len(fixtures),
            "fixtures_updated": len(fixtures),
            "events_checked": 24,
            "events_relevant": len(fixtures),
            "competitions_checked": 1,
            "competitions_relevant": 1 if fixtures else 0,
            "provider_calls": calls_executed,
            "cache_hits": cache_hits,
            "calls_avoided": cache_hits,
            "queried_dates": queried_dates,
            "errors": errors,
        }

    # =========================================================================
    # Universal Orchestrator
    # =========================================================================

    async def execute_refresh(
        self,
        sports: Optional[List[str]] = None,
        date: Optional[str] = None,
        date_range: Optional[List[str]] = None,
        competitions: Optional[List[str]] = None,
        force: bool = False,
    ) -> RefreshRunRecord:
        """
        Universal Targeted Refresh Pipeline across all supported sports.
        """
        start_time = time.perf_counter()
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        today_lagos = get_current_lagos_today()
        errors: List[str] = []
        call_budget = CallBudgetGuard(max_allowed_calls=50)

        # 1. Determine Target Date Range (Africa/Lagos)
        target_dates: List[str] = []
        if date:
            target_dates = [date]
        elif date_range:
            target_dates = [d for d in date_range if d >= today_lagos]
            if not target_dates:
                target_dates = [today_lagos]
        else:
            target_dates = refresh_planner.compute_lagos_date_window(horizon_days=OPERATIONAL_FIXTURE_HORIZON_DAYS)

        # 2. Determine Target Sports Scope
        ALL_SUPPORTED_SPORTS = ["football", "basketball", "baseball", "hockey", "formula_1"]
        if sports:
            req_sports: List[str] = []
            for s in sports:
                norm = s.lower().strip()
                if norm == "all":
                    req_sports = list(ALL_SUPPORTED_SPORTS)
                    break
                elif norm in ("ice_hockey",):
                    req_sports.append("hockey")
                elif norm in ("f1",):
                    req_sports.append("formula_1")
                elif norm in ALL_SUPPORTED_SPORTS:
                    req_sports.append(norm)
            target_sports = list(dict.fromkeys(req_sports)) if req_sports else list(ALL_SUPPORTED_SPORTS)
        else:
            target_sports = list(ALL_SUPPORTED_SPORTS)

        # 3. Call Modular Refresh Handlers only for Target Sports
        raw_fixtures_by_sport: Dict[str, List[Dict[str, Any]]] = {}
        sport_fetched_map: Dict[str, int] = {sp: 0 for sp in ALL_SUPPORTED_SPORTS}
        provider_calls_avoided_total = 0
        competitions_discovered_count = 0
        competitions_queried_count = 0
        per_sport_plans: Dict[str, Any] = {}

        # Football
        if "football" in target_sports:
            fb_res = await self.refresh_football(target_dates, force=force, competitions=competitions, call_budget=call_budget)
            raw_fixtures_by_sport["football"] = fb_res.get("fixtures", [])
            sport_fetched_map["football"] = fb_res.get("fetched_count", 0)
            provider_calls_avoided_total += fb_res.get("calls_avoided", 0)
            competitions_discovered_count += fb_res.get("competitions_checked", 0)
            competitions_queried_count += fb_res.get("provider_calls", 0)
            per_sport_plans["football"] = fb_res
            errors.extend(fb_res.get("errors", []))

        # Basketball
        if "basketball" in target_sports:
            bk_res = await self.refresh_basketball(target_dates, force=force, competitions=competitions, call_budget=call_budget)
            raw_fixtures_by_sport["basketball"] = bk_res.get("fixtures", [])
            sport_fetched_map["basketball"] = bk_res.get("fetched_count", 0)
            provider_calls_avoided_total += bk_res.get("calls_avoided", 0)
            competitions_discovered_count += bk_res.get("competitions_checked", 0)
            competitions_queried_count += bk_res.get("provider_calls", 0)
            per_sport_plans["basketball"] = bk_res
            errors.extend(bk_res.get("errors", []))

        # Baseball
        if "baseball" in target_sports:
            bb_res = await self.refresh_baseball(target_dates, force=force, competitions=competitions, call_budget=call_budget)
            raw_fixtures_by_sport["baseball"] = bb_res.get("fixtures", [])
            sport_fetched_map["baseball"] = bb_res.get("fetched_count", 0)
            provider_calls_avoided_total += bb_res.get("calls_avoided", 0)
            competitions_discovered_count += bb_res.get("competitions_checked", 0)
            competitions_queried_count += bb_res.get("provider_calls", 0)
            per_sport_plans["baseball"] = bb_res
            errors.extend(bb_res.get("errors", []))

        # Hockey
        if "hockey" in target_sports:
            hk_res = await self.refresh_hockey(target_dates, force=force, competitions=competitions, call_budget=call_budget)
            raw_fixtures_by_sport["hockey"] = hk_res.get("fixtures", [])
            sport_fetched_map["hockey"] = hk_res.get("fetched_count", 0)
            provider_calls_avoided_total += hk_res.get("calls_avoided", 0)
            competitions_discovered_count += hk_res.get("competitions_checked", 0)
            competitions_queried_count += hk_res.get("provider_calls", 0)
            per_sport_plans["hockey"] = hk_res
            errors.extend(hk_res.get("errors", []))

        # Formula 1
        if "formula_1" in target_sports:
            f1_res = await self.refresh_f1(target_dates, force=force, call_budget=call_budget)
            raw_fixtures_by_sport["formula_1"] = f1_res.get("fixtures", [])
            sport_fetched_map["formula_1"] = f1_res.get("fetched_count", 0)
            provider_calls_avoided_total += f1_res.get("calls_avoided", 0)
            competitions_discovered_count += f1_res.get("events_checked", 24)
            competitions_queried_count += f1_res.get("provider_calls", 0)
            per_sport_plans["formula_1"] = f1_res
            errors.extend(f1_res.get("errors", []))

        # 4. Deduplicate Fixtures across Providers and Dates
        all_raw_fixtures: List[Dict[str, Any]] = []
        for sp_list in raw_fixtures_by_sport.values():
            all_raw_fixtures.extend(sp_list)

        dedup_map: Dict[str, Dict[str, Any]] = {}
        for fix in all_raw_fixtures:
            key = self._generate_canonical_fixture_key(fix)
            if key not in dedup_map:
                dedup_map[key] = fix
            else:
                existing = dedup_map[key]
                if fix.get("status") == "completed" or (fix.get("status") == "live" and existing.get("status") == "scheduled"):
                    dedup_map[key] = fix

        deduplicated_fixtures = list(dedup_map.values())
        deduplicated_count = len(deduplicated_fixtures)

        # 5. Persist Operational Events in Active Operational Datastore
        for fix in deduplicated_fixtures:
            fix["source"] = fix.get("source", fix.get("provider", "sports-skills"))
            fix["source_event_id"] = fix.get("source_event_id", fix.get("id"))
            fix["provider_event_id"] = fix.get("source_event_id", fix.get("id"))
            fix["canonical_key"] = self._generate_canonical_fixture_key(fix)
            fix["lagos_date"] = get_lagos_date_str(fix.get("kickoffUtc") or fix.get("scheduled_at"))

        from backend.db.failover_manager import failover_manager
        active_db = database_router.get_active_database_name()
        failover_st = failover_manager.failover_state.value
        mongo_persisted_count = 0
        neon_persisted_count = 0

        try:
            persisted_fixtures_count = await database_router.fixtures.upsert_fixtures(deduplicated_fixtures)
            if active_db == "neon":
                neon_persisted_count = persisted_fixtures_count
                mongo_persisted_count = 0
            else:
                mongo_persisted_count = persisted_fixtures_count
                neon_persisted_count = 0
        except Exception as router_upsert_err:
            errors.append(f"Router event upsert notice: {str(router_upsert_err)}")

        # 6. Ensure Historical Parquet Datasets are Registered in DuckDB
        for sport in target_sports:
            try:
                cache_p = os.path.join(settings.parquet_cache_dir, f"{sport}_v1_history.parquet")
                if not os.path.exists(cache_p):
                    local_repo_p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "parquet_cache", f"{sport}_v1_history.parquet")
                    if os.path.exists(local_repo_p):
                        cache_p = local_repo_p
                    else:
                        try:
                            cache_p = r2_manager.get_local_parquet_path(sport, version="v1")
                        except Exception:
                            pass

                if os.path.exists(cache_p):
                    duckdb_engine.register_parquet_view(f"{sport}_matches", cache_p)
                    if sport == "hockey":
                        duckdb_engine.register_parquet_view("ice_hockey_matches", cache_p)
                    elif sport == "formula_1":
                        duckdb_engine.register_parquet_view("f1_results", cache_p)
                        duckdb_engine.register_parquet_view("f1_matches", cache_p)
            except Exception as e:
                errors.append(f"DuckDB registration for {sport}: {str(e)}")

        # 7. Targeted Prediction Pipeline
        active_model = model_service.get_active_model()
        published_feed: List[Dict[str, Any]] = []
        pipeline_result: Dict[str, Any] = {}
        published_predictions_count = 0

        eligible_target_fixtures = [
            f for f in deduplicated_fixtures
            if get_lagos_date_str(f.get("kickoffUtc") or f.get("scheduled_at")) >= today_lagos
        ]
        if not eligible_target_fixtures and deduplicated_fixtures:
            eligible_target_fixtures = deduplicated_fixtures

        try:
            pipeline_result = execute_prediction_pipeline(
                active_model=active_model,
                is_subscriber_feed=True,
                custom_fixtures=eligible_target_fixtures,
            )
            published_feed = pipeline_result.get("publishedFeed", [])[:20]
            total_candidates = len(eligible_target_fixtures)
            pipe_diag_init = pipeline_result.get("diagnostics", {})
            validated_count = pipe_diag_init.get("validatedFixtures", len([r for r in pipeline_result.get("allResults", []) if r.get("validationStatus") == "validated"]))
            published_count = len(published_feed)
            print(f"[SyncService] Prediction pipeline counters -> totalCandidates: {total_candidates}, validatedCount: {validated_count}, publishedCount: {published_count}")

            # Attach unique prediction_run_id and required governance attributes
            from backend.services.sync_publication import attach_prediction_governance_metadata
            attach_prediction_governance_metadata(
                predictions=published_feed,
                active_model=active_model,
                feature_version="2.0.0",
                run_id=run_id,
            )
        except Exception as e:
            errors.append(f"Prediction Pipeline Error: {str(e)}")
            total_candidates = len(eligible_target_fixtures)
            validated_count = 0
            published_count = 0

        # 8. Persist Validated Predictions to Active Operational Datastore FIRST
        db_persisted_count = 0
        db_persist_success = False
        predictions_persisted_mongo = 0
        predictions_persisted_neon = 0
        try:
            if published_feed:
                db_persisted_count = await database_router.predictions.save_predictions(published_feed)
                if active_db == "neon":
                    predictions_persisted_neon = db_persisted_count
                    predictions_persisted_mongo = 0
                else:
                    predictions_persisted_mongo = db_persisted_count
                    predictions_persisted_neon = 0
                db_persist_success = True
            else:
                db_persist_success = True
        except Exception as router_pred_err:
            db_persist_success = False
            errors.append(f"Router prediction persist error: {str(router_pred_err)}")

        # 9. Only after successful persistence should the system publish them to Redis
        redis_publish_failed = False
        redis_feed_state = "UNAVAILABLE"

        if db_persist_success and published_feed:
            # Operational database holds authoritative predictions
            published_predictions_count = len(published_feed)
            try:
                feed_by_date: Dict[str, List[Dict[str, Any]]] = {}
                for item in published_feed:
                    d_str = get_lagos_date_str(item.get("kickoffUtc") or item.get("scheduled_at"))
                    if d_str:
                        if d_str not in feed_by_date:
                            feed_by_date[d_str] = []
                        feed_by_date[d_str].append(item)

                write_states: List[str] = []
                for date_str, date_items in feed_by_date.items():
                    w_res = await redis_client.set_json(f"predictpro:feed:{date_str}", date_items, ex_seconds=86400)
                    if not w_res:
                        redis_publish_failed = True
                        st = getattr(w_res, "state", None) or "DEGRADED"
                        write_states.append(st)
                        errors.append(f"Redis write degraded for date {date_str} [{st}]: {getattr(w_res, 'error', None) or getattr(w_res, 'details', None)}")
                    else:
                        write_states.append(getattr(w_res, "state", "REMOTE_SUCCESS"))

                w_latest = await redis_client.set_json("predictpro:feed:latest", published_feed, ex_seconds=86400)
                if not w_latest:
                    redis_publish_failed = True
                    st = getattr(w_latest, "state", None) or "DEGRADED"
                    write_states.append(st)
                    errors.append(f"Redis write degraded for latest feed [{st}]: {getattr(w_latest, 'error', None) or getattr(w_latest, 'details', None)}")
                else:
                    write_states.append(getattr(w_latest, "state", "REMOTE_SUCCESS"))

                if any(s == "UNAVAILABLE" for s in write_states):
                    redis_feed_state = "UNAVAILABLE"
                elif redis_publish_failed or any(s in ("DEGRADED", "REMOTE_FAILURE") for s in write_states):
                    redis_feed_state = "DEGRADED"
                else:
                    redis_feed_state = "REMOTE_SUCCESS"

            except Exception as e:
                redis_publish_failed = True
                redis_feed_state = getattr(redis_client, "last_write_state", "UNAVAILABLE") or "UNAVAILABLE"
                errors.append(f"Redis publish notice: {str(e)}")
        elif not db_persist_success:
            # If operational DB persistence failed, do NOT publish unpersisted predictions to Redis
            published_predictions_count = 0
            redis_publish_failed = True
            redis_feed_state = "DEGRADED"
            errors.append("Redis publication skipped because operational database persistence failed.")
        else:
            # published_feed was empty
            published_predictions_count = 0
            try:
                w_latest = await redis_client.set_json("predictpro:feed:latest", [], ex_seconds=86400)
                if w_latest:
                    redis_feed_state = getattr(w_latest, "state", "REMOTE_SUCCESS")
                else:
                    redis_publish_failed = True
                    redis_feed_state = getattr(w_latest, "state", "DEGRADED")
                    errors.append(f"Redis write failed for latest feed [{redis_feed_state}]: {getattr(w_latest, 'error', None) or getattr(w_latest, 'details', None)}")
            except Exception as e:
                redis_publish_failed = True
                redis_feed_state = getattr(redis_client, "last_write_state", "UNAVAILABLE") or "UNAVAILABLE"
                errors.append(f"Redis publish error: {str(e)}")

        # Trigger controlled incremental replication from MongoDB to Neon PostgreSQL standby
        repl_summary = None
        try:
            repl_summary = await replication_manager.execute_incremental_replication(trigger_source="refresh_pipeline")
            if active_db == "mongodb" and repl_summary:
                neon_replicated_fixtures = repl_summary.get("fixtures_replicated", repl_summary.get("total_records_written", 0))
                neon_persisted_count = max(neon_persisted_count, neon_replicated_fixtures)
        except Exception as repl_err:
            print(f"[SyncService] Controlled replication notice: {repl_err}")
            repl_summary = {"status": "skipped", "error": str(repl_err)}

        from backend.db.neon_budget_guard import neon_budget_guard
        estimated_refresh_bytes = (db_persisted_count * 1500) + (neon_persisted_count * 1200)
        neon_budget_guard.record_refresh_growth(estimated_refresh_bytes)

        # 10. Diagnostics Breakdown
        fb_rows = duckdb_engine.get_sport_history_count("football")
        bk_rows = duckdb_engine.get_sport_history_count("basketball")
        bb_rows = duckdb_engine.get_sport_history_count("baseball")
        hk_rows = duckdb_engine.get_sport_history_count("hockey")
        f1_rows = duckdb_engine.get_sport_history_count("f1_results") or duckdb_engine.get_sport_history_count("formula_1")

        all_results = pipeline_result.get("allResults", [])
        pipe_diag = pipeline_result.get("diagnostics", {})

        total_fetched = sum(sport_fetched_map.values())
        fixtures_eligible = pipe_diag.get("fixturesEligible", 0)
        fixtures_model_executed = pipe_diag.get("fixturesModelExecuted", 0)
        ensemble_successful = pipe_diag.get("ensembleSuccessful", 0)
        validated_fixtures = pipe_diag.get("validatedFixtures", 0)

        if total_fetched == 0 and deduplicated_count == 0:
            first_failing_stage = "FETCH"
        elif fixtures_eligible == 0 and len(eligible_target_fixtures) == 0:
            first_failing_stage = "ELIGIBILITY"
        elif fixtures_model_executed == 0 and fixtures_eligible > 0:
            first_failing_stage = "HISTORICAL_DATA"
        elif ensemble_successful == 0 and fixtures_model_executed > 0:
            first_failing_stage = "MODEL_EXECUTION"
        elif validated_fixtures == 0 and ensemble_successful > 0:
            first_failing_stage = "MARKET_VALIDATION"
        elif published_predictions_count == 0 and validated_fixtures > 0:
            first_failing_stage = "RANKING_PUBLICATION"
        else:
            first_failing_stage = None

        sport_history_map = {
            "football": fb_rows,
            "basketball": bk_rows,
            "baseball": bb_rows,
            "hockey": hk_rows,
            "formula_1": f1_rows,
        }

        usable_teams_per_sport: Dict[str, set] = {sp: set() for sp in ALL_SUPPORTED_SPORTS}
        for r in all_results:
            sp_key = r.get("sport", "football")
            if sp_key in ("hockey", "ice_hockey"):
                sp_key = "hockey"
            elif sp_key in ("f1", "formula_1"):
                sp_key = "formula_1"
            h_team = r.get("homeTeam", "")
            a_team = r.get("awayTeam", "")
            feats = r.get("features", {})
            h_count = feats.get("homeMatchesCount", 0)
            a_count = feats.get("awayMatchesCount", 0)
            if h_team and h_count >= 5:
                usable_teams_per_sport.setdefault(sp_key, set()).add(h_team)
            if a_team and a_count >= 5:
                usable_teams_per_sport.setdefault(sp_key, set()).add(a_team)

        sports_diagnostics: Dict[str, Any] = {}
        for sp in ALL_SUPPORTED_SPORTS:
            sp_results = [r for r in all_results if r.get("sport") == sp or (sp == "hockey" and r.get("sport") in ("hockey", "ice_hockey"))]
            sp_fetched = sport_fetched_map.get(sp, 0)
            sp_hist = sport_history_map.get(sp, 0)
            sp_eligible = len([r for r in sp_results if r.get("stopReason") not in ("REJECTED_INVALID", "REJECTED_PAST", "REJECTED_COMPLETED", "invalid_fixture")])
            sp_validated = len([r for r in sp_results if r.get("validationStatus") == "validated"])
            sp_published = len([p for p in published_feed if p.get("sport") == sp or (sp == "hockey" and p.get("sport") in ("hockey", "ice_hockey"))])
            sp_markets = sum(len(r.get("markets", [])) for r in sp_results)
            sp_abstentions = len([r for r in sp_results if r.get("stopReason") in ("INSUFFICIENT_HISTORY", "ABSTAINED", "insufficient_history", "abstained") or r.get("validationStatus") == "abstained"])

            sp_stage = None
            if sp_fetched > 0 or len(sp_results) > 0:
                if sp_hist == 0:
                    sp_stage = "HISTORICAL_DATA"
                elif sp_eligible == 0:
                    sp_stage = "ELIGIBILITY"
                elif sp_validated == 0:
                    sp_stage = "MARKET_VALIDATION"
                elif sp_published == 0 and len(published_feed) == 0:
                    sp_stage = "RANKING_PUBLICATION"

            sp_errors = [e for e in errors if sp in e.lower()]
            prov_status = "PASS" if (sp_fetched > 0 or len(sp_results) > 0) and not sp_errors else ("NO_GAMES_SCHEDULED" if not sp_errors else "PROVIDER_ERROR")

            plan_info = per_sport_plans.get(sp, {})

            sports_diagnostics[sp] = {
                "sport": sp,
                "providerStatus": prov_status,
                "competitions_checked": plan_info.get("competitions_checked", plan_info.get("events_checked", 1)),
                "competitions_relevant": plan_info.get("competitions_relevant", plan_info.get("events_relevant", 1 if sp_fetched > 0 else 0)),
                "provider_calls": plan_info.get("provider_calls", 0),
                "cache_hits": plan_info.get("cache_hits", 0),
                "fixtures_updated": plan_info.get("fixtures_updated", sp_fetched),
                "fixturesFetched": sp_fetched,
                "historicalRows": sp_hist,
                "usableTeamsCount": len(usable_teams_per_sport.get(sp, set())),
                "eligibleFixtures": sp_eligible,
                "modelCalculationsExecuted": sp_eligible if sp_hist > 0 else 0,
                "eloSuccessful": sp_eligible if sp_hist > 0 else 0,
                "poissonSuccessful": sp_eligible if sp_hist > 0 else 0,
                "marketsGenerated": sp_markets,
                "validatedFixtures": sp_validated,
                "predictionsPublished": sp_published,
                "abstentionsCount": sp_abstentions,
                "firstFailingStage": sp_stage,
                "errors": sp_errors,
            }

        is_prod = active_model in {"ELO", "POISSON", "ELO + POISSON", "F1RatingEngine + F1ProbabilityEngine", "F1"}

        home_team_counts: Dict[str, int] = {}
        away_team_counts: Dict[str, int] = {}
        for r in all_results:
            h_team = r.get("homeTeam", "")
            a_team = r.get("awayTeam", "")
            feats = r.get("features", {})
            h_count = feats.get("homeMatchesCount", 0)
            a_count = feats.get("awayMatchesCount", 0)
            if h_team:
                home_team_counts[h_team] = h_count
            if a_team:
                away_team_counts[a_team] = a_count

        diagnostics: Dict[str, Any] = {
            # PROVIDER DIAGNOSTICS
            "providerFixturesReturned": total_fetched,
            "fixturesNormalized": len(all_raw_fixtures),
            "fixturesDeduplicated": deduplicated_count,
            "requestedDateRange": target_dates,
            "requested_date_range": target_dates,

            # DATABASE DIAGNOSTICS
            "fixturesPersisted": mongo_persisted_count if active_db == "mongodb" else neon_persisted_count,
            "fixturesPersistedMongo": mongo_persisted_count,
            "fixturesPersistedNeon": neon_persisted_count,
            "predictionsPersisted": db_persisted_count,
            "predictionsPersistedMongo": predictions_persisted_mongo,
            "predictionsPersistedNeon": predictions_persisted_neon,
            "databasePublicationStatus": "published" if db_persist_success else "failed",
            "database_publication_status": "published" if db_persist_success else "failed",
            "activeDatabase": active_db,
            "mongoHealth": mongo_manager.health_check() if hasattr(mongo_manager, "health_check") else {"status": "connected"},
            "neonHealth": neon_adapter.health_check() if hasattr(neon_adapter, "health_check") else {"status": "configured_standby"},
            "failoverState": failover_st,
            "replicationStatus": "replicated" if active_db == "mongodb" and repl_summary else "active_primary",
            "neonReplication": repl_summary,
            "neon_replication": repl_summary,
            "redisCacheStatus": normalize_redis_feed_state(redis_feed_state),
            "redisFeedState": normalize_redis_feed_state(redis_feed_state),
            "redis_feed_state": normalize_redis_feed_state(redis_feed_state),

            # PUBLICATION DIAGNOSTICS
            "totalCandidates": total_candidates,
            "total_candidates": total_candidates,
            "validatedCount": validated_count,
            "validated_count": validated_count,
            "publishedCount": published_predictions_count,
            "published_count": published_predictions_count,
            "rejectionReason": f"No predictions published. Failing stage: {first_failing_stage or 'ELIGIBILITY'}" if published_predictions_count == 0 else None,
            "rejectionStage": first_failing_stage if published_predictions_count == 0 else None,
            "firstFailingStage": first_failing_stage,

            # PIPELINE DIAGNOSTICS
            "fixturesEligibleForPrediction": fixtures_eligible,
            "fixturesEligible": fixtures_eligible,
            "fixturesModelExecuted": fixtures_model_executed,
            "ensembleSuccessful": ensemble_successful,
            "calibrated": pipe_diag.get("calibrated", 0),
            "confidencePassed": pipe_diag.get("confidencePassed", 0),
            "abstained": pipe_diag.get("abstained", 0),
            "validationPassed": pipe_diag.get("validationPassed", 0),
            "validatedFixtures": validated_fixtures,
            "publishedPredictions": published_predictions_count,
            "predictionsPublished": published_predictions_count,
            "candidateStopReasons": pipe_diag.get("candidateStopReasons", {}),
            "stopReasonsBreakdown": pipe_diag.get("candidateStopReasons", {}),

            # SUMMARY & HISTORY METRICS
            "refresh_type": "force" if force else "normal",
            "requested_sports": target_sports,
            "competitions_discovered_count": competitions_discovered_count,
            "competitions_evaluated_count": len(target_sports),
            "competitions_queried_count": competitions_queried_count,
            "fixtures_fetched_by_sport": sport_fetched_map,
            "fixtures_deduplicated_count": deduplicated_count,
            "fixtures_eligible_count": fixtures_eligible,
            "predictions_calculated_count": ensemble_successful,
            "predictions_published_count": published_predictions_count,
            "provider_calls_avoided_count": provider_calls_avoided_total,
            "call_budget": call_budget.to_dict(),
            "sports": sports_diagnostics,
            "football history rows": fb_rows,
            "basketball history rows": bk_rows,
            "baseball history rows": bb_rows,
            "hockey history rows": hk_rows,
            "formula_1 history rows": f1_rows,
            "home team history count": home_team_counts,
            "away team history count": away_team_counts,
            "fixtures rejected for insufficient history": pipe_diag.get("fixturesInsufficientHistory", 0),
            "activeModel": active_model,
            "isProductionModel": is_prod,
            "firstFailingStage": first_failing_stage,
            "fixturesFetchedFootball": sport_fetched_map.get("football", 0),
            "fixturesFetchedBasketball": sport_fetched_map.get("basketball", 0),
            "fixturesFetchedBaseball": sport_fetched_map.get("baseball", 0),
            "fixturesFetchedHockey": sport_fetched_map.get("hockey", 0),
            "fixturesFetchedF1": sport_fetched_map.get("formula_1", 0),
            "fixturesRejectedInvalid": pipe_diag.get("fixturesRejectedInvalid", 0),
            "fixturesRejectedPast": pipe_diag.get("fixturesRejectedPast", 0),
            "fixturesRejectedCompleted": pipe_diag.get("fixturesRejectedCompleted", 0),
            "fixturesInsufficientHistory": pipe_diag.get("fixturesInsufficientHistory", 0),
            "eloSuccessful": pipe_diag.get("eloSuccessful", 0),
            "poissonSuccessful": pipe_diag.get("poissonSuccessful", 0),
            "marketsGenerated": pipe_diag.get("marketsGenerated", 0),
            "marketsRejected": pipe_diag.get("marketsRejected", 0),
            "footballHistoryRows": fb_rows,
            "basketballHistoryRows": bk_rows,
            "baseballHistoryRows": bb_rows,
            "hockeyHistoryRows": hk_rows,
            "f1HistoryRows": f1_rows,
            "homeTeamHistoryCount": home_team_counts,
            "awayTeamHistoryCount": away_team_counts,
            "fixturesRejected": (pipe_diag.get("fixturesRejectedInvalid", 0) + pipe_diag.get("fixturesRejectedPast", 0) + pipe_diag.get("fixturesRejectedCompleted", 0)) or (max(0, deduplicated_count - fixtures_eligible)),
        }

        # Apply operational retention so MongoDB and Neon do not grow indefinitely
        # Does NOT delete live fixtures or recently completed fixtures needed for score reconciliation
        try:
            await database_router.fixtures.prune_expired_fixtures(retention_days=14)
            if hasattr(neon_adapter, "is_configured") and neon_adapter.is_configured():
                from backend.db.neon_budget_guard import neon_budget_guard
                executor = getattr(neon_adapter, "executor", getattr(neon_adapter, "_query_executor", None))
                if executor:
                    await neon_budget_guard.prune_expired_failover_data(executor)
        except Exception as prune_err:
            print(f"[SyncService] Operational retention prune notice: {prune_err}")

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        if not db_persist_success:
            status_str = "failed"
            fail_reason = "Refresh failed: Operational database persistence failed."
            if fail_reason not in errors:
                errors.append(fail_reason)
        elif (total_fetched > 0 or deduplicated_count > 0) and published_predictions_count == 0 and fixtures_eligible > 0:
            status_str = "failed"
            fail_reason = f"Refresh failed: {deduplicated_count} fixtures processed but 0 predictions published. Failing stage: {first_failing_stage or 'MARKET_VALIDATION'}"
            if fail_reason not in errors:
                errors.append(fail_reason)
        else:
            if redis_publish_failed:
                diagnostics["redisPublicationDegraded"] = True
                status_str = "completed" if db_persist_success else "partial"
            else:
                status_str = "completed" if not errors else ("partial" if (deduplicated_count > 0 or total_fetched > 0) else "failed")

        record = RefreshRunRecord(
            id=run_id,
            timestamp=now_iso,
            status=status_str,
            matches_synced=deduplicated_count,
            predictions_published=published_predictions_count,
            duration_ms=duration_ms,
            errors=errors,
            diagnostics=diagnostics,
            redisFeedState=normalize_redis_feed_state(redis_feed_state),
            totalCandidates=total_candidates,
            validatedCount=validated_count,
            publishedCount=published_predictions_count,
        )

        try:
            await database_router.refresh_state.save_run(record.model_dump())
        except Exception as router_run_err:
            print(f"[SyncService] Source sync run audit notice: {router_run_err}")

        return record


    async def execute_sync_feed(self) -> Dict[str, Any]:
        """
        Dedicated 'Sync Feed' backend workflow.
        Queries SportsSkills sources for tracked PredictPro fixtures, updates MongoDB with scores/statuses,
        evaluates completed predictions, and calculates model-performance metrics.
        Never overwrites the original prediction metadata.
        """
        import re
        try:
            import httpx
        except ImportError:
            httpx = None
        import math
        start_time = time.perf_counter()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        errors: List[str] = []
        live_updated_count = 0
        completed_updated_count = 0
        evaluated_count = 0
        hit_count = 0
        miss_count = 0
        
        fb_fetched = 0
        bk_fetched = 0
        bb_fetched = 0
        hk_fetched = 0
        f1_fetched = 0
        
        # 1. Fetch from SportsSkills Daily Feeds
        all_prov_fixtures: List[Dict[str, Any]] = []
        
        # Football
        try:
            fb = await football_adapter.fetch_fixtures()
            fb_fetched = len(fb)
            all_prov_fixtures.extend(fb)
        except Exception as e:
            errors.append(f"SportsSkills Football sync error: {str(e)}")
            
        # Basketball
        try:
            bk = await basketball_adapter.fetch_fixtures()
            bk_fetched = len(bk)
            all_prov_fixtures.extend(bk)
        except Exception as e:
            errors.append(f"SportsSkills Basketball sync error: {str(e)}")
            
        # Baseball
        try:
            bb = await baseball_adapter.fetch_fixtures()
            bb_fetched = len(bb)
            all_prov_fixtures.extend(bb)
        except Exception as e:
            errors.append(f"SportsSkills Baseball sync error: {str(e)}")
            
        # Hockey
        try:
            hk = await sports_skills_hockey_provider.fetch_fixtures()
            hk_fetched = len(hk)
            all_prov_fixtures.extend(hk)
        except Exception as e:
            errors.append(f"SportsSkills Hockey sync error: {str(e)}")
            
        # F1
        try:
            f1 = await sports_skills_f1_provider.fetch_fixtures()
            f1_fetched = len(f1)
            all_prov_fixtures.extend(f1)
        except Exception as e:
            errors.append(f"SportsSkills F1 sync error: {str(e)}")
            
        # 2. Get tracked PredictPro fixtures from operational_events using targeted query
        prov_ids: Set[str] = set()
        prov_canonical_keys: Set[str] = set()
        for p in all_prov_fixtures:
            p_id = p.get("id")
            s_id = p.get("source_event_id")
            if p_id:
                prov_ids.add(str(p_id))
            if s_id:
                prov_ids.add(str(s_id))
            c_k = self._generate_canonical_fixture_key(p)
            if c_k:
                prov_canonical_keys.add(c_k)

        try:
            recent_completed = await database_router.fixtures.get_recent_completed(hours=72)
            live_fixtures = await database_router.fixtures.get_live_fixtures()
            by_ids_fixtures = await database_router.fixtures.get_by_ids(list(prov_ids)) if prov_ids else []
            seen_op_ids = set()
            operational_events = []
            for op in (recent_completed + live_fixtures + by_ids_fixtures):
                o_id = op.get("id")
                if o_id and o_id not in seen_op_ids:
                    seen_op_ids.add(o_id)
                    operational_events.append(op)
        except Exception as e:
            errors.append(f"Error loading targeted operational events: {str(e)}")
            operational_events = []

        # Index the operational events for O(1) matching
        from backend.utils.text_normalize import normalize_team_name

        by_id: Dict[str, Dict[str, Any]] = {}
        by_source_id: Dict[tuple, Dict[str, Any]] = {}
        by_canonical_key: Dict[str, Dict[str, Any]] = {}
        by_sport_teams_date: Dict[tuple, Dict[str, Any]] = {}

        for op in operational_events:
            o_id = op.get("id")
            if o_id:
                by_id[o_id] = op
            s_id = op.get("source_event_id") or op.get("provider_event_id")
            sp = op.get("sport")
            if s_id and sp:
                by_source_id[(sp, str(s_id))] = op
            c_k = op.get("canonical_key") or self._generate_canonical_fixture_key(op)
            if c_k:
                by_canonical_key[c_k] = op

            h_key = normalize_team_name(op.get("homeTeam", ""))
            a_key = normalize_team_name(op.get("awayTeam", ""))
            dt = (op.get("kickoffUtc") or op.get("scheduled_at") or "")[:10]
            if sp and h_key and a_key and dt:
                by_sport_teams_date[(sp, h_key, a_key, dt)] = op

        def find_matching_operational(prov_ev):
            p_id = prov_ev.get("id")
            if p_id and p_id in by_id:
                return by_id[p_id]

            p_src = prov_ev.get("source_event_id") or prov_ev.get("id")
            sp = prov_ev.get("sport")
            if p_src and sp and (sp, str(p_src)) in by_source_id:
                return by_source_id[(sp, str(p_src))]

            c_key = self._generate_canonical_fixture_key(prov_ev)
            if c_key and c_key in by_canonical_key:
                return by_canonical_key[c_key]

            h_k = normalize_team_name(prov_ev.get("homeTeam", ""))
            a_k = normalize_team_name(prov_ev.get("awayTeam", ""))
            dt = (prov_ev.get("kickoffUtc") or prov_ev.get("scheduled_at") or "")[:10]
            if (sp, h_k, a_k, dt) in by_sport_teams_date:
                return by_sport_teams_date[(sp, h_k, a_k, dt)]

            return None
            
        # 3. Match and Update Operational Events
        matched_completed_fixtures: List[Dict[str, Any]] = []
        
        for prov_ev in all_prov_fixtures:
            op_event = find_matching_operational(prov_ev)
            if op_event:
                op_id = op_event["id"]
                new_status = prov_ev.get("status", "scheduled")
                home_score = prov_ev.get("currentScore", {}).get("home", 0)
                away_score = prov_ev.get("currentScore", {}).get("away", 0)
                display = prov_ev.get("currentScore", {}).get("display") or f"{home_score} - {away_score}"
                
                update_fields = {
                    "status": new_status,
                    "currentScore": {
                        "home": home_score,
                        "away": away_score,
                        "display": display,
                    },
                    "home_score": home_score,
                    "away_score": away_score,
                    "period/clock": prov_ev.get("period_clock") or prov_ev.get("currentScore", {}).get("period_clock") or "N/A",
                    "started_at": prov_ev.get("kickoffUtc") or prov_ev.get("scheduled_at"),
                    "ended_at": now_iso if new_status == "completed" else None,
                    "last_score_sync_at": now_iso,
                    "provider_event_id": prov_ev.get("source_event_id") or prov_ev.get("id"),
                }
                
                try:
                    await database_router.fixtures.upsert_fixtures([{"id": op_id, **update_fields}])
                    
                    if new_status == "live":
                        live_updated_count += 1
                    elif new_status == "completed":
                        completed_updated_count += 1
                        matched_completed_fixtures.append((op_id, prov_ev))
                        # Incrementally update team feature snapshots
                        try:
                            sp = prov_ev.get("sport", "football")
                            h_team = prov_ev.get("homeTeam", "")
                            a_team = prov_ev.get("awayTeam", "")
                            m_date = (prov_ev.get("kickoffUtc") or prov_ev.get("scheduled_at") or now_iso)[:10]
                            if h_team and a_team:
                                await feature_snapshot_service.update_after_match(
                                    sport=sp,
                                    home_team=h_team,
                                    away_team=a_team,
                                    home_score=home_score,
                                    away_score=away_score,
                                    match_date=m_date,
                                )
                        except Exception as snap_err:
                            print(f"[SyncService] Snapshot update notice for completed match {op_id}: {snap_err}")
                except Exception as e:
                    errors.append(f"Error updating operational event {op_id}: {str(e)}")
                    
        # 4. Evaluate Predictions for Completed Matches
        # F1 Jolpica results fetcher helper
        async def fetch_f1_race_results(year_val: int, round_val: int) -> List[Dict[str, Any]]:
            url_str = f"https://api.jolpi.ca/ergast/f1/{year_val}/{round_val}/results.json"
            headers_dict = {"User-Agent": "PredictPro/1.0 (contact: harristotle84@gmail.com)"}
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp_obj = await client.get(url_str, headers=headers_dict)
                    if resp_obj.status_code == 200:
                        data_json = resp_obj.json()
                        races_list = data_json.get("MRData", {}).get("RaceTable", {}).get("Races", [])
                        if races_list:
                            return races_list[0].get("Results", [])
            except Exception as f1_err:
                print(f"[SyncFeed] Jolpica results fetch error: {f1_err}")
            return []

        async def resolve_outcome(pred_doc: Dict[str, Any], market_item: Dict[str, Any], ev_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            sport_val = pred_doc.get("sport", "").lower().strip()
            market_name_val = market_item.get("marketName", "")
            sel_val = market_item.get("selection", "")
            
            # F1 Outcome Resolution
            if sport_val in ("formula_1", "f1"):
                fix_id = pred_doc.get("fixture_id") or pred_doc.get("id") or ""
                f1_parts = re.findall(r"\d+", fix_id)
                if len(f1_parts) >= 2:
                    y_val = int(f1_parts[0])
                    rnd_val = int(f1_parts[1])
                    f1_res = await fetch_f1_race_results(y_val, rnd_val)
                    if f1_res:
                        # Find predicted driver
                        matched_d = None
                        for d_item in f1_res:
                            fam = d_item.get('Driver', {}).get('familyName', '')
                            giv = d_item.get('Driver', {}).get('givenName', '')
                            full = f"{giv} {fam}"
                            if fam.lower() in sel_val.lower() or full.lower() in sel_val.lower():
                                matched_d = d_item
                                break
                        
                        if "Winner" in market_name_val or "Win" in market_name_val:
                            win_d = f1_res[0]
                            w_name = f"{win_d.get('Driver', {}).get('givenName', '')} {win_d.get('Driver', {}).get('familyName', '')}"
                            w_fam = win_d.get('Driver', {}).get('familyName', '')
                            is_hit = w_fam.lower() in sel_val.lower() or w_name.lower() in sel_val.lower()
                            return {"actual_outcome": f"{w_name} Winner", "is_hit": is_hit}
                        elif "Podium" in market_name_val:
                            if matched_d:
                                p_pos = int(matched_d.get("position", 20))
                                return {"actual_outcome": f"Finished P{p_pos}", "is_hit": p_pos <= 3}
                        elif "Top 10" in market_name_val:
                            if matched_d:
                                p_pos = int(matched_d.get("position", 20))
                                return {"actual_outcome": f"Finished P{p_pos}", "is_hit": p_pos <= 10}
                        elif "Fastest Lap" in market_name_val:
                            fast_d = "None"
                            is_fast_hit = False
                            for d_item in f1_res:
                                if d_item.get("FastestLap", {}).get("rank") == "1":
                                    fast_d = f"{d_item.get('Driver', {}).get('givenName', '')} {d_item.get('Driver', {}).get('familyName', '')}"
                                    is_fast_hit = d_item.get('Driver', {}).get('familyName', '').lower() in sel_val.lower() or fast_d.lower() in sel_val.lower()
                                    break
                            return {"actual_outcome": f"{fast_d} Fastest Lap", "is_hit": is_fast_hit}
                return None
                
            # Traditional Sports Outcome Resolution
            h_sc = ev_data.get("currentScore", {}).get("home")
            a_sc = ev_data.get("currentScore", {}).get("away")
            if h_sc is None or a_sc is None:
                h_sc = ev_data.get("home_score")
                a_sc = ev_data.get("away_score")
            if h_sc is None or a_sc is None:
                return None
                
            h_name = pred_doc.get("homeTeam") or pred_doc.get("home_team") or "Home Team"
            a_name = pred_doc.get("awayTeam") or pred_doc.get("away_team") or "Away Team"
            
            # 1X2 / Moneyline
            if any(term in market_name_val.lower() for term in ["1x2", "moneyline", "winner", "win / draw / loss"]):
                if h_sc > a_sc:
                    act = f"{h_name} Win"
                    hit = (h_name.lower() in sel_val.lower()) or ("home" in sel_val.lower() and "win" in sel_val.lower())
                elif a_sc > h_sc:
                    act = f"{a_name} Win"
                    hit = (a_name.lower() in sel_val.lower()) or ("away" in sel_val.lower() and "win" in sel_val.lower())
                else:
                    act = "Draw"
                    hit = "draw" in sel_val.lower()
                return {"actual_outcome": act, "is_hit": hit}
                
            # Double Chance
            elif "double chance" in market_name_val.lower():
                sel_clean = sel_val.lower()
                is_1x = "1x" in sel_clean or ("draw" in sel_clean and h_name.lower() in sel_clean)
                is_x2 = "x2" in sel_clean or ("draw" in sel_clean and a_name.lower() in sel_clean)
                is_12 = "12" in sel_clean or ("or" in sel_clean and h_name.lower() in sel_clean and a_name.lower() in sel_clean and "draw" not in sel_clean)

                if h_sc > a_sc:
                    act = f"{h_name} Win (1X/12)"
                    hit = is_1x or is_12
                elif a_sc > h_sc:
                    act = f"{a_name} Win (X2/12)"
                    hit = is_x2 or is_12
                else:
                    act = "Draw (1X/X2)"
                    # 12 is Home or Away Win - it LOOSES on Draw!
                    hit = is_1x or is_x2
                return {"actual_outcome": act, "is_hit": hit}
                
            # Over/Under
            elif "over" in market_name_val.lower() or "under" in market_name_val.lower():
                bench = 2.5
                f_list = re.findall(r"\d+\.\d+", market_name_val + " " + sel_val)
                if not f_list:
                    i_list = re.findall(r"\d+", market_name_val + " " + sel_val)
                    if i_list:
                        bench = float(i_list[0])
                else:
                    bench = float(f_list[0])
                    
                tot = h_sc + a_sc
                if tot > bench:
                    act = f"Over {bench} Goals" if sport_val == "football" else f"Over {bench} Points"
                    hit = "over" in sel_val.lower()
                else:
                    act = f"Under {bench} Goals" if sport_val == "football" else f"Under {bench} Points"
                    hit = "under" in sel_val.lower()
                return {"actual_outcome": act, "is_hit": hit}
                
            # BTTS
            elif "btts" in market_name_val.lower() or "both teams" in market_name_val.lower():
                is_btts = h_sc > 0 and a_sc > 0
                act = "Yes" if is_btts else "No"
                hit = "yes" in sel_val.lower() if is_btts else "no" in sel_val.lower()
                return {"actual_outcome": act, "is_hit": hit}
                
            # Puck Line / Run Line / Spread
            elif any(term in market_name_val.lower() for term in ["puck line", "run line", "spread"]):
                f_list = re.findall(r"[-+]?\d+\.?\d*", sel_val)
                spread_val = float(f_list[0]) if f_list else 1.5
                diff = h_sc - a_sc
                if h_name.lower() in sel_val.lower():
                    hit = (diff + spread_val) > 0
                    act = f"{h_name} Diff {diff:+d}"
                else:
                    hit = (-diff + spread_val) > 0
                    act = f"{a_name} Diff {-diff:+d}"
                return {"actual_outcome": act, "is_hit": hit}
                
            return None

        for op_id, prov_ev in matched_completed_fixtures:
            # Query predictions for this completed fixture
            try:
                predictions_cursor = await database_router.predictions.get_by_fixture_id(op_id)
                for pred_doc in predictions_cursor:
                    validated_markets = pred_doc.get("validatedMarkets") or []
                    if not validated_markets:
                        validated_markets = [{
                            "id": pred_doc.get("id"),
                            "marketName": pred_doc.get("market"),
                            "selection": pred_doc.get("selection"),
                            "probabilityPercentage": pred_doc.get("percentage"),
                        }]
                        
                    for market_item in validated_markets:
                        m_id = market_item.get("id") or f"{pred_doc.get('id')}-m1"
                        m_name = market_item.get("marketName")
                        selection = market_item.get("selection")
                        percentage = market_item.get("probabilityPercentage", 50.0)
                        prob = percentage / 100.0

                        # Duplicate result protection: do not re-evaluate already finalized prediction results
                        existing_eval = await database_router.prediction_results.get_by_id(m_id)
                        if existing_eval and existing_eval.get("hit_or_miss") in {"hit", "miss"}:
                            continue

                        resolved_res = await resolve_outcome(pred_doc, market_item, prov_ev)
                        if resolved_res:
                            hit_or_miss = "hit" if resolved_res["is_hit"] else "miss"
                            hit_numeric = 1.0 if hit_or_miss == "hit" else 0.0
                            brier_ind = round((prob - hit_numeric) ** 2, 4)
                            p_clamped = min(max(prob, 1e-15), 1.0 - 1e-15)
                            log_loss_ind = round(-math.log(p_clamped if hit_or_miss == "hit" else (1.0 - p_clamped)), 4)
                            rps_ind = round((prob - hit_numeric) ** 2, 4)

                            pred_created_at = (
                                pred_doc.get("created_at")
                                or pred_doc.get("prediction_created_at")
                                or pred_doc.get("timestamp")
                                or pred_doc.get("kickoffUtc")
                                or now_iso
                            )

                            p_run_id = (
                                pred_doc.get("prediction_run_id")
                                or pred_doc.get("run_id")
                                or ""
                            )

                            # Persistent evaluation record written against the same prediction/run
                            result_rec = {
                                "id": m_id,
                                "prediction_id": m_id,
                                "prediction_run_id": p_run_id,
                                "run_id": p_run_id,
                                "fixture_id": op_id,
                                "sport": pred_doc.get("sport") or "football",
                                "market": m_name,
                                "model": pred_doc.get("model") or pred_doc.get("modelVersion") or "ELO + POISSON",
                                "model_version": pred_doc.get("model_version") or pred_doc.get("modelVersion") or "2.0.0",
                                "feature_version": pred_doc.get("feature_version") or pred_doc.get("featureVersion") or "2.0.0",
                                "data_cutoff": pred_doc.get("data_cutoff") or pred_created_at,
                                "raw_probability": pred_doc.get("raw_probability") or prob,
                                "calibrated_probability": round(prob, 4),
                                "confidence": pred_doc.get("confidence") or "HIGH",
                                "publication_status": "settled",
                                "calibration_version": pred_doc.get("calibration_version") or pred_doc.get("calibrationVersion") or "cal_v1.0",
                                "predicted_probability": round(prob, 4),
                                "predicted_outcome": selection,
                                "actual_outcome": resolved_res["actual_outcome"],
                                "hit_or_miss": hit_or_miss,
                                "is_correct": (hit_or_miss == "hit"),
                                "brier_score": brier_ind,
                                "log_loss": log_loss_ind,
                                "rps": rps_ind,
                                "settlement_details": {
                                    "prediction_run_id": p_run_id,
                                    "home_score": home_score,
                                    "away_score": away_score,
                                    "display": display,
                                    "brier_score": brier_ind,
                                    "log_loss": log_loss_ind,
                                    "rps": rps_ind,
                                },
                                "prediction_created_at": pred_created_at,
                                "result_recorded_at": now_iso,
                                "settled_at": now_iso,
                            }

                            try:
                                await database_router.prediction_results.save_results([result_rec])
                                # Retain settlement result for future calibration/model evaluation
                                cal_data = {
                                    "sport": result_rec["sport"],
                                    "market": result_rec["market"],
                                    "model_name": result_rec["model"],
                                    "prediction_run_id": p_run_id,
                                    "brier_score": brier_ind,
                                    "log_loss": log_loss_ind,
                                    "rps": rps_ind,
                                    "last_evaluated_at": now_iso,
                                }
                                await database_router.calibrations.save_calibration(
                                    sport=result_rec["sport"],
                                    model_name=result_rec["model"],
                                    market_type=result_rec["market"],
                                    data=cal_data,
                                )
                            except Exception as router_res_err:
                                print(f"[SyncService] Prediction result save notice: {router_res_err}")

                            evaluated_count += 1
                            if hit_or_miss == "hit":
                                hit_count += 1
                            else:
                                miss_count += 1
            except Exception as e:
                errors.append(f"Error evaluating predictions for operational event {op_id}: {str(e)}")

        # 5. Compute performance metrics from prediction_results
        performance_metrics = {
            "global_accuracy": 0.0,
            "brier_score": 0.0,
            "log_loss": 0.0,
            "calibration_error": 0.0,
            "total_evaluated": 0,
            "market_accuracy": {},
            "sport_accuracy": {},
            "confidence_bucket_performance": {},
        }
        
        try:
            records = await database_router.prediction_results.get_recent_results(limit=1000)
            if records:
                total_rec = len(records)
                hits_rec = 0
                brier_sum = 0.0
                log_loss_sum = 0.0
                
                sport_groups = {}
                market_groups = {}
                
                buckets = [
                    {"name": "50-60%", "min": 0.5, "max": 0.6, "predicted_sum": 0.0, "hit_count": 0, "total": 0},
                    {"name": "60-70%", "min": 0.6, "max": 0.7, "predicted_sum": 0.0, "hit_count": 0, "total": 0},
                    {"name": "70-80%", "min": 0.7, "max": 0.8, "predicted_sum": 0.0, "hit_count": 0, "total": 0},
                    {"name": "80-90%", "min": 0.8, "max": 0.9, "predicted_sum": 0.0, "hit_count": 0, "total": 0},
                    {"name": "90-100%", "min": 0.9, "max": 1.05, "predicted_sum": 0.0, "hit_count": 0, "total": 0},
                ]
                
                for r in records:
                    p = float(r.get("predicted_probability") or 0.5)
                    if p > 1.0:
                        p = p / 100.0
                    
                    hit = 1 if r.get("hit_or_miss") == "hit" else 0
                    hits_rec += hit
                    
                    # Brier Score
                    brier_sum += (p - hit) ** 2
                    
                    # Log Loss
                    p_clipped = min(max(p, 1e-15), 1.0 - 1e-15)
                    log_loss_sum += hit * math.log(p_clipped) + (1 - hit) * math.log(1.0 - p_clipped)
                    
                    # Grouping
                    sport = r.get("sport", "unknown")
                    sport_groups.setdefault(sport, {"hits": 0, "total": 0})
                    sport_groups[sport]["hits"] += hit
                    sport_groups[sport]["total"] += 1
                    
                    market = r.get("market", "unknown")
                    market_groups.setdefault(market, {"hits": 0, "total": 0})
                    market_groups[market]["hits"] += hit
                    market_groups[market]["total"] += 1
                    
                    for b in buckets:
                        if b["min"] <= p < b["max"]:
                            b["predicted_sum"] += p
                            b["hit_count"] += hit
                            b["total"] += 1
                            break
                            
                global_accuracy = hits_rec / total_rec
                brier_score = brier_sum / total_rec
                log_loss = -log_loss_sum / total_rec
                
                ece = 0.0
                confidence_bucket_perf = {}
                for b in buckets:
                    if b["total"] > 0:
                        avg_pred = b["predicted_sum"] / b["total"]
                        avg_actual = b["hit_count"] / b["total"]
                        ece += (b["total"] / total_rec) * abs(avg_pred - avg_actual)
                        confidence_bucket_perf[b["name"]] = {
                            "accuracy": round(avg_actual, 4),
                            "avg_probability": round(avg_pred, 4),
                            "total_predictions": b["total"],
                            "hits": b["hit_count"],
                        }
                    else:
                        confidence_bucket_perf[b["name"]] = {
                            "accuracy": 0.0,
                            "avg_probability": 0.0,
                            "total_predictions": 0,
                            "hits": 0,
                        }
                        
                sport_acc = {
                    sp: {"accuracy": round(g["hits"] / g["total"], 4), "total": g["total"]}
                    for sp, g in sport_groups.items()
                }
                market_acc = {
                    mk: {"accuracy": round(g["hits"] / g["total"], 4), "total": g["total"]}
                    for mk, g in market_groups.items()
                }
                
                performance_metrics = {
                    "global_accuracy": round(global_accuracy, 4),
                    "brier_score": round(brier_score, 4),
                    "log_loss": round(log_loss, 4),
                    "calibration_error": round(ece, 4),
                    "total_evaluated": total_rec,
                    "market_accuracy": market_acc,
                    "sport_accuracy": sport_acc,
                    "confidence_bucket_performance": confidence_bucket_perf,
                }
        except Exception as e:
            errors.append(f"Error computing performance metrics: {str(e)}")

        # 6. Update Redis caches with fresh scores for active/serving feed
        try:
            published_feed = await database_router.predictions.get_published_feed(limit=200)
            
            # Fetch only the matching operational events by ID
            active_f_ids = list(set([m.get("fixture_id") or m.get("id") or m.get("fixtureId") for m in published_feed if (m.get("fixture_id") or m.get("id") or m.get("fixtureId"))]))
            if active_f_ids:
                op_events_list = await database_router.fixtures.get_by_ids(active_f_ids)
                op_events_map = {op["id"]: op for op in op_events_list if "id" in op}
            else:
                op_events_map = {}
            
            for m in published_feed:
                f_id = m.get("fixture_id") or m.get("id")
                if f_id in op_events_map:
                    op = op_events_map[f_id]
                    if op.get("currentScore") is not None:
                        m["currentScore"] = op.get("currentScore")
                    if op.get("status"):
                        raw = str(op.get("status")).lower().strip()
                        m["status"] = "live" if raw == "live" else "completed" if raw == "completed" else "upcoming"
            
            # Save back to Redis
            feed_by_date: Dict[str, List[Dict[str, Any]]] = {}
            for item in published_feed:
                d_str = get_lagos_date_str(item.get("kickoffUtc") or item.get("scheduled_at"))
                if d_str:
                    feed_by_date.setdefault(d_str, []).append(item)
                    
            for date_str, date_items in feed_by_date.items():
                await redis_client.set_json(f"predictpro:feed:{date_str}", date_items, ex_seconds=86400)
                
            await redis_client.set_json("predictpro:feed:latest", published_feed, ex_seconds=86400)
        except Exception as e:
            errors.append(f"Error updating Redis caches after sync feed: {str(e)}")

        # 7-12. Run Candidate Evaluation, Validation Gates & Promotion Pipeline
        eval_summary: Dict[str, Any] = {}
        try:
            eval_summary = await asyncio.to_thread(evaluation_pipeline.run_candidate_evaluation_and_promotion)
        except Exception as eval_err:
            errors.append(f"Error in candidate evaluation pipeline: {str(eval_err)}")
            eval_summary = {
                "candidate_calibration_records_created": 0,
                "candidates_evaluated": 0,
                "promotions_approved": 0,
                "candidate_details": [],
                "current_production_model": model_service.get_active_model(),
            }

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        diagnostics = {
            "status": "success" if not errors else "partial",
            "timestamp": now_iso,
            "duration_ms": duration_ms,
            "provider_results": {
                "football": fb_fetched,
                "basketball": bk_fetched,
                "baseball": bb_fetched,
                "hockey": hk_fetched,
                "formula_1": f1_fetched,
            },
            "live_matches_updated": live_updated_count,
            "completed_matches_updated": completed_updated_count,
            "predictions_evaluated": evaluated_count,
            "hits": hit_count,
            "misses": miss_count,
            "metrics_updated": performance_metrics,
            "evaluation_records_created": evaluated_count,
            "candidate_calibration_records_created": eval_summary.get("candidate_calibration_records_created", 0),
            "candidates_evaluated": eval_summary.get("candidates_evaluated", 0),
            "promotions_approved": eval_summary.get("promotions_approved", 0),
            "candidate_details": eval_summary.get("candidate_details", []),
            "current_production_model": eval_summary.get("current_production_model", model_service.get_active_model()),
            "errors": errors,
        }
        # Structured logging for operations
        import json
        structured_log = {
            "event": "sync_feed_completed",
            "duration_ms": duration_ms,
            "provider_events_processed": len(all_prov_fixtures),
            "mongo_docs_returned": len(operational_events),
            "matches_matched": len(matched_completed_fixtures),
            "predictions_evaluated": evaluated_count,
            "predictions_published": len(published_feed) if 'published_feed' in locals() else 0,
        }
        print(f"[SyncFeed] {json.dumps(structured_log)}")

        # Trigger controlled incremental replication to update reconciled scores & results in Neon
        try:
            repl_summary = await replication_manager.execute_incremental_replication(trigger_source="sync_feed")
            diagnostics["neon_replication"] = repl_summary
        except Exception as repl_err:
            print(f"[SyncFeed] Controlled replication notice: {repl_err}")
            diagnostics["neon_replication"] = {"status": "skipped", "error": str(repl_err)}

        return diagnostics

sync_service = SyncService()
