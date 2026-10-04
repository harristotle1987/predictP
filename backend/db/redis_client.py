import time
import json
import logging
from typing import Optional, Dict, Any, List
try:
    import httpx
except ImportError:
    httpx = None
from backend.config import settings

def _get_httpx():
    global httpx
    if httpx is None:
        try:
            import httpx as h
            httpx = h
        except ImportError:
            pass
    return httpx

logger = logging.getLogger("predictpro.redis")

# ---------------------------------------------------------------------------
# Required State Constants
# ---------------------------------------------------------------------------
class RedisState:
    REMOTE_SUCCESS = "REMOTE_SUCCESS"
    REMOTE_FAILURE = "REMOTE_FAILURE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"

REDIS_REMOTE_SUCCESS = RedisState.REMOTE_SUCCESS
REDIS_REMOTE_FAILURE = RedisState.REMOTE_FAILURE
REDIS_DEGRADED = RedisState.DEGRADED
REDIS_UNAVAILABLE = RedisState.UNAVAILABLE

# ---------------------------------------------------------------------------
# Explicit Operation Result
# ---------------------------------------------------------------------------
class RedisOperationResult:
    """
    Standard result returned by UpstashRedisClient operations.
    Differentiates REMOTE_SUCCESS, REMOTE_FAILURE, DEGRADED, and UNAVAILABLE.
    Evaluates to True ONLY if remote write actually succeeded (or explicitly allowed in dev).
    Never allows production failures to appear as successful published-feed writes.
    """
    def __init__(
        self,
        success: bool,
        state: str,
        details: str = "",
        error: Optional[str] = None,
        latency_ms: float = 0.0,
        is_local_fallback: bool = False,
        is_authoritative: bool = False,
        status_code: Optional[int] = None,
    ):
        self.success = success
        self.state = state
        self.status = state  # backwards compatibility alias
        self.details = details
        self.error = error
        self.latency_ms = latency_ms
        self.is_local_fallback = is_local_fallback
        self.is_authoritative = is_authoritative
        self.status_code = status_code

    def __bool__(self) -> bool:
        return self.success

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, bool):
            return self.success == other
        if isinstance(other, str):
            return self.state == other
        return super().__eq__(other)

    def __repr__(self) -> str:
        return f"<RedisOperationResult state={self.state} success={self.success} authoritative={self.is_authoritative} local_fallback={self.is_local_fallback}>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "state": self.state,
            "status": self.state,
            "details": self.details,
            "error": self.error,
            "latencyMs": self.latency_ms,
            "is_local_fallback": self.is_local_fallback,
            "is_authoritative": self.is_authoritative,
            "status_code": self.status_code,
        }

    def __getitem__(self, item: str) -> Any:
        return self.to_dict()[item]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


_SENTINEL = object()

