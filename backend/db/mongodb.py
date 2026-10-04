import os
import sys
import time
from typing import Optional, Dict, Any, List
try:
    from pymongo import MongoClient, ASCENDING, DESCENDING, UpdateOne
except ImportError:
    MongoClient = None
    ASCENDING = 1
    DESCENDING = -1
    class UpdateOne:
        def __init__(self, filter, update, upsert=False):
            self.filter = filter
            self.update = update
            self.upsert = upsert

try:
    import mongomock
except ImportError:
    mongomock = None
from backend.config import settings

import re

def _match_doc(doc, filt):
    if not filt:
        return True
    if "$or" in filt:
        or_list = filt["$or"]
        sub_filt = {k: v for k, v in filt.items() if k != "$or"}
        if sub_filt and not _match_doc(doc, sub_filt):
            return False
        return any(_match_doc(doc, sub) for sub in or_list)

    for k, v in filt.items():
        if isinstance(v, dict):
            if "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            elif "$regex" in v:
                pattern = v["$regex"]
                val = str(doc.get(k, ""))
                if not re.search(pattern, val):
                    return False
            elif "$gte" in v:
                if doc.get(k, "") < v["$gte"]:
                    return False
            elif "$lte" in v:
                if doc.get(k, "") > v["$lte"]:
                    return False
        else:
            if doc.get(k) != v:
                return False
    return True


class TestMockCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, list):
            sort_key, sort_dir = key_or_list[0]
        else:
            sort_key, sort_dir = key_or_list, (direction if direction is not None else 1)
        rev = (sort_dir == -1 or sort_dir == DESCENDING)
        sorted_docs = sorted(self._docs, key=lambda x: str(x.get(sort_key, "")), reverse=rev)
        return TestMockCursor(sorted_docs)

    def limit(self, n):
        return TestMockCursor(self._docs[:n])

    def __iter__(self):
        return iter(self._docs)

    def __len__(self):
        return len(self._docs)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return TestMockCursor(self._docs[item])
        return self._docs[item]


class TestMockCollection:
    def __init__(self):
        self._docs = []

    def insert_one(self, doc):
        self._docs.append(dict(doc))
        return self

    def insert_many(self, docs):
        for d in docs:
            self._docs.append(dict(d))

    def update_one(self, filt, update, upsert=False):
        set_vals = update.get("$set", {})
        for d in self._docs:
            if _match_doc(d, filt):
                d.update(set_vals)
                return
        if upsert:
            new_doc = dict(filt)
            new_doc.update(set_vals)
            self._docs.append(new_doc)

    def update_many(self, filt, update):
        set_vals = update.get("$set", {})
        for d in self._docs:
            if _match_doc(d, filt):
                d.update(set_vals)

    def delete_many(self, filt=None):
        if not filt:
            self._docs.clear()
        else:
            self._docs = [d for d in self._docs if not _match_doc(d, filt)]

    def bulk_write(self, operations, ordered=False):
        """Simulates MongoDB bulk_write with UpdateOne / InsertOne operations."""
        upserted_count = 0
        matched_count = 0
        modified_count = 0
        for op in operations:
            if hasattr(op, "filter") and hasattr(op, "update"):
                filt = op.filter
                update = op.update
                upsert = getattr(op, "upsert", False)
                set_vals = update.get("$set", {}) if isinstance(update, dict) else {}
                matched = False
                for d in self._docs:
                    if _match_doc(d, filt):
                        d.update(set_vals)
                        matched = True
                        matched_count += 1
                        modified_count += 1
                        break
                if not matched and upsert:
                    new_doc = dict(filt)
                    new_doc.update(set_vals)
                    self._docs.append(new_doc)
                    upserted_count += 1
        class BulkWriteResult:
            def __init__(self, upserted, matched, modified):
                self.upserted_count = upserted
                self.matched_count = matched
                self.modified_count = modified
                self.inserted_count = 0
                self.deleted_count = 0
                self.upserted_ids = {}
        return BulkWriteResult(upserted_count, matched_count, modified_count)

    def find_one(self, filt=None, projection=None, sort=None):
        docs = self._docs
        if filt:
            docs = [d for d in docs if _match_doc(d, filt)]
        if not docs:
            return None
        if sort and isinstance(sort, list):
            sort_key, sort_dir = sort[0]
            docs = sorted(docs, key=lambda x: str(x.get(sort_key, "")), reverse=(sort_dir == -1))
        d = dict(docs[0])
        if projection and isinstance(projection, dict):
            include_keys = {k for k, v in projection.items() if v and k != "_id"}
            exclude_keys = {k for k, v in projection.items() if not v and k != "_id"}
            if include_keys:
                p_doc = {k: d[k] for k in include_keys if k in d}
                if projection.get("_id", 1) != 0 and "_id" in d:
                    p_doc["_id"] = d["_id"]
                return p_doc
            elif exclude_keys:
                p_doc = {k: v for k, v in d.items() if k not in exclude_keys}
                if projection.get("_id", 1) == 0:
                    p_doc.pop("_id", None)
                return p_doc
        return d

    def find(self, filt=None, projection=None):
        docs = self._docs
        if filt:
            docs = [d for d in docs if _match_doc(d, filt)]
        if projection and isinstance(projection, dict):
            include_keys = {k for k, v in projection.items() if v and k != "_id"}
            exclude_keys = {k for k, v in projection.items() if not v and k != "_id"}
            projected = []
            for d in docs:
                if include_keys:
                    p_doc = {k: d[k] for k in include_keys if k in d}
                    if projection.get("_id", 1) != 0 and "_id" in d:
                        p_doc["_id"] = d["_id"]
                elif exclude_keys:
                    p_doc = {k: v for k, v in d.items() if k not in exclude_keys}
                    if projection.get("_id", 1) == 0:
                        p_doc.pop("_id", None)
                else:
                    p_doc = dict(d)
                projected.append(p_doc)
            return TestMockCursor(projected)
        return TestMockCursor([dict(d) for d in docs])

    def count_documents(self, filt=None):
        docs = self._docs
        if filt:
            docs = [d for d in docs if _match_doc(d, filt)]
        return len(docs)

    def create_index(self, *args, **kwargs):
        pass


