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
    def filter_valid_predictions(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        valid = []
        for m in items:
            val_st = str(m.get("validationStatus") or m.get("validation_status") or "").lower().strip()
            if val_st != "validated":
                continue
            if m.get("published") is False:
                continue
            valid.append(m)
        return valid

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
        validated_feed = []
        for m in matches:
            val_st = str(m.get("validationStatus") or m.get("validation_status") or "").lower().strip()
            if val_st != "validated":
                continue
            if m.get("published") is False:
                continue

            # Normalized lookup using validatedMarkets first and markets as fallback
            markets = m.get("validatedMarkets") or m.get("markets")
            if not markets or not isinstance(markets, list) or len(markets) == 0:
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
                    continue

            # Set m['validatedMarkets'] = markets before returning the prediction
            m["validatedMarkets"] = markets

            # Ensure every returned prediction contains: fixtureId, sport, league, homeTeam, awayTeam, kickoff, prediction, probability, validatedMarkets, validationStatus, and published
            f_id = m.get("fixtureId") or m.get("fixture_id") or m.get("id")
            m["fixtureId"] = f_id
            m["fixture_id"] = f_id
            m["id"] = f_id
            m["sport"] = m.get("sport") or "football"
            m["league"] = m.get("league") or "General"
            m["homeTeam"] = m.get("homeTeam") or m.get("home_team") or ""
            m["awayTeam"] = m.get("awayTeam") or m.get("away_team") or ""

            kickoff = m.get("kickoff") or m.get("kickoffUtc") or m.get("scheduled_at") or ""
            m["kickoff"] = kickoff
            m["kickoffUtc"] = kickoff

            first_m = markets[0] if markets else {}
            pred_text = m.get("prediction") or m.get("selection") or first_m.get("selection") or ""
            prob_num = m.get("probability")
            if prob_num is None:
                prob_pct_val = m.get("calibratedPercentage") or m.get("percentage") or first_m.get("probabilityPercentage") or 50.0
                prob_num = float(prob_pct_val)

            m["prediction"] = pred_text
            m["probability"] = prob_num
            m["percentage"] = prob_num
            m["calibratedPercentage"] = prob_num
            m["validationStatus"] = "validated"
            m["validation_status"] = "validated"
            m["published"] = True

            if not m.get("highestPercentagePrediction"):
                m["highestPercentagePrediction"] = {
                    "marketName": first_m.get("marketName") or m.get("market", ""),
                    "selection": pred_text,
                    "percentage": prob_num,
                }

            validated_feed.append(m)

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
            # Default: Today and future dates only in Africa/Lagos, or latest validated items if none in future
            filtered = [
                m for m in validated_feed
                if (m.get("event_date_lagos") and m.get("event_date_lagos") >= today_lagos)
                or get_lagos_date_str(m.get("kickoffUtc") or m.get("scheduled_at")) >= today_lagos
            ]
            if not filtered and validated_feed:
                filtered = list(validated_feed)

        # Filter by sport if requested
        if sport and sport.lower() != "all":
            filtered = [m for m in filtered if m.get("sport", "").lower() == sport.lower()]

        # Filter by league if requested
        if league and league.lower() != "all":
            filtered = [m for m in filtered if m.get("league", "").lower() == league.lower()]

        # Strict limit capped at 20
        max_allowed = min(20, max(1, limit))

        # Apply sport-aware ranking if fetching across all sports
        if not sport or sport.lower() == "all":
            filtered = rank_and_select_best_of_day(filtered, max_results=max_allowed)

        # Sort feed by: 1. best-of-day first 2. highest calibrated probability 3. kickoff time
        def _feed_sort_key(item: Dict[str, Any]):
            is_best = 0 if item.get("isBestOfDay") else 1
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

feed_service = FeedService()