# ---------------------------------------------------------------------------
# Upstash Redis Client
# ---------------------------------------------------------------------------
class UpstashRedisClient:
    def __init__(self, url: Any = _SENTINEL, token: Any = _SENTINEL):
        if url is _SENTINEL:
            self.url = (settings.upstash_redis_rest_url or "").strip() or None
        else:
            self.url = (url.strip() if isinstance(url, str) else None) or None

        if token is _SENTINEL:
            self.token = (settings.upstash_redis_rest_token or "").strip() or None
        else:
            self.token = (token.strip() if isinstance(token, str) else None) or None

        self._is_prod_override: Optional[bool] = None
        # Clearly identified development/test cache: NEVER authoritative for production
        self._dev_test_cache: Dict[str, Any] = {}
        self.last_write_remote: bool = False
        self.last_write_state: str = RedisState.UNAVAILABLE
        self.last_write_result: Optional[RedisOperationResult] = None
        self.last_error: Optional[str] = None

    @property
    def _local_cache(self) -> Dict[str, Any]:
        """Backwards compatibility alias for development/test cache."""
        return self._dev_test_cache

    @_local_cache.setter
    def _local_cache(self, val: Dict[str, Any]):
        self._dev_test_cache = val

    @property
    def is_production(self) -> bool:
        if self._is_prod_override is not None:
            return self._is_prod_override
        env = (settings.environment or "").lower().strip()
        return env in ("production", "prod")

    @is_production.setter
    def is_production(self, val: bool):
        self._is_prod_override = val

    @is_production.deleter
    def is_production(self):
        self._is_prod_override = None

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def check_connection(self) -> Dict[str, Any]:
        """
        Pings Upstash Redis REST and returns connection health diagnostics.
        Explicitly distinguishes REMOTE_SUCCESS, REMOTE_FAILURE, DEGRADED, and UNAVAILABLE.
        """
        if not self.url or not self.token:
            state = RedisState.UNAVAILABLE
            err = "Unconfigured: UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN is missing"
            logger.warning(f"[RedisClient] {err}")
            return {
                "status": "unavailable",
                "state": state,
                "latencyMs": 0.0,
                "details": err,
                "error": err,
                "is_authoritative": False,
                "is_local_fallback": not self.is_production,
                "cacheHitRate": 0,
                "memoryUsedMb": 0,
            }

        h = _get_httpx()
        if h is None:
            return {
                "status": "unavailable",
                "state": RedisState.UNAVAILABLE,
                "latencyMs": 0.0,
                "details": "httpx library is not available",
                "error": "httpx library is not available",
                "is_authoritative": False,
                "is_local_fallback": not self.is_production,
                "cacheHitRate": 0,
                "memoryUsedMb": 0,
            }

        start = time.perf_counter()
        try:
            async with h.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.url.rstrip('/')}/ping", headers=self._headers())
                latency = round((time.perf_counter() - start) * 1000, 1)

                if res.status_code == 200:
                    data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                    pong_msg = data.get("result", res.text)
                    if "PONG" in str(pong_msg).upper():
                        return {
                            "status": "healthy",
                            "state": RedisState.REMOTE_SUCCESS,
                            "latencyMs": latency,
                            "details": "Connected to Upstash Redis REST (REMOTE_SUCCESS)",
                            "error": None,
                            "is_authoritative": True,
                            "is_local_fallback": False,
                            "cacheHitRate": 100 if self.last_write_remote else 0,
                            "memoryUsedMb": 1,
                        }
                    else:
                        state = RedisState.DEGRADED
                        err = f"Unexpected ping response from Redis: {pong_msg}"
                        logger.warning(f"[RedisClient] {err}")
                        return {
                            "status": "degraded",
                            "state": state,
                            "latencyMs": latency,
                            "details": f"Degraded ({err})",
                            "error": err,
                            "is_authoritative": False,
                            "is_local_fallback": False,
                            "cacheHitRate": 0,
                            "memoryUsedMb": 0,
                        }

                elif res.status_code in (401, 403):
                    state = RedisState.REMOTE_FAILURE
                    err = f"Redis authentication failure: HTTP {res.status_code} Unauthorized"
                    logger.error(f"[RedisClient] {err}")
                    return {
                        "status": "failed",
                        "state": state,
                        "latencyMs": latency,
                        "details": err,
                        "error": err,
                        "status_code": res.status_code,
                        "is_authoritative": False,
                        "is_local_fallback": False,
                        "cacheHitRate": 0,
                        "memoryUsedMb": 0,
                    }
                else:
                    state = RedisState.REMOTE_FAILURE
                    err = f"Redis remote failure: HTTP {res.status_code} - {res.text[:80]}"
                    logger.error(f"[RedisClient] {err}")
                    return {
                        "status": "failed",
                        "state": state,
                        "latencyMs": latency,
                        "details": err,
                        "error": err,
                        "status_code": res.status_code,
                        "is_authoritative": False,
                        "is_local_fallback": False,
                        "cacheHitRate": 0,
                        "memoryUsedMb": 0,
                    }

        except Exception as e:
            latency = round((time.perf_counter() - start) * 1000, 1)
            is_timeout = (h and isinstance(e, getattr(h, "TimeoutException", ()))) or "timeout" in str(e).lower()
            state = RedisState.DEGRADED if is_timeout else RedisState.UNAVAILABLE
            err = f"Redis connection {'timed out' if is_timeout else 'unavailable'}: {str(e)}"
            logger.error(f"[RedisClient] {err}")
            return {
                "status": "degraded" if is_timeout else "unavailable",
                "state": state,
                "latencyMs": latency,
                "details": err,
                "error": err,
                "is_authoritative": False,
                "is_local_fallback": not self.is_production,
                "cacheHitRate": 0,
                "memoryUsedMb": 0,
            }

    async def get_json(
        self,
        key: str,
        allow_dev_fallback: Optional[bool] = None,
    ) -> Optional[Any]:
        """
        Reads JSON value from Upstash Redis REST.
        In production, fails closed on remote failures without silently substituting in-memory cache.
        In development/test, allows development cache fallback when Redis is unconfigured or unavailable.
        """
        # Determine whether local dev fallback is permitted
        dev_fallback_allowed = allow_dev_fallback if allow_dev_fallback is not None else (not self.is_production)

        # 1. Unconfigured handling
        if not self.url or not self.token or httpx is None:
            if dev_fallback_allowed:
                return self._dev_test_cache.get(key)
            logger.error(f"[RedisClient] Rejecting get_json('{key}'): Redis unconfigured or httpx missing.")
            return None

        # 2. Remote Redis read
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.url.rstrip('/')}/get/{key}", headers=self._headers())

                if res.status_code == 200:
                    try:
                        data = res.json()
                    except Exception:
                        data = {"result": res.text}

                    val = data.get("result")
                    if val is None or val == "nil" or val == "":
                        # Remote key does not exist
                        return None

                    if isinstance(val, str):
                        try:
                            parsed = json.loads(val)
                            # Keep dev cache in sync for debugging
                            self._dev_test_cache[key] = parsed
                            return parsed
                        except (json.JSONDecodeError, TypeError):
                            self._dev_test_cache[key] = val
                            return val
                    elif isinstance(val, (dict, list)):
                        self._dev_test_cache[key] = val
                        return val
                    else:
                        return val

                elif res.status_code in (401, 403):
                    err = f"Redis authentication failure on get_json('{key}'): HTTP {res.status_code}"
                    logger.error(f"[RedisClient] {err}")
                    if dev_fallback_allowed:
                        return self._dev_test_cache.get(key)
                    return None

                else:
                    err = f"Redis remote error on get_json('{key}'): HTTP {res.status_code}"
                    logger.error(f"[RedisClient] {err}")
                    if dev_fallback_allowed:
                        return self._dev_test_cache.get(key)
                    return None

        except httpx.TimeoutException as e:
            logger.error(f"[RedisClient] Redis timeout reading key '{key}': {e}")
            if dev_fallback_allowed:
                return self._dev_test_cache.get(key)
            return None

        except Exception as e:
            logger.error(f"[RedisClient] Redis unavailable reading key '{key}': {e}")
            if dev_fallback_allowed:
                return self._dev_test_cache.get(key)
            return None

    async def set_json(
        self,
        key: str,
        value: Any,
        ex_seconds: int = 86400,
        fail_closed: Optional[bool] = None,
    ) -> RedisOperationResult:
        """
        Writes JSON payload to Upstash Redis REST.
        CRITICAL: Production Redis failures must NEVER appear as successful published-feed writes.
        Returns a RedisOperationResult distinguishing REMOTE_SUCCESS, REMOTE_FAILURE, DEGRADED, UNAVAILABLE.
        """
        # Determine whether fail-closed is strictly enforced
        enforce_fail_closed = fail_closed if fail_closed is not None else self.is_production

        # 1. Unconfigured handling
        if not self.url or not self.token or httpx is None:
            state = RedisState.UNAVAILABLE
            err = "Redis is unconfigured (UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN missing)"
            self.last_write_remote = False
            self.last_write_state = state
            self.last_error = err

            if enforce_fail_closed:
                logger.error(f"[RedisClient] Cannot publish production feed to '{key}': {err}")
                res = RedisOperationResult(
                    success=False,
                    state=state,
                    details=err,
                    error=err,
                    is_local_fallback=False,
                    is_authoritative=False,
                )
                self.last_write_result = res
                return res

            # Development/test mode: cache in dev cache, but clearly label as local fallback
            self._dev_test_cache[key] = value
            logger.info(f"[RedisClient] Stored key '{key}' in local dev/test cache (unconfigured remote).")
            res = RedisOperationResult(
                success=True,
                state=state,
                details="Cached in development/test in-memory cache only",
                error=err,
                is_local_fallback=True,
                is_authoritative=False,
            )
            self.last_write_result = res
            return res

        # 2. Remote Redis write
        start = time.perf_counter()
        try:
            payload = json.dumps(value) if not isinstance(value, str) else value
            async with httpx.AsyncClient(timeout=3.0) as client:
                endpoint = f"{self.url.rstrip('/')}/set/{key}"
                if ex_seconds and ex_seconds > 0:
                    endpoint += f"?ex={ex_seconds}"

                res = await client.post(
                    endpoint,
                    content=payload,
                    headers=self._headers(),
                )
                latency = round((time.perf_counter() - start) * 1000, 1)

                if res.status_code == 200:
                    # Successful authoritative remote write
                    self._dev_test_cache[key] = value
                    self.last_write_remote = True
                    self.last_write_state = RedisState.REMOTE_SUCCESS
                    self.last_error = None

                    op_res = RedisOperationResult(
                        success=True,
                        state=RedisState.REMOTE_SUCCESS,
                        details="Successfully published to Upstash Redis REST (REMOTE_SUCCESS)",
                        error=None,
                        latency_ms=latency,
                        is_local_fallback=False,
                        is_authoritative=True,
                        status_code=200,
                    )
                    self.last_write_result = op_res
                    return op_res

                elif res.status_code in (401, 403):
                    state = RedisState.REMOTE_FAILURE
                    err = f"Redis authentication failure on set_json('{key}'): HTTP {res.status_code} Unauthorized"
                    logger.error(f"[RedisClient] {err}")

                    self.last_write_remote = False
                    self.last_write_state = state
                    self.last_error = err

                    op_res = RedisOperationResult(
                        success=False,
                        state=state,
                        details=err,
                        error=err,
                        latency_ms=latency,
                        is_local_fallback=False,
                        is_authoritative=False,
                        status_code=res.status_code,
                    )
                    self.last_write_result = op_res
                    return op_res

                else:
                    state = RedisState.REMOTE_FAILURE
                    err = f"Redis remote failure on set_json('{key}'): HTTP {res.status_code} - {res.text[:100]}"
                    logger.error(f"[RedisClient] {err}")

                    self.last_write_remote = False
                    self.last_write_state = state
                    self.last_error = err

                    op_res = RedisOperationResult(
                        success=False,
                        state=state,
                        details=err,
                        error=err,
                        latency_ms=latency,
                        is_local_fallback=False,
                        is_authoritative=False,
                        status_code=res.status_code,
                    )
                    self.last_write_result = op_res
                    return op_res

        except httpx.TimeoutException as e:
            latency = round((time.perf_counter() - start) * 1000, 1)
            state = RedisState.DEGRADED
            err = f"Redis write timed out after 3.0s for key '{key}': {str(e)}"
            logger.error(f"[RedisClient] {err}")

            self.last_write_remote = False
            self.last_write_state = state
            self.last_error = err

            op_res = RedisOperationResult(
                success=False,
                state=state,
                details=err,
                error=err,
                latency_ms=latency,
                is_local_fallback=False,
                is_authoritative=False,
            )
            self.last_write_result = op_res
            return op_res

        except Exception as e:
            latency = round((time.perf_counter() - start) * 1000, 1)
            state = RedisState.UNAVAILABLE
            err = f"Redis write unavailable for key '{key}': {str(e)}"
            logger.error(f"[RedisClient] {err}")

            self.last_write_remote = False
            self.last_write_state = state
            self.last_error = err

            op_res = RedisOperationResult(
                success=False,
                state=state,
                details=err,
                error=err,
                latency_ms=latency,
                is_local_fallback=False,
                is_authoritative=False,
            )
            self.last_write_result = op_res
            return op_res

    async def delete_key(self, key: str) -> RedisOperationResult:
        """
        Deletes a key from remote Upstash Redis REST and local dev cache.
        """
        self._dev_test_cache.pop(key, None)
        if not self.url or not self.token:
            return RedisOperationResult(
                success=True,
                state=RedisState.UNAVAILABLE,
                details="Deleted from dev/test cache only",
                is_local_fallback=True,
            )

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.url.rstrip('/')}/del/{key}", headers=self._headers())
                success = (res.status_code == 200)
                state = RedisState.REMOTE_SUCCESS if success else RedisState.REMOTE_FAILURE
                return RedisOperationResult(
                    success=success,
                    state=state,
                    status_code=res.status_code,
                    is_authoritative=success,
                )
        except Exception as e:
            logger.error(f"[RedisClient] Error deleting key '{key}': {e}")
            return RedisOperationResult(
                success=False,
                state=RedisState.UNAVAILABLE,
                error=str(e),
                is_authoritative=False,
            )

    async def get_keys(self, pattern: str = "predictpro:feed:*") -> list:
        """
        Returns keys matching pattern from remote Upstash Redis REST,
        with local dev/test keys included when running in development.
        """
        import fnmatch
        keys = set()
        for k in self._dev_test_cache.keys():
            if fnmatch.fnmatch(k, pattern):
                keys.add(k)

        if not self.url or not self.token:
            return list(keys)

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.url.rstrip('/')}/keys/{pattern}", headers=self._headers())
                if res.status_code == 200:
                    data = res.json()
                    res_keys = data.get("result", [])
                    if isinstance(res_keys, list):
                        for k in res_keys:
                            keys.add(str(k))
        except Exception as e:
            logger.error(f"[RedisClient] Error scanning keys '{pattern}': {e}")

        return list(keys)


redis_client = UpstashRedisClient()