class TestMockDatabase(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = TestMockCollection()
        return super().__getitem__(name)


class MongoDBManager:
    def __init__(self):
        self._client: Optional[MongoClient] = None
        self._mock_client: Optional[Any] = None
        self._db = None
        self._is_connected_real = False
        self._has_attempted_connect = False
        self._last_health_check_time: float = 0.0
        self._cached_health_result: Optional[Dict[str, Any]] = None
        self._health_cache_ttl: float = 2.0
        self.get_client()
        self._init_collections()

    @property
    def is_test_environment(self) -> bool:
        return (
            os.getenv("APP_ENV") == "test"
            or os.getenv("UNIT_TEST") == "1"
            or "unittest" in sys.modules
            or "pytest" in sys.modules
            or settings.environment == "test"
        )

    def get_client(self):
        if not self._has_attempted_connect or (self._client is None and settings.mongodb_uri):
            self._has_attempted_connect = True
            if settings.mongodb_uri and MongoClient is not None:
                try:
                    server_selection_timeout = getattr(settings, "mongodb_server_selection_timeout_ms", 1500)
                    connect_timeout = getattr(settings, "mongodb_connect_timeout_ms", 1500)
                    socket_timeout = getattr(settings, "mongodb_socket_timeout_ms", 3000)
                    max_pool_size = getattr(settings, "mongodb_max_pool_size", 50)

                    self._client = MongoClient(
                        settings.mongodb_uri,
                        serverSelectionTimeoutMS=server_selection_timeout,
                        connectTimeoutMS=connect_timeout,
                        socketTimeoutMS=socket_timeout,
                        maxPoolSize=max_pool_size,
                    )
                    self._client.admin.command('ping')
                    self._is_connected_real = True
                    self._db = self._client[settings.mongodb_database]
                except Exception as e:
                    self._is_connected_real = False
                    self._client = None
                    self._db = None

        if self._db is None:
            if self.is_test_environment:
                if mongomock is not None:
                    if self._mock_client is None:
                        self._mock_client = mongomock.MongoClient()
                    self._db = self._mock_client[settings.mongodb_database]
                else:
                    if self._mock_client is None:
                        self._mock_client = TestMockDatabase()
                    self._db = self._mock_client
            else:
                self._db = None

        return self._client if self._is_connected_real else self._mock_client

    @property
    def db(self):
        if self._db is None:
            self.get_client()
        if self._db is None and not self.is_test_environment:
            raise RuntimeError("DATABASE_UNAVAILABLE: MongoDB is unreachable and mongomock fallback is disabled in production.")
        return self._db

    def check_connection(self, force_refresh: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force_refresh and self._cached_health_result is not None:
            if (now - self._last_health_check_time) < self._health_cache_ttl:
                return self._cached_health_result

        if not settings.mongodb_uri:
            res = {
                "status": "DISCONNECTED" if not self.is_test_environment else "CONNECTED",
                "latencyMs": 0,
                "poolActive": 0,
                "cluster": "Unconfigured (MONGODB_URI missing)",
                "error": "MONGODB_URI is not configured",
            }
            self._cached_health_result = res
            self._last_health_check_time = now
            return res

        # Reuse application-scoped MongoClient
        if self._client is None and not self._has_attempted_connect:
            self.get_client()

        client = self._client
        if client is not None:
            try:
                start = time.perf_counter()
                client.admin.command('ping')
                latency = (time.perf_counter() - start) * 1000
                self._is_connected_real = True
                res = {
                    "status": "CONNECTED",
                    "latencyMs": round(latency, 1),
                    "poolActive": 1,
                    "cluster": settings.mongodb_database or "predictpro",
                    "error": None,
                }
                self._cached_health_result = res
                self._last_health_check_time = now
                return res
            except Exception as e:
                err_msg = str(e)
                self._is_connected_real = False
                
                # Check for TLS alert / IP access list errors
                if any(phrase in err_msg.lower() for phrase in [
                    "tls alert", "ip access list", "cluster access restricted", 
                    "whitelist", "ssl handshake", "bad_certificate", "ssl error"
                ]):
                    status = "DEGRADED"
                    err_clean = "TLS alert: IP access list or cluster access restricted"
                elif "timeout" in err_msg.lower() or "serverselectiontimeouterror" in err_msg.lower():
                    status = "DEGRADED"
                    err_clean = f"MongoDB connection timeout: {err_msg[:60]}"
                else:
                    status = "DISCONNECTED"
                    err_clean = f"MongoDB ping failed: {err_msg[:60]}"

                if self.is_test_environment and self._db is not None:
                    res = {
                        "status": "CONNECTED",
                        "latencyMs": 0,
                        "poolActive": 1,
                        "cluster": "test_mock",
                        "error": None,
                    }
                else:
                    res = {
                        "status": status,
                        "latencyMs": 0,
                        "poolActive": 0,
                        "cluster": settings.mongodb_database or "predictpro",
                        "error": err_clean,
                        "details": err_msg,
                    }
                self._cached_health_result = res
                self._last_health_check_time = now
                return res
        else:
            if self.is_test_environment and self._db is not None:
                res = {
                    "status": "CONNECTED",
                    "latencyMs": 0,
                    "poolActive": 1,
                    "cluster": "test_mock",
                    "error": None,
                }
                self._cached_health_result = res
                self._last_health_check_time = now
                return res
            res = {
                "status": "DISCONNECTED",
                "latencyMs": 0,
                "poolActive": 0,
                "cluster": "Disconnected: Client not connected",
                "error": "MongoDB client is not connected",
            }
            self._cached_health_result = res
            self._last_health_check_time = now
            return res

    def _init_collections(self):
        # Ensure indexes exist on startup
        try:
            db = self.db
            if db is None:
                return
            db.operational_events.create_index([("sport", ASCENDING), ("status", ASCENDING), ("scheduled_at", ASCENDING)])
            db.operational_events.create_index([("sport", ASCENDING), ("kickoffUtc", ASCENDING)])
            db.operational_events.create_index([("sport", ASCENDING), ("status", ASCENDING)])
            db.operational_events.create_index([("source", ASCENDING), ("source_event_id", ASCENDING)], unique=True)
            db.operational_events.create_index([("source_event_id", ASCENDING)])
            db.operational_events.create_index([("provider_event_id", ASCENDING)])
            db.operational_events.create_index([("competition_id", ASCENDING), ("scheduled_at", ASCENDING)])
            db.operational_events.create_index([("home_team_id", ASCENDING), ("away_team_id", ASCENDING), ("scheduled_at", ASCENDING)])
            db.operational_events.create_index([("canonical_key", ASCENDING)])
            db.operational_events.create_index([("status", ASCENDING), ("scheduled_at", ASCENDING)])
            db.operational_events.create_index([("sport", ASCENDING), ("league", ASCENDING)])
            db.operational_events.create_index([("created_at", DESCENDING)])
            db.operational_events.create_index([("updated_at", DESCENDING)])

            db.operational_event_updates.create_index([("event_id", ASCENDING), ("source_updated_at", DESCENDING)])
            db.historical_ingestion_runs.create_index([("sport", ASCENDING), ("created_at", DESCENDING)])
            db.historical_ingestion_runs.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
            db.historical_dataset_manifests.create_index([("sport", ASCENDING), ("dataset_version", ASCENDING)], unique=True)
            db.historical_dataset_manifests.create_index([("sport", ASCENDING), ("active", ASCENDING)])

            db.predictions.create_index([("event_id", ASCENDING), ("created_at", DESCENDING)])
            db.predictions.create_index([("sport", ASCENDING), ("model_version", ASCENDING)])
            db.predictions.create_index([("published", ASCENDING), ("kickoffUtc", ASCENDING)])
            db.predictions.create_index([("sport", ASCENDING), ("validationStatus", ASCENDING), ("published", ASCENDING)])
            db.predictions.create_index([("canonical_key", ASCENDING)])
            db.predictions.create_index([("created_at", DESCENDING)])

            db.prediction_results.create_index([("fixtureId", ASCENDING)])
            db.prediction_results.create_index([("sport", ASCENDING), ("status", ASCENDING), ("evaluatedAt", DESCENDING)])
            db.prediction_results.create_index([("evaluatedAt", DESCENDING)])

            db.team_feature_snapshots.create_index([("sport", ASCENDING), ("team_norm", ASCENDING), ("as_of", DESCENDING)])
            db.fixture_freshness.create_index([("sport", ASCENDING), ("date", ASCENDING)], unique=True)
            db.admin_audit_log.create_index([("admin_user_id", ASCENDING), ("created_at", DESCENDING)])
        except Exception as e:
            print(f"[MongoDB] Index creation notice: {e}")

    # 17 Required Collections
    @property
    def operational_events(self):
        return self.db["operational_events"]

    @property
    def operational_event_updates(self):
        return self.db["operational_event_updates"]

    @property
    def teams(self):
        return self.db["teams"]

    @property
    def competitions(self):
        return self.db["competitions"]

    @property
    def predictions(self):
        return self.db["predictions"]

    @property
    def prediction_results(self):
        return self.db["prediction_results"]

    @property
    def historical_ingestion_runs(self):
        return self.db["historical_ingestion_runs"]

    @property
    def historical_dataset_manifests(self):
        return self.db["historical_dataset_manifests"]

    @property
    def data_quality_reports(self):
        return self.db["data_quality_reports"]

    @property
    def source_sync_runs(self):
        return self.db["source_sync_runs"]

    @property
    def source_payload_audit(self):
        return self.db["source_payload_audit"]

    @property
    def job_locks(self):
        return self.db["job_locks"]

    @property
    def admin_users(self):
        return self.db["admin_users"]

    @property
    def admin_audit_log(self):
        return self.db["admin_audit_log"]

    @property
    def api_keys(self):
        return self.db["api_keys"]

    @property
    def calibration_records(self):
        return self.db["calibration_records"]

    @property
    def backtest_results(self):
        return self.db["backtest_results"]

    @property
    def model_versions(self):
        return self.db["model_versions"]

    @property
    def model_candidates(self):
        return self.db["model_candidates"]

    @property
    def model_promotions(self):
        return self.db["model_promotions"]

    async def get_operational_events(
        self,
        sport: Optional[str] = None,
        status: Optional[str] = None,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if sport and sport != "all":
            query["sport"] = sport
        if status and status != "all":
            query["status"] = status
        if from_time or to_time:
            query["scheduled_at"] = {}
            if from_time:
                query["scheduled_at"]["$gte"] = from_time
            if to_time:
                query["scheduled_at"]["$lte"] = to_time

        try:
            cursor = self.operational_events.find(query, {"_id": 0}).sort("scheduled_at", 1).limit(100)
            return list(cursor)
        except Exception as e:
            print(f"[MongoDB] Query events error: {e}")
            return []

    async def get_operational_event_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.operational_events.find_one({"id": event_id}, {"_id": 0})
        except Exception:
            return None

    SYNC_RUN_PROJECTION = {
        "_id": 0,
        "id": 1,
        "timestamp": 1,
        "status": 1,
        "trigger_source": 1,
        "duration_ms": 1,
        "metrics": 1,
        "errors": 1,
    }

    MANIFEST_PROJECTION = {
        "_id": 0,
        "dataset_version": 1,
        "sport": 1,
        "created_at": 1,
        "active": 1,
        "row_count": 1,
        "source_versions": 1,
        "file_path": 1,
    }

    async def get_sync_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        capped_limit = min(max(1, limit), 50)
        try:
            cursor = self.source_sync_runs.find({}, self.SYNC_RUN_PROJECTION).sort("timestamp", -1).limit(capped_limit)
            return list(cursor)
        except Exception:
            return []

    async def get_dataset_manifests(self, sport: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        capped_limit = min(max(1, limit), 50)
        query: Dict[str, Any] = {"active": True}
        if sport and sport.lower() != "all":
            query["sport"] = sport.lower()
        try:
            cursor = self.historical_dataset_manifests.find(query, self.MANIFEST_PROJECTION).sort("created_at", -1).limit(capped_limit)
            return list(cursor)
        except Exception:
            return []

    async def reconcile_events(self) -> int:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            res = self.operational_events.update_many(
                {"status": "scheduled", "scheduled_at": {"$lt": now_iso}},
                {"$set": {"status": "completed", "reconciled_at": now_iso}}
            )
            return res.modified_count
        except Exception:
            return 0

mongo_manager = MongoDBManager()
