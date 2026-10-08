from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Set
from backend.db.redis_client import redis_client
from backend.db.database_router import database_router
from backend.engine.ranking import rank_and_select_best_of_day

# Africa/Lagos is West Africa Time (WAT), UTC+1
LAGOS_TZ = timezone(timedelta(hours=1))

def get_lagos_date_str(val: Any) -> str:
    """
    Extracts YYYY-MM-DD in Africa/Lagos timezone from an ISO UTC string or datetime.
    """
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.astimezone(LAGOS_TZ).strftime("%Y-%m-%d")
    if isinstance(val, str):
        try:
            clean = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(LAGOS_TZ).strftime("%Y-%m-%d")
        except Exception:
            return val[:10]
    return ""

def get_current_lagos_today() -> str:
    return datetime.now(LAGOS_TZ).strftime("%Y-%m-%d")

class FeedService:
    def normalize_feed_item(self, m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        val_st = str(m.get("validationStatus") or m.get("validation_status") or "").lower().strip()
        if val_st != "validated":
            return None
        if m.get("published") is False:
            return None

        # Normalized lookup using validatedMarkets first and markets as fallback
        raw_markets = m.get("validatedMarkets") or m.get("markets")
        markets = []
        if raw_markets and isinstance(raw_markets, list):
            for mk in raw_markets:
                if isinstance(mk, dict):
                    markets.append(mk)
                elif isinstance(mk, str):
                    pct_val = m.get("calibratedPercentage") or m.get("percentage") or 50.0
                    markets.append({
                        "id": f"{m.get('id', 'pred')}-m{len(markets)+1}",
                        "marketName": mk,
                        "selection": m.get("selection") or mk,
                        "probabilityPercentage": float(pct_val),
                        "confidence": m.get("confidence") or "HIGH",
                    })

        if not markets:
            m_name = m.get("market") or (m.get("highestPercentagePrediction", {}).get("marketName") if isinstance(m.get("highestPercentagePrediction"), dict) else None)
            sel_name = m.get("selection") or (m.get("highestPercentagePrediction", {}).get("selection") if isinstance(m.get("highestPercentagePrediction"), dict) else None)
            pct_val = m.get("calibratedPercentage") or m.get("percentage") or (m.get("highestPercentagePrediction", {}).get("percentage") if isinstance(m.get("highestPercentagePrediction"), dict) else None)
            if m_name and sel_name and pct_val is not None:
                markets = [{
                    "id": f"{m.get('id', 'pred')}-m1",
                    "marketName": m_name,
                    "selection": sel_name,
                    "probabilityPercentage": float(pct_val),
                    "confidence": m.get("confidence") or "HIGH",
                }]
            else:
                # If no markets exist, skip the record
                return None

        # Set m['validatedMarkets'] = markets before returning the prediction
        m["validatedMarkets"] = markets

        # Ensure every returned prediction contains: fixtureId, sport, league, homeTeam, awayTeam, kickoff, prediction, probability, validatedMarkets, validationStatus, and published
        pred_id = m.get("id") or m.get("prediction_id") or m.get("fixtureId") or m.get("fixture_id")
        f_id = m.get("fixtureId") or m.get("fixture_id") or m.get("id")
        m["fixtureId"] = f_id
        m["fixture_id"] = f_id
        m["id"] = pred_id
        m["sport"] = m.get("sport") or "football"
        m["league"] = m.get("league") or "General"
        m["homeTeam"] = m.get("homeTeam") or m.get("home_team") or ""
        m["awayTeam"] = m.get("awayTeam") or m.get("away_team") or ""

        kickoff = m.get("kickoff") or m.get("kickoffUtc") or m.get("scheduled_at") or ""
        m["kickoff"] = kickoff
        m["kickoffUtc"] = kickoff

        highest_market = max(
            markets,
            key=lambda mk: float(
                mk.get("calibratedPercentage")
                if isinstance(mk, dict) and mk.get("calibratedPercentage") is not None
                else (
                    mk.get("probabilityPercentage")
                    if isinstance(mk, dict) and mk.get("probabilityPercentage") is not None
                    else (
                        mk.get("percentage")
                        if isinstance(mk, dict) and mk.get("percentage") is not None
                        else 0
                    )
                )
            )
        ) if markets and isinstance(markets, list) else {}

        prob_pct_val = None
        if isinstance(highest_market, dict) and highest_market:
            for k in ("calibratedPercentage", "probabilityPercentage", "percentage"):
                if highest_market.get(k) is not None:
                    prob_pct_val = highest_market.get(k)
                    break
        if prob_pct_val is None:
            for k in ("calibratedPercentage", "percentage", "probability"):
                if m.get(k) is not None:
                    prob_pct_val = m.get(k)
                    break

        if prob_pct_val is None:
            # A missing probability must cause the prediction to be rejected rather than displaying a fake 50%
            return None

        prob_num = float(prob_pct_val)
        pred_text = (highest_market.get("selection") if isinstance(highest_market, dict) else None) or m.get("prediction") or m.get("selection") or ""
        m_name = (highest_market.get("marketName") if isinstance(highest_market, dict) else None) or m.get("market") or ""

        m["prediction"] = pred_text
        m["selection"] = pred_text
        m["market"] = m_name
        m["probability"] = prob_num
        m["percentage"] = prob_num
        m["calibratedPercentage"] = prob_num
        m["validationStatus"] = "validated"
        m["validation_status"] = "validated"
        m["published"] = True

        if not m.get("highestPercentagePrediction") or not isinstance(m.get("highestPercentagePrediction"), dict):
            m["highestPercentagePrediction"] = {
                "marketName": m_name,
                "selection": pred_text,
                "percentage": prob_num,
            }
        else:
            hpp = m["highestPercentagePrediction"]
            if not hpp.get("marketName"):
                hpp["marketName"] = m_name
            if not hpp.get("selection"):
                hpp["selection"] = pred_text
            if hpp.get("percentage") is None:
                hpp["percentage"] = prob_num

        return m

    def filter_valid_predictions(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        valid = []
        for m in items:
            norm = self.normalize_feed_item(m)
            if norm:
                valid.append(norm)
        return valid

    def normalize_feed_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.filter_valid_predictions(items)

    async def get_feed(
        self,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        date: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        GET feed strictly reads the already-published validated feed from Redis / DatabaseRouter.
        Never calls SportsSkills, never trains models, never computes predictions lazily.
        Enforces maximum limit of 20 published predictions.
        Excludes rejected, abstained, insufficient_data, and completed matches from active prediction feeds.
        """
        matches: List[Dict[str, Any]] = []
        is_cache_hit = False

        if date:
            # 1. Read date-specific key from Redis (fast serving path)
            try:
                cached = await redis_client.get_json(f"predictpro:feed:{date}")
                if cached is not None and isinstance(cached, list) and len(cached) > 0:
                    matches = cached
                    is_cache_hit = True
            except Exception as redis_err:
                print(f"[FeedService] Redis date feed read notice: {redis_err}")
                matches = []

            # 2. Redis miss or failure -> fallback to DatabaseRouter active operational database
            if not is_cache_hit:
                # Do not swallow DatabaseUnavailableError: propagate explicit error state when SELECT 1 fails
                matches = await database_router.predictions.get_daily_feed(date, sport=sport, league=league, limit=50)
        else:
            # 1. Read latest key from Redis (fast serving path)
            try:
                cached = await redis_client.get_json("predictpro:feed:latest")
                if cached is not None and isinstance(cached, list) and len(cached) > 0:
                    matches = cached
                    is_cache_hit = True
            except Exception as redis_err:
                print(f"[FeedService] Redis latest feed read notice: {redis_err}")
                matches = []

            # 2. Redis miss or failure -> fallback to DatabaseRouter active operational database
            if not is_cache_hit:
                # Do not swallow DatabaseUnavailableError: propagate explicit error state when SELECT 1 fails
                matches = await database_router.predictions.get_published_feed(sport=sport, league=league, limit=50)

        # Gate: Filter strictly to validated + published items
        validated_feed = self.filter_valid_predictions(matches)

        # Status and score are returned directly by Neon in one unified query (JOIN).
        # Only fallback to separate lookup if individual items are missing operational status.
        missing_status_items = [m for m in validated_feed if not m.get("status") or m.get("currentScore") is None]
        if missing_status_items:
            try:
                fixture_ids = [m.get("fixture_id") or m.get("id") for m in missing_status_items if (m.get("fixture_id") or m.get("id"))]
                if fixture_ids:
                    try:
                        op_events_list = await database_router.fixtures.get_by_ids(fixture_ids)
                    except Exception as route_fx_err:
                        print(f"[FeedService] Database router get_by_ids notice: {route_fx_err}")
                        op_events_list = []

                    op_events_map = {op["id"]: op for op in op_events_list if "id" in op}
                    for m in missing_status_items:
                        f_id = m.get("fixture_id") or m.get("id")
                        if f_id in op_events_map:
                            op = op_events_map[f_id]
                            if op.get("currentScore") is not None:
                                m["currentScore"] = op.get("currentScore")
                            if op.get("status"):
                                raw = str(op.get("status")).lower().strip()
                                m["status"] = "live" if raw == "live" else "completed" if raw == "completed" else "upcoming"
            except Exception as e:
                print(f"[FeedService] Notice verifying secondary status in get_feed: {e}")

        # Filter by requested Lagos calendar date
        today_lagos = get_current_lagos_today()
        if date:
            # Specific date requested (must match event's Lagos date)
            filtered = [
                m for m in validated_feed
                if (m.get("event_date_lagos") == date or get_lagos_date_str(m.get("kickoffUtc") or m.get("scheduled_at")) == date)
            ]
        else:
            # Default: Today and future dates only in Africa/Lagos
            filtered = [
                m for m in validated_feed
                if (m.get("event_date_lagos") and m.get("event_date_lagos") >= today_lagos)
                or get_lagos_date_str(m.get("kickoffUtc") or m.get("scheduled_at") or m.get("start_time")) >= today_lagos
            ]

        # Filter by sport if requested
        if sport and sport.lower() != "all":
            filtered = [m for m in filtered if m.get("sport", "").lower() == sport.lower()]

        # Filter by league if requested
        if league and league.lower() != "all":
            filtered = [m for m in filtered if m.get("league", "").lower() == league.lower()]

        # Strict limit capped at 20
        max_allowed = min(20, max(1, limit))

        # Apply sport-aware ranking if fetching across all sports and no explicit best-of-day set
        has_explicit_best = any(it.get("isBestOfDay") is not None or it.get("is_best_of_day") is not None for it in filtered)
        if not has_explicit_best and (not sport or sport.lower() == "all"):
            filtered = rank_and_select_best_of_day(filtered, max_results=max_allowed)

        # Sort feed by: 1. best-of-day first 2. highest calibrated probability 3. kickoff time
        def _feed_sort_key(item: Dict[str, Any]):
            is_best = 0 if (item.get("isBestOfDay") is True or item.get("is_best_of_day") is True) else 1
            prob_val = 0.0
            if item.get("calibratedPercentage") is not None:
                prob_val = float(item["calibratedPercentage"])
            elif item.get("percentage") is not None:
                prob_val = float(item["percentage"])
            elif isinstance(item.get("highestPercentagePrediction"), dict) and item["highestPercentagePrediction"].get("percentage") is not None:
                prob_val = float(item["highestPercentagePrediction"]["percentage"])
            kickoff = str(item.get("kickoffUtc") or item.get("scheduled_at") or "")
            return (is_best, -prob_val, kickoff)

        filtered = sorted(filtered, key=_feed_sort_key)

        # STRICT RULE: Only live and completed fixtures should display or have a score.
        # Upcoming matches must NEVER have currentScore or finalScore.
        for item in filtered:
            raw_st = str(item.get("status") or "").lower().strip()
            is_completed = raw_st in ("completed", "finished", "ft", "ended")
            is_live = not is_completed and raw_st in ("live", "in_progress", "halftime", "1st_half", "2nd_half")

            if is_completed:
                item["status"] = "completed"
                if not item.get("finalScore") and item.get("currentScore"):
                    item["finalScore"] = item["currentScore"]
            elif is_live:
                item["status"] = "live"
                item["finalScore"] = None
            else:
                item["status"] = "upcoming"
                item["currentScore"] = None
                item["current_score"] = None
                item["finalScore"] = None
                item["final_score"] = None
                item.pop("currentScore", None)
                item.pop("current_score", None)
                item.pop("finalScore", None)
                item.pop("final_score", None)

        return filtered[:max_allowed]

    async def get_available_prediction_dates(self) -> List[str]:
        """
        Returns a sorted list of unique YYYY-MM-DD dates (Africa/Lagos)
        for today and future that have published predictions.
        Never computes predictions. Return actual dates only.
        """
        dates_set: Set[str] = set()
        today_lagos = get_current_lagos_today()

        # 1. Check Redis keys matching predictpro:feed:*
        try:
            feed_keys = await redis_client.get_keys("predictpro:feed:*")
            for k in feed_keys:
                if k == "predictpro:feed:latest":
                    cached = await redis_client.get_json("predictpro:feed:latest")
                    if cached and isinstance(cached, list):
                        for m in cached:
                            if m.get("validationStatus") == "validated" and m.get("validatedMarkets"):
                                d = get_lagos_date_str(m.get("kickoffUtc") or m.get("scheduled_at"))
                                if d and d >= today_lagos:
                                    dates_set.add(d)
                elif k.startswith("predictpro:feed:"):
                    date_part = k.split("predictpro:feed:")[-1]
                    if len(date_part) == 10 and date_part >= today_lagos:
                        cached = await redis_client.get_json(k)
                        if cached and isinstance(cached, list):
                            valid_items = [
                                m for m in cached
                                if m.get("validationStatus") == "validated" and m.get("validatedMarkets")
                            ]
                            if valid_items:
                                dates_set.add(date_part)
        except Exception as e:
            print(f"[FeedService] Redis available dates error: {e}")

        # 2. Check operational database via database_router
        try:
            published_docs = await database_router.predictions.get_published_feed(limit=100)
            for doc in published_docs:
                if doc.get("validatedMarkets"):
                    d = doc.get("event_date_lagos") or get_lagos_date_str(doc.get("kickoffUtc") or doc.get("scheduled_at"))
                    if d and d >= today_lagos:
                        dates_set.add(d)
        except Exception as e:
            print(f"[FeedService] DatabaseRouter available dates notice: {e}")

        return sorted(list(dates_set))

    async def get_match_by_id(self, match_id: str) -> Optional[Dict[str, Any]]:
        # Fetch operational event for final score / live status if available
        event = None
        try:
            event = await database_router.fixtures.get_by_id(match_id)
        except Exception:
            pass

        # Check Redis cached feed
        cached = await redis_client.get_json("predictpro:feed:latest")
        if cached and isinstance(cached, list):
            for m in cached:
                if m.get("id") == match_id:
                    if event:
                        if event.get("currentScore") is not None:
                            m["currentScore"] = event.get("currentScore")
                        if event.get("status"):
                            raw = str(event.get("status")).lower().strip()
                            m["status"] = "live" if raw == "live" else "completed" if raw == "completed" else "upcoming"
                    return m

        # Check operational database predictions via database_router
        try:
            doc = await database_router.predictions.get_by_id(match_id)
            if not doc:
                preds = await database_router.predictions.get_by_fixture_id(match_id)
                if preds:
                    doc = preds[0]
            if doc:
                if event:
                    if event.get("currentScore") is not None:
                        doc["currentScore"] = event.get("currentScore")
                    if event.get("status"):
                        raw = str(event.get("status")).lower().strip()
                        doc["status"] = "live" if raw == "live" else "completed" if raw == "completed" else "upcoming"
                return doc
        except Exception:
            pass

        if event:
            evt_copy = dict(event)
            evt_copy["predictionAvailable"] = False
            evt_copy["validatedMarkets"] = []
            return evt_copy

        return None

    async def get_competition_telemetry(self) -> List[Dict[str, Any]]:
        """
        Returns recent cross-sport competition telemetry:
        Sport | Competition | Fixtures discovered | Eligible | Rejected | Published
        """
        try:
            latest_run = await database_router.refresh_state.get_latest_run()
            if latest_run and isinstance(latest_run, dict):
                diag = latest_run.get("diagnostics") or {}
                if "competition_telemetry" in diag:
                    return diag["competition_telemetry"]
                if "competitionTelemetry" in diag:
                    return diag["competitionTelemetry"]
        except Exception:
            pass
        return []

    async def get_competition_telemetry_table(self) -> str:
        try:
            latest_run = await database_router.refresh_state.get_latest_run()
            if latest_run and isinstance(latest_run, dict):
                diag = latest_run.get("diagnostics") or {}
                if "competition_telemetry_table" in diag:
                    return diag["competition_telemetry_table"]
                if "competitionTelemetryTable" in diag:
                    return diag["competitionTelemetryTable"]
        except Exception:
            pass
        return "Sport | Competition | Fixtures discovered | Eligible | Rejected | Published\n(No competitions active)"

    async def get_latest_diagnostics_summary(self) -> Dict[str, Any]:
        """
        Returns structured summary metrics for UI display:
        Fixtures discovered, Fixtures eligible, Predictions validated, Predictions published,
        Sports represented, Leagues represented, and per-sport breakdown.
        """
        try:
            latest_run = await database_router.refresh_state.get_latest_run()
            if latest_run and isinstance(latest_run, dict):
                diag = latest_run.get("diagnostics") or {}
                per_sport_audit = diag.get("per_sport_audit") or diag.get("perSportAudit") or {}
                comp_telem = diag.get("competition_telemetry") or diag.get("competitionTelemetry") or []

                discovered = diag.get("fixturesDiscovered") or diag.get("fixtures_discovered") or diag.get("fixtures_deduplicated_count") or latest_run.get("matches_synced", 0)
                eligible = diag.get("fixturesEligible") or diag.get("fixtures_eligible") or diag.get("fixtures_eligible_count") or 0
                validated = diag.get("predictionsValidated") or diag.get("validatedCount") or diag.get("validated_count") or latest_run.get("validatedCount", 0)
                published = diag.get("predictionsPublished") or diag.get("publishedCount") or diag.get("published_count") or latest_run.get("predictions_published", 0)

                sports_rep = diag.get("sportsRepresented") or diag.get("sports_represented")
                leagues_rep = diag.get("leaguesRepresented") or diag.get("leagues_represented")

                if sports_rep is None or sports_rep == 0:
                    sports_rep = len({r["sport"] for r in comp_telem if r.get("published", 0) > 0}) or len({r["sport"] for r in comp_telem if r.get("eligible", 0) > 0})
                if leagues_rep is None or leagues_rep == 0:
                    leagues_rep = len({r["competition"] for r in comp_telem if r.get("published", 0) > 0}) or len({r["competition"] for r in comp_telem if r.get("eligible", 0) > 0})

                per_sport_list = []
                for sp, data in per_sport_audit.items():
                    if isinstance(data, dict):
                        per_sport_list.append({
                            "sport": sp,
                            "discovered": data.get("discovered", 0),
                            "eligible": data.get("history_eligible", data.get("eligible", 0)),
                            "published": data.get("publishable", data.get("published", 0)),
                            "rejection_reason": data.get("exact_rejection_reason", "NONE"),
                        })

                return {
                    "fixturesDiscovered": discovered,
                    "fixturesEligible": eligible,
                    "predictionsValidated": validated,
                    "predictionsPublished": published,
                    "sportsRepresented": sports_rep,
                    "leaguesRepresented": leagues_rep,
                    "perSport": per_sport_list,
                    "runId": latest_run.get("id"),
                    "timestamp": latest_run.get("timestamp"),
                }
        except Exception:
            pass

        return {
            "fixturesDiscovered": 0,
            "fixturesEligible": 0,
            "predictionsValidated": 0,
            "predictionsPublished": 0,
            "sportsRepresented": 0,
            "leaguesRepresented": 0,
            "perSport": [],
            "runId": None,
            "timestamp": None,
        }

def is_upcoming_fixture(fix: Dict[str, Any], today_lagos: Optional[str] = None) -> bool:
    if not today_lagos:
        today_lagos = get_current_lagos_today()
    if fix.get("event_date_lagos"):
        return fix.get("event_date_lagos") >= today_lagos
    k_time = fix.get("kickoffUtc") or fix.get("fixture_time") or fix.get("scheduled_at") or fix.get("start_time")
    if k_time:
        return get_lagos_date_str(k_time) >= today_lagos
    return True

feed_service = FeedService()
