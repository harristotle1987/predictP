"""
MongoDB Atlas Adapter.
Implements the IDatabaseAdapter and repository interfaces for the primary operational store.
Encapsulates all direct PyMongo operations behind typed, bounded, and projected interfaces.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from backend.db.interfaces import (
    IDatabaseAdapter,
    IFixtureRepository,
    IPredictionRepository,
    IPredictionResultRepository,
    IRefreshStateRepository,
    IModelConfigRepository,
    ICalibrationRepository,
    IModelGovernanceRepository,
    IFeatureRepository,
    DatabaseUnavailableError,
)
from backend.db.mongodb import mongo_manager, UpdateOne
from backend.config import settings

def _assert_mongo_write_permitted():
    """
    SPLIT-BRAIN PROTECTION:
    Disallows writing to MongoDB when Neon PostgreSQL is the active write authority.
    """
    from backend.db.failover_manager import failover_manager
    if not failover_manager.is_mongo_write_permitted():
        raise DatabaseUnavailableError(
            "SPLIT-BRAIN PROTECTION: MongoDB writes are disabled while Neon PostgreSQL is the active write authority."
        )

# Explicit projections to prohibit SELECT * equivalent broad queries
FIXTURE_PROJECTION = {
    "_id": 0,
    "id": 1,
    "source_system": 1,
    "source": 1,
    "sport": 1,
    "league": 1,
    "competition_id": 1,
    "competitionTier": 1,
    "homeTeam": 1,
    "awayTeam": 1,
    "home_team": 1,
    "away_team": 1,
    "home_team_id": 1,
    "away_team_id": 1,
    "kickoffUtc": 1,
    "scheduled_at": 1,
    "status": 1,
    "currentScore": 1,
    "venue": 1,
    "referee": 1,
    "canonical_key": 1,
    "source_event_id": 1,
    "provider_event_id": 1,
    "lagos_date": 1,
    "fetched_at": 1,
    "fixture_version": 1,
    "created_at": 1,
    "updated_at": 1,
}

PREDICTION_PROJECTION = {
    "_id": 0,
    "id": 1,
    "source_system": 1,
    "fixtureId": 1,
    "fixture_id": 1,
    "sport": 1,
    "league": 1,
    "competition_id": 1,
    "homeTeam": 1,
    "awayTeam": 1,
    "home_team": 1,
    "away_team": 1,
    "driver": 1,
    "opponent": 1,
    "kickoffUtc": 1,
    "scheduled_at": 1,
    "date": 1,
    "event_date_lagos": 1,
    "status": 1,
    "currentScore": 1,
    "finalScore": 1,
    "isBestOfDay": 1,
    "bestMarket": 1,
    "markets": 1,
    "market": 1,
    "selection": 1,
    "percentage": 1,
    "raw_probability": 1,
    "calibrated_probability": 1,
    "highestPercentagePrediction": 1,
    "validatedMarkets": 1,
    "sportStats": 1,
    "validationStatus": 1,
    "modelVersion": 1,
    "model": 1,
    "model_version": 1,
    "model_name": 1,
    "modelMetadata": 1,
    "metadata": 1,
    "training_window": 1,
    "training_match_count": 1,
    "feature_timestamp": 1,
    "prediction_timestamp": 1,
    "calibratedPercentage": 1,
    "confidenceScore": 1,
    "published": 1,
    "published_at": 1,
    "prediction_version": 1,
    "created_at": 1,
    "updated_at": 1,
}

RESULT_PROJECTION = {
    "_id": 0,
    "id": 1,
    "source_system": 1,
    "fixtureId": 1,
    "fixture_id": 1,
    "prediction_id": 1,
    "sport": 1,
    "market": 1,
    "model": 1,
    "model_version": 1,
    "calibration_version": 1,
    "predicted_probability": 1,
    "status": 1,
    "predicted_outcome": 1,
    "actual_outcome": 1,
    "hit_or_miss": 1,
    "is_correct": 1,
    "brier_score": 1,
    "log_loss": 1,
    "details": 1,
    "evaluatedAt": 1,
    "prediction_created_at": 1,
    "result_recorded_at": 1,
    "created_at": 1,
    "updated_at": 1,
}


def _safe_cursor(cur, sort_spec=None, limit_num=None):
    if sort_spec and hasattr(cur, "sort") and not isinstance(cur, list):
        if isinstance(sort_spec, list):
            cur = cur.sort(sort_spec)
        else:
            cur = cur.sort(sort_spec)
    if limit_num and hasattr(cur, "limit") and not isinstance(cur, list):
        cur = cur.limit(limit_num)
    res = list(cur)
    if limit_num and isinstance(cur, list):
        res = res[:limit_num]
    return res


class MongoFixtureRepository(IFixtureRepository):
    """MongoDB implementation of Fixture repository."""

    def _get_collection(self):
        col = mongo_manager.db["operational_events"]
        if col is None:
            raise DatabaseUnavailableError("MongoDB operational_events collection unavailable")
        return col

    async def get_by_id(self, fixture_id: str) -> Optional[Dict[str, Any]]:
        if not fixture_id:
            return None
        col = self._get_collection()
        try:
            return col.find_one({"id": fixture_id}, FIXTURE_PROJECTION)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_by_id failed: {e}") from e

    async def get_by_ids(self, fixture_ids: List[str]) -> List[Dict[str, Any]]:
        if not fixture_ids:
            return []
        unique_ids = list(set(fixture_ids))
        col = self._get_collection()
        try:
            cur = col.find({"$or": [{"id": {"$in": unique_ids}}, {"source_event_id": {"$in": unique_ids}}]}, FIXTURE_PROJECTION)
            return _safe_cursor(cur, limit_num=len(unique_ids))
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_by_ids failed: {e}") from e

    async def get_live_fixtures(self, sport: Optional[str] = None) -> List[Dict[str, Any]]:
        col = self._get_collection()
        query: Dict[str, Any] = {"status": {"$in": ["live", "in_play", "inplay", "1H", "2H", "HT"]}}
        if sport and sport.lower() != "all":
            query["sport"] = sport.lower()
        try:
            cur = col.find(query, FIXTURE_PROJECTION)
            return _safe_cursor(cur, sort_spec="kickoffUtc", limit_num=100)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_live_fixtures failed: {e}") from e

    async def get_recent_completed(self, sport: Optional[str] = None, hours: int = 48) -> List[Dict[str, Any]]:
        col = self._get_collection()
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        query: Dict[str, Any] = {
            "status": {"$in": ["completed", "FT", "finished", "AET", "AP"]},
            "kickoffUtc": {"$gte": cutoff_iso},
        }
        if sport and sport.lower() != "all":
            query["sport"] = sport.lower()
        try:
            cur = col.find(query, FIXTURE_PROJECTION)
            return _safe_cursor(cur, sort_spec=[("kickoffUtc", -1)], limit_num=150)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_recent_completed failed: {e}") from e

    async def get_by_date_and_sport(
        self,
        date_str: str,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        col = self._get_collection()
        query: Dict[str, Any] = {
            "$or": [
                {"kickoffUtc": {"$regex": f"^{date_str}"}},
                {"scheduled_at": {"$regex": f"^{date_str}"}},
            ]
        }
        if sport and sport.lower() != "all":
            query["sport"] = sport.lower()
        if league and league.lower() != "all":
            query["league"] = league
        try:
            cur = col.find(query, FIXTURE_PROJECTION)
            return _safe_cursor(cur, sort_spec="kickoffUtc", limit_num=limit)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_by_date_and_sport failed: {e}") from e

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
        col = self._get_collection()
        bounded_limit = min(100, max(1, limit))
        safe_offset = max(0, offset)
        query: Dict[str, Any] = {}

        if start_date and end_date:
            query["kickoffUtc"] = {"$gte": start_date, "$lte": f"{end_date}T23:59:59Z"}
        elif start_date:
            query["kickoffUtc"] = {"$gte": start_date}
        elif end_date:
            query["kickoffUtc"] = {"$lte": f"{end_date}T23:59:59Z"}
        elif date:
            query["$or"] = [
                {"kickoffUtc": {"$regex": f"^{date}"}},
                {"scheduled_at": {"$regex": f"^{date}"}},
            ]

        if sport and sport.lower() != "all":
            query["sport"] = sport.lower()
        if league and league.lower() != "all":
            query["league"] = league
        if status and status.lower() != "all":
            norm_st = status.lower().strip()
            if norm_st == "live":
                query["status"] = {"$in": ["live", "in_play", "inplay", "1H", "2H", "HT"]}
            elif norm_st == "completed":
                query["status"] = {"$in": ["completed", "FT", "finished", "AET", "AP"]}
            elif norm_st in ("upcoming", "scheduled"):
                query["status"] = {"$in": ["scheduled", "upcoming", "NS", "TIMED"]}
            else:
                query["status"] = status

        try:
            cur = col.find(query, FIXTURE_PROJECTION)
            if hasattr(cur, "sort"):
                cur = cur.sort("kickoffUtc", 1)
            if safe_offset > 0 and hasattr(cur, "skip"):
                cur = cur.skip(safe_offset)
            if hasattr(cur, "limit"):
                cur = cur.limit(bounded_limit)
            results = list(cur)
            if safe_offset > 0 and not hasattr(cur, "skip") and isinstance(results, list):
                results = results[safe_offset:safe_offset + bounded_limit]
            elif isinstance(results, list):
                results = results[:bounded_limit]
            return results
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_fixtures failed: {e}") from e

    async def count_fixtures(
        self,
        sport: Optional[str] = None,
        league: Optional[str] = None,
        date: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        col = self._get_collection()
        query: Dict[str, Any] = {}

        if start_date and end_date:
            query["kickoffUtc"] = {"$gte": start_date, "$lte": f"{end_date}T23:59:59Z"}
        elif start_date:
            query["kickoffUtc"] = {"$gte": start_date}
        elif end_date:
            query["kickoffUtc"] = {"$lte": f"{end_date}T23:59:59Z"}
        elif date:
            query["$or"] = [
                {"kickoffUtc": {"$regex": f"^{date}"}},
                {"scheduled_at": {"$regex": f"^{date}"}},
            ]

        if sport and sport.lower() != "all":
            query["sport"] = sport.lower()
        if league and league.lower() != "all":
            query["league"] = league
        if status and status.lower() != "all":
            norm_st = status.lower().strip()
            if norm_st == "live":
                query["status"] = {"$in": ["live", "in_play", "inplay", "1H", "2H", "HT"]}
            elif norm_st == "completed":
                query["status"] = {"$in": ["completed", "FT", "finished", "AET", "AP"]}
            elif norm_st in ("upcoming", "scheduled"):
                query["status"] = {"$in": ["scheduled", "upcoming", "NS", "TIMED"]}
            else:
                query["status"] = status
        try:
            if hasattr(col, "count_documents"):
                return col.count_documents(query)
            elif hasattr(col, "count"):
                return col.count(query)
            return len(list(col.find(query, {"_id": 1})))
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB count_fixtures failed: {e}") from e

    async def prune_expired_fixtures(self, retention_days: int = 14) -> int:
        _assert_mongo_write_permitted()
        col = self._get_collection()
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        query = {
            "status": {"$in": ["completed", "FT", "finished", "AET", "AP"]},
            "kickoffUtc": {"$lt": cutoff_iso},
        }
        try:
            if hasattr(col, "delete_many"):
                res = col.delete_many(query)
                return getattr(res, "deleted_count", 0)
            return 0
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB prune_expired_fixtures failed: {e}") from e

    async def upsert_fixtures(self, fixtures: List[Dict[str, Any]]) -> int:
        _assert_mongo_write_permitted()
        if not fixtures:
            return 0
        col = self._get_collection()
        now_iso = datetime.now(timezone.utc).isoformat()
        batch_size = max(1, getattr(settings, "fixture_upsert_batch_size", 100))
        allowed_fields = {k for k in FIXTURE_PROJECTION.keys() if k != "_id"}
        
        # Prepare and project fixture records strictly according to operational schema
        prepared_fixtures: List[Dict[str, Any]] = []
        for fix in fixtures:
            f_id = fix.get("id") or fix.get("source_event_id")
            if not f_id:
                continue
            doc = {k: v for k, v in fix.items() if k in allowed_fields}
            doc["id"] = f_id
            doc["source_system"] = doc.get("source_system") or "predictpro_primary_mongo"
            doc["updated_at"] = now_iso
            if "created_at" not in doc:
                doc["created_at"] = now_iso
            doc["fixture_version"] = doc.get("fixture_version", 1)
            prepared_fixtures.append(doc)

        total_upserted = 0
        # Process batches sequentially to prevent overwhelming MongoDB
        for i in range(0, len(prepared_fixtures), batch_size):
            chunk = prepared_fixtures[i:i + batch_size]
            if not chunk:
                continue
            
            ops = [UpdateOne({"id": doc["id"]}, {"$set": doc}, upsert=True) for doc in chunk]
            try:
                if hasattr(col, "bulk_write"):
                    res = col.bulk_write(ops, ordered=False)
                    upserted = getattr(res, "upserted_count", 0) + getattr(res, "modified_count", 0) + getattr(res, "matched_count", 0)
                    total_upserted += upserted if upserted > 0 else len(chunk)
                else:
                    for doc in chunk:
                        col.update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)
                        total_upserted += 1
            except Exception as bulk_err:
                # Fallback to sequential update_one if bulk_write has driver compatibility issue
                for doc in chunk:
                    try:
                        col.update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)
                        total_upserted += 1
                    except Exception as e:
                        raise DatabaseUnavailableError(f"MongoDB upsert_fixtures failed for {doc['id']}: {e}") from e

        return total_upserted


class MongoPredictionRepository(IPredictionRepository):
    """MongoDB implementation of Prediction repository."""

    def _get_collection(self):
        col = mongo_manager.db["predictions"]
        if col is None:
            raise DatabaseUnavailableError("MongoDB predictions collection unavailable")
        return col

    def _enrich_feed_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Ensures the complete published-feed representation required by FeedService and the React UI."""
        enriched = []
        for item in items:
            doc = dict(item)
            f_id = doc.get("fixture_id") or doc.get("fixtureId") or doc.get("id")
            if f_id:
                if "fixture_id" not in doc:
                    doc["fixture_id"] = f_id
                if "fixtureId" not in doc:
                    doc["fixtureId"] = f_id
                if "id" not in doc:
                    doc["id"] = f_id

            if not doc.get("highestPercentagePrediction") and doc.get("validatedMarkets"):
                first_m = doc["validatedMarkets"][0]
                doc["highestPercentagePrediction"] = {
                    "marketName": first_m.get("marketName") or doc.get("market", ""),
                    "selection": first_m.get("selection") or doc.get("selection", ""),
                    "percentage": first_m.get("probabilityPercentage") or doc.get("percentage") or doc.get("calibratedPercentage", 0.0),
                }

            if doc.get("percentage") is None and doc.get("calibratedPercentage") is not None:
                doc["percentage"] = doc["calibratedPercentage"]
            elif doc.get("calibratedPercentage") is None and doc.get("percentage") is not None:
                doc["calibratedPercentage"] = doc["percentage"]

            if not doc.get("market") and doc.get("highestPercentagePrediction"):
                doc["market"] = doc["highestPercentagePrediction"].get("marketName", "")
            if not doc.get("selection") and doc.get("highestPercentagePrediction"):
                doc["selection"] = doc["highestPercentagePrediction"].get("selection", "")

            if not doc.get("event_date_lagos"):
                k_utc = doc.get("kickoffUtc") or doc.get("scheduled_at")
                if k_utc:
                    from backend.services.feed_service import get_lagos_date_str
                    doc["event_date_lagos"] = get_lagos_date_str(k_utc)

            enriched.append(doc)
        return enriched

    async def get_by_id(self, prediction_id: str) -> Optional[Dict[str, Any]]:
        col = self._get_collection()
        try:
            doc = col.find_one({"$or": [{"id": prediction_id}, {"fixtureId": prediction_id}, {"fixture_id": prediction_id}]}, PREDICTION_PROJECTION)
            if not doc:
                return None
            return self._enrich_feed_items([doc])[0]
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB prediction get_by_id failed: {e}") from e

    async def get_by_fixture_id(self, fixture_id: str) -> List[Dict[str, Any]]:
        col = self._get_collection()
        try:
            cur = col.find({"$or": [{"fixtureId": fixture_id}, {"fixture_id": fixture_id}, {"id": fixture_id}]}, PREDICTION_PROJECTION)
            items = _safe_cursor(cur, limit_num=20)
            return self._enrich_feed_items(items)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB prediction get_by_fixture_id failed: {e}") from e

    async def get_daily_feed(
        self, date_str: str, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        col = self._get_collection()
        query: Dict[str, Any] = {
            "validationStatus": "validated",
            "$or": [
                {"event_date_lagos": date_str},
                {"kickoffUtc": {"$regex": f"^{date_str}"}},
                {"scheduled_at": {"$regex": f"^{date_str}"}},
                {"date": date_str},
            ],
        }
        if sport and sport.lower() != "all":
            query["sport"] = sport.lower()
        if league and league.lower() != "all":
            query["league"] = league
        try:
            cur = col.find(query, PREDICTION_PROJECTION)
            items = _safe_cursor(cur, sort_spec=[("isBestOfDay", -1), ("calibratedPercentage", -1), ("percentage", -1)], limit_num=limit)
            return self._enrich_feed_items(items)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_daily_feed failed: {e}") from e

    async def get_published_feed(
        self, sport: Optional[str] = None, league: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        col = self._get_collection()
        query: Dict[str, Any] = {"validationStatus": "validated"}
        if sport and sport.lower() != "all":
            query["sport"] = sport.lower()
        if league and league.lower() != "all":
            query["league"] = league
        try:
            cur = col.find(query, PREDICTION_PROJECTION)
            items = _safe_cursor(cur, sort_spec="kickoffUtc", limit_num=limit)
            return self._enrich_feed_items(items)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_published_feed failed: {e}") from e

    async def save_predictions(self, predictions: List[Dict[str, Any]]) -> int:
        _assert_mongo_write_permitted()
        if not predictions:
            return 0
        col = self._get_collection()
        now_iso = datetime.now(timezone.utc).isoformat()
        count = 0
        for pred in predictions:
            p_id = pred.get("id")
            if not p_id:
                continue
            pred["source_system"] = pred.get("source_system", "predictpro_primary_mongo")
            pred["updated_at"] = now_iso
            if "created_at" not in pred:
                pred["created_at"] = now_iso
            pred["prediction_version"] = pred.get("prediction_version", 1)
            try:
                col.update_one({"id": p_id}, {"$set": pred}, upsert=True)
                count += 1
            except Exception as e:
                raise DatabaseUnavailableError(f"MongoDB save_predictions failed for {p_id}: {e}") from e
        return count


class MongoPredictionResultRepository(IPredictionResultRepository):
    """MongoDB implementation of Prediction Result repository."""

    def _get_collection(self):
        col = mongo_manager.db["prediction_results"]
        if col is None:
            raise DatabaseUnavailableError("MongoDB prediction_results collection unavailable")
        return col

    async def get_by_id(self, result_id: str) -> Optional[Dict[str, Any]]:
        col = self._get_collection()
        try:
            return col.find_one({"$or": [{"id": result_id}, {"prediction_id": result_id}]}, RESULT_PROJECTION)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB prediction result get_by_id failed: {e}") from e

    async def get_by_fixture_id(self, fixture_id: str) -> List[Dict[str, Any]]:
        col = self._get_collection()
        try:
            cur = col.find({"$or": [{"fixtureId": fixture_id}, {"fixture_id": fixture_id}, {"id": fixture_id}]}, RESULT_PROJECTION)
            return _safe_cursor(cur, limit_num=20)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB prediction result get_by_fixture_id failed: {e}") from e

    async def get_recent_results(self, sport: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        col = self._get_collection()
        query: Dict[str, Any] = {}
        if sport and sport.lower() != "all":
            query["sport"] = sport.lower()
        try:
            cur = col.find(query, RESULT_PROJECTION)
            return _safe_cursor(cur, sort_spec="evaluatedAt", limit_num=limit)
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_recent_results failed: {e}") from e

    async def save_results(self, results: List[Dict[str, Any]]) -> int:
        _assert_mongo_write_permitted()
        if not results:
            return 0
        col = self._get_collection()
        now_iso = datetime.now(timezone.utc).isoformat()
        count = 0
        for res in results:
            r_id = res.get("id") or res.get("prediction_id")
            if not r_id:
                continue
            res["source_system"] = res.get("source_system", "predictpro_primary_mongo")
            res["updated_at"] = now_iso
            if "created_at" not in res:
                res["created_at"] = now_iso
            try:
                col.update_one({"prediction_id": r_id}, {"$set": res}, upsert=True)
                count += 1
            except Exception as e:
                raise DatabaseUnavailableError(f"MongoDB save_results failed for {r_id}: {e}") from e
        return count


class MongoRefreshStateRepository(IRefreshStateRepository):
    """MongoDB implementation of Refresh State repository."""

    def _get_runs_collection(self):
        col = mongo_manager.db["refresh_runs"]
        if col is None:
            raise DatabaseUnavailableError("MongoDB refresh_runs collection unavailable")
        return col

    def _get_freshness_collection(self):
        col = mongo_manager.db["fixture_freshness"]
        if col is None:
            raise DatabaseUnavailableError("MongoDB fixture_freshness collection unavailable")
        return col

    async def save_run(self, run_record: Dict[str, Any]) -> bool:
        _assert_mongo_write_permitted()
        col = self._get_runs_collection()
        r_id = run_record.get("id")
        if not r_id:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        run_record["source_system"] = run_record.get("source_system", "predictpro_primary_mongo")
        run_record["updated_at"] = now_iso
        if "created_at" not in run_record:
            run_record["created_at"] = now_iso
        try:
            col.update_one({"id": r_id}, {"$set": run_record}, upsert=True)
            try:
                sync_col = mongo_manager.db["source_sync_runs"]
                if sync_col is not None:
                    sync_col.update_one({"id": r_id}, {"$set": dict(run_record)}, upsert=True)
            except Exception:
                pass
            return True
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB save_run failed: {e}") from e

    async def get_latest_run(self) -> Optional[Dict[str, Any]]:
        col = self._get_runs_collection()
        try:
            return col.find_one({}, {"_id": 0}, sort=[("timestamp", -1)])
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_latest_run failed: {e}") from e

    async def get_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        col = self._get_runs_collection()
        try:
            return list(col.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_runs failed: {e}") from e

    async def is_sport_date_fresh(self, sport: str, date_str: str) -> bool:
        col = self._get_freshness_collection()
        try:
            doc = col.find_one({"sport": sport, "date": date_str}, {"_id": 0})
            if not doc or "fetched_at" not in doc:
                return False
            # Check 15-minute freshness window
            fetched_at_str = doc["fetched_at"]
            fetched_dt = datetime.fromisoformat(fetched_at_str.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - fetched_dt).total_seconds() < 900
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB is_sport_date_fresh failed: {e}") from e

    async def mark_sport_date_fresh(self, sport: str, date_str: str, fixture_count: int = 0) -> None:
        col = self._get_freshness_collection()
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            col.update_one(
                {"sport": sport, "date": date_str},
                {"$set": {
                    "sport": sport,
                    "date": date_str,
                    "fetched_at": now_iso,
                    "fixture_count": fixture_count,
                    "source_system": "predictpro_primary_mongo",
                    "updated_at": now_iso,
                }},
                upsert=True
            )
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB mark_sport_date_fresh failed: {e}") from e


class MongoModelConfigRepository(IModelConfigRepository):
    """MongoDB implementation of Model Configuration repository."""

    def _get_collection(self):
        col = mongo_manager.db["model_versions"]
        if col is None:
            raise DatabaseUnavailableError("MongoDB model_versions collection unavailable")
        return col

    async def get_active_model(self, sport: str = "football") -> str:
        col = self._get_collection()
        try:
            doc = col.find_one({"key": "active_model"}, {"_id": 0})
            if doc and "model_id" in doc:
                return doc["model_id"]
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_active_model failed: {e}") from e
        return "ELO + POISSON"

    async def set_active_model(self, model: str, sport: str = "football", tier: str = "production") -> Dict[str, Any]:
        _assert_mongo_write_permitted()
        col = self._get_collection()
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "key": "active_model",
            "model_id": model,
            "sport": sport,
            "tier": tier,
            "source_system": "predictpro_primary_mongo",
            "updated_at": now_iso,
            "is_production_ready": tier == "production",
        }
        try:
            col.update_one({"key": "active_model"}, {"$set": payload}, upsert=True)
            return payload
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB set_active_model failed: {e}") from e

    async def get_model_config(self, sport: str = "football") -> Dict[str, Any]:
        col = self._get_collection()
        try:
            doc = col.find_one({"key": "active_model"}, {"_id": 0})
            if doc:
                return doc
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_model_config failed: {e}") from e
        return {
            "key": "active_model",
            "model_id": "ELO + POISSON",
            "sport": sport,
            "tier": "production",
            "is_production_ready": True,
        }


class MongoCalibrationRepository(ICalibrationRepository):
    """MongoDB implementation of Calibration repository."""

    def _get_collection(self):
        col = mongo_manager.db["calibrations"]
        if col is None:
            raise DatabaseUnavailableError("MongoDB calibrations collection unavailable")
        return col

    async def get_calibration(self, sport: str, model_name: str, market_type: str) -> Optional[Dict[str, Any]]:
        col = self._get_collection()
        try:
            return col.find_one(
                {"sport": sport, "model": model_name, "market": market_type},
                {"_id": 0}
            )
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_calibration failed: {e}") from e

    async def save_calibration(
        self, sport: str, model_name: str, market_type: str, data: Dict[str, Any]
    ) -> bool:
        _assert_mongo_write_permitted()
        col = self._get_collection()
        now_iso = datetime.now(timezone.utc).isoformat()
        data["sport"] = sport
        data["model"] = model_name
        data["market"] = market_type
        data["source_system"] = "predictpro_primary_mongo"
        data["updated_at"] = now_iso
        if "created_at" not in data:
            data["created_at"] = now_iso
        try:
            col.update_one(
                {"sport": sport, "model": model_name, "market": market_type},
                {"$set": data},
                upsert=True
            )
            return True
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB save_calibration failed: {e}") from e


class MongoModelGovernanceRepository(IModelGovernanceRepository):
    """MongoDB implementation of Model Governance repository."""

    def _get_collection(self):
        col = mongo_manager.db["model_governance"]
        if col is None:
            raise DatabaseUnavailableError("MongoDB model_governance collection unavailable")
        return col

    async def get_governance_record(self, sport: str, gate_name: str) -> Optional[Dict[str, Any]]:
        col = self._get_collection()
        try:
            return col.find_one({"sport": sport, "gate_name": gate_name}, {"_id": 0})
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_governance_record failed: {e}") from e

    async def save_governance_record(
        self, sport: str, gate_name: str, status: str, criteria: Dict[str, Any], notes: Optional[str] = None
    ) -> bool:
        _assert_mongo_write_permitted()
        col = self._get_collection()
        now_iso = datetime.now(timezone.utc).isoformat()
        doc = {
            "sport": sport,
            "gate_name": gate_name,
            "status": status,
            "criteria_results": criteria,
            "admin_notes": notes or "",
            "source_system": "predictpro_primary_mongo",
            "validated_at": now_iso,
            "updated_at": now_iso,
        }
        try:
            col.update_one(
                {"sport": sport, "gate_name": gate_name},
                {"$set": doc},
                upsert=True
            )
            if "promotion_" in gate_name or status == "PROMOTED":
                cand_id = gate_name.replace("promotion_", "")
                prom_doc = {
                    "candidate_id": cand_id,
                    "sport": sport,
                    "market": criteria.get("market") if isinstance(criteria, dict) else None,
                    "status": status,
                    "promoted_at": now_iso,
                    "gates": criteria,
                }
                if isinstance(criteria, dict) and "market" in criteria:
                    prom_doc["market"] = criteria["market"]
                else:
                    # extract market from notes if available
                    prom_doc["market"] = notes.split("market ")[-1] if (notes and "market " in notes) else "default"
                mongo_manager.model_promotions.update_one({"candidate_id": cand_id}, {"$set": prom_doc}, upsert=True)
            return True
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB save_governance_record failed: {e}") from e

    async def get_all_governance_records(self, sport: Optional[str] = None) -> List[Dict[str, Any]]:
        col = self._get_collection()
        query = {"sport": sport} if sport else {}
        try:
            return list(col.find(query, {"_id": 0}).sort("validated_at", -1).limit(50))
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_all_governance_records failed: {e}") from e


class MongoFeatureRepository(IFeatureRepository):
    """MongoDB operational team feature snapshots repository."""

    def _get_collection(self):
        try:
            return mongo_manager.db["team_feature_snapshots"]
        except Exception:
            return None

    async def get_team_snapshot(
        self, team_name: str, sport: str = "football", as_of: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if not team_name:
            return None
        col = self._get_collection()
        if col is None:
            raise DatabaseUnavailableError("MongoDB team_feature_snapshots collection is unavailable")

        norm_name = team_name.lower().strip()
        query: Dict[str, Any] = {"team_name": norm_name, "sport": sport}
        if as_of:
            query["as_of"] = {"$lte": as_of}

        try:
            doc = col.find_one(query, {"_id": 0}, sort=[("as_of", -1)])
            return doc
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_team_snapshot failed: {e}") from e

    async def get_batch_team_snapshots(
        self, team_names: List[str], sport: str = "football", as_of: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        if not team_names:
            return {}
        col = self._get_collection()
        if col is None:
            raise DatabaseUnavailableError("MongoDB team_feature_snapshots collection is unavailable")

        unique_names = list(set([n.lower().strip() for n in team_names if n and n.strip()]))
        query: Dict[str, Any] = {"team_name": {"$in": unique_names}, "sport": sport}
        if as_of:
            query["as_of"] = {"$lte": as_of}

        result: Dict[str, Dict[str, Any]] = {}
        try:
            if hasattr(col, "find"):
                cur = col.find(query, {"_id": 0})
                if hasattr(cur, "sort") and not isinstance(cur, list):
                    cur = cur.sort("as_of", -1)
                if hasattr(cur, "limit") and not isinstance(cur, list):
                    cur = cur.limit(len(unique_names) * 5)
                docs = list(cur)
            else:
                docs = []
            for doc in docs:
                t_name = doc.get("team_name", "").lower().strip()
                if t_name and t_name not in result:
                    result[t_name] = doc
            return result
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB get_batch_team_snapshots failed: {e}") from e

    async def save_team_snapshot(self, snapshot: Dict[str, Any]) -> bool:
        if not snapshot or "team_name" not in snapshot:
            return False
        _assert_mongo_write_permitted()
        col = self._get_collection()
        if col is None:
            raise DatabaseUnavailableError("MongoDB team_feature_snapshots collection is unavailable")

        t_name = snapshot["team_name"].lower().strip()
        sport = snapshot.get("sport", "football")
        as_of = snapshot.get("as_of") or datetime.now(timezone.utc).isoformat()

        snapshot_copy = dict(snapshot)
        snapshot_copy["team_name"] = t_name
        snapshot_copy["sport"] = sport
        snapshot_copy["as_of"] = as_of
        snapshot_copy["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            col.update_one(
                {"team_name": t_name, "sport": sport, "as_of": as_of},
                {"$set": snapshot_copy},
                upsert=True,
            )
            return True
        except Exception as e:
            raise DatabaseUnavailableError(f"MongoDB save_team_snapshot failed: {e}") from e


class MongoDatabaseAdapter(IDatabaseAdapter):
    """
    MongoDB Atlas Primary Operational Database Adapter.
    """

    def __init__(self):
        self._fixtures = MongoFixtureRepository()
        self._predictions = MongoPredictionRepository()
        self._prediction_results = MongoPredictionResultRepository()
        self._refresh_state = MongoRefreshStateRepository()
        self._model_config = MongoModelConfigRepository()
        self._calibrations = MongoCalibrationRepository()
        self._governance = MongoModelGovernanceRepository()
        self._features = MongoFeatureRepository()

    @property
    def backend_name(self) -> str:
        return "mongodb"

    def check_connection(self) -> Dict[str, Any]:
        """Runs the MongoDB connection check."""
        return mongo_manager.check_connection()

    def is_healthy(self) -> bool:
        health = self.check_connection()
        st = str(health.get("status", "")).upper()
        return st in ("CONNECTED",)

    @property
    def fixtures(self) -> IFixtureRepository:
        return self._fixtures

    @property
    def predictions(self) -> IPredictionRepository:
        return self._predictions

    @property
    def prediction_results(self) -> IPredictionResultRepository:
        return self._prediction_results

    @property
    def refresh_state(self) -> IRefreshStateRepository:
        return self._refresh_state

    @property
    def model_config(self) -> IModelConfigRepository:
        return self._model_config

    @property
    def calibrations(self) -> ICalibrationRepository:
        return self._calibrations

    @property
    def governance(self) -> IModelGovernanceRepository:
        return self._governance

    @property
    def features(self) -> IFeatureRepository:
        return self._features


# Singleton instance
mongodb_adapter = MongoDatabaseAdapter()
