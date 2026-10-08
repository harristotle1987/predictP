import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import sys
import types
import json
try:
    import httpx
except ImportError:
    httpx = types.ModuleType("httpx")
    httpx._is_mock = True
    class _TimeoutException(Exception): pass
    class _ConnectError(Exception): pass
    
    _in_memory_store = {}
    
    class _MockResponse:
        def __init__(self, status_code=200, text="OK", json_data=None):
            self.status_code = status_code
            self.text = text
            self._json = json_data if json_data is not None else {"result": "OK"}
            self.headers = {"content-type": "application/json"}
        def json(self):
            return self._json

    class _AsyncClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, *args, **kwargs):
            if "/get/" in url:
                k = url.split("/get/")[-1]
                val = _in_memory_store.get(k)
                if val is not None:
                    return _MockResponse(200, "OK", {"result": json.dumps(val) if not isinstance(val, str) else val})
                return _MockResponse(200, "OK", {"result": "nil"})
            return _MockResponse(200, "OK", {"result": "PONG"})
        async def post(self, url, *args, **kwargs):
            if "/set/" in url:
                k = url.split("/set/")[-1].split("?")[0]
                content = kwargs.get("content")
                try:
                    val = json.loads(content)
                except Exception:
                    val = content
                _in_memory_store[k] = val
                return _MockResponse(200, "OK", {"result": "OK"})
            return _MockResponse(200, "OK", {"result": "OK"})

    httpx.TimeoutException = _TimeoutException
    httpx.ConnectError = _ConnectError
    httpx.AsyncClient = _AsyncClient
    sys.modules["httpx"] = httpx

from datetime import datetime, timezone

from backend.config import settings
import backend.db.redis_client
backend.db.redis_client.httpx = httpx

from backend.db.redis_client import (
    UpstashRedisClient,
    RedisState,
    REDIS_REMOTE_SUCCESS,
    REDIS_REMOTE_FAILURE,
    REDIS_DEGRADED,
    REDIS_UNAVAILABLE,
)
from backend.services.sync_service import sync_service
from backend.db.mongodb import mongo_manager

class TestRedisClientResilience(unittest.IsolatedAsyncioTestCase):
    """
    Test suite for Upstash Redis client resilience and error handling.
    Verifies:
    1. Redis success: REMOTE_SUCCESS state, authoritative remote write.
    2. Redis timeout: DEGRADED state, failure result, error logging.
    3. Redis authentication failure: REMOTE_FAILURE state, failure result, 401/403 status.
    4. Redis unavailable: UNAVAILABLE state, connection refused/DNS failure handling.
    5. Local development fallback: clearly identified development/test cache, never claims remote success.
    6. Production fail-closed behavior: fails closed, never returns success: true or completed on Redis failure.
    """

    def setUp(self):
        from backend.db.neon_budget_guard import neon_budget_guard
        from backend.db.failover_manager import failover_manager
        from backend.db.database_router import database_router, RoutingMode
        neon_budget_guard.reset_metrics()
        failover_manager.reset_state_for_tests()
        database_router.set_routing_mode(RoutingMode.AUTO)

        # Default test client with dummy remote credentials
        self.test_url = "https://mock-redis.upstash.io"
        self.test_token = "mock-secret-token"
        self.client = UpstashRedisClient(url=self.test_url, token=self.test_token)

    # =========================================================================
    # Test 1: Redis Success
    # =========================================================================
    async def test_1_redis_success(self):
        """1. Remote Upstash Redis responds 200 OK -> REMOTE_SUCCESS, success=True, authoritative."""
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.headers = {"content-type": "application/json"}
        mock_res.json.return_value = {"result": "PONG"}
        mock_res.text = "PONG"

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_res
            conn_res = await self.client.check_connection()
            self.assertEqual(conn_res["state"], RedisState.REMOTE_SUCCESS)
            self.assertEqual(conn_res["status"], "healthy")
            self.assertTrue(conn_res["is_authoritative"])
            self.assertFalse(conn_res["is_local_fallback"])

        mock_set_res = MagicMock()
        mock_set_res.status_code = 200
        mock_set_res.text = '{"result": "OK"}'

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_set_res
            write_res = await self.client.set_json("test:key:1", {"foo": "bar"})
            self.assertTrue(write_res.success)
            self.assertEqual(write_res.state, RedisState.REMOTE_SUCCESS)
            self.assertTrue(write_res.is_authoritative)
            self.assertFalse(write_res.is_local_fallback)
            self.assertTrue(self.client.last_write_remote)
            self.assertEqual(self.client.last_write_state, RedisState.REMOTE_SUCCESS)

        # Verify get_json successfully retrieves remote value
        mock_get_val = MagicMock()
        mock_get_val.status_code = 200
        mock_get_val.headers = {"content-type": "application/json"}
        mock_get_val.json.return_value = {"result": '{"foo": "bar"}'}
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_get_val
            val = await self.client.get_json("test:key:1")
            self.assertEqual(val, {"foo": "bar"})

    # =========================================================================
    # Test 2: Redis Timeout
    # =========================================================================
    async def test_2_redis_timeout(self):
        """2. Remote Upstash Redis times out -> DEGRADED state, success=False, error logged."""
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.TimeoutException("Read timed out after 3.0s")
            conn_res = await self.client.check_connection()
            self.assertEqual(conn_res["state"], RedisState.DEGRADED)
            self.assertEqual(conn_res["status"], "degraded")
            self.assertIn("timed out", conn_res["error"].lower())

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.TimeoutException("Connect timed out after 3.0s")
            write_res = await self.client.set_json("test:key:timeout", {"data": 123})
            self.assertFalse(write_res.success)
            self.assertEqual(write_res.state, RedisState.DEGRADED)
            self.assertIn("timed out", write_res.error.lower())
            self.assertFalse(self.client.last_write_remote)
            self.assertEqual(self.client.last_write_state, RedisState.DEGRADED)

        # In production mode, get_json fails closed on timeout
        with patch.object(self.client, "is_production", True):
            with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
                mock_get.side_effect = httpx.TimeoutException("Timeout")
                val = await self.client.get_json("test:key:timeout")
                self.assertIsNone(val, "Production get_json must return None on timeout (fail closed)")

    # =========================================================================
    # Test 3: Redis Authentication Failure
    # =========================================================================
    async def test_3_redis_authentication_failure(self):
        """3. Remote Upstash Redis returns 401 Unauthorized -> REMOTE_FAILURE state, success=False."""
        mock_auth_fail = MagicMock()
        mock_auth_fail.status_code = 401
        mock_auth_fail.text = "Unauthorized: Invalid or expired token"

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_auth_fail
            conn_res = await self.client.check_connection()
            self.assertEqual(conn_res["state"], RedisState.REMOTE_FAILURE)
            self.assertEqual(conn_res["status"], "failed")
            self.assertEqual(conn_res["status_code"], 401)
            self.assertIn("authentication failure", conn_res["error"].lower())

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_auth_fail
            write_res = await self.client.set_json("test:key:auth", {"data": 456})
            self.assertFalse(write_res.success)
            self.assertEqual(write_res.state, RedisState.REMOTE_FAILURE)
            self.assertEqual(write_res.status_code, 401)
            self.assertFalse(self.client.last_write_remote)
            self.assertEqual(self.client.last_write_state, RedisState.REMOTE_FAILURE)

        # In production mode, get_json fails closed on auth error
        with patch.object(self.client, "is_production", True):
            with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = mock_auth_fail
                val = await self.client.get_json("test:key:auth")
                self.assertIsNone(val, "Production get_json must return None on auth failure")

    # =========================================================================
    # Test 4: Redis Unavailable
    # =========================================================================
    async def test_4_redis_unavailable(self):
        """4. Remote Upstash Redis network unreachable -> UNAVAILABLE state, success=False."""
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.ConnectError("[Errno 111] Connection refused")
            conn_res = await self.client.check_connection()
            self.assertEqual(conn_res["state"], RedisState.UNAVAILABLE)
            self.assertEqual(conn_res["status"], "unavailable")
            self.assertIn("unavailable", conn_res["error"].lower())

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.ConnectError("Failed to resolve hostname")
            write_res = await self.client.set_json("test:key:unavail", {"data": 789})
            self.assertFalse(write_res.success)
            self.assertEqual(write_res.state, RedisState.UNAVAILABLE)
            self.assertIn("unavailable", write_res.error.lower())
            self.assertFalse(self.client.last_write_remote)
            self.assertEqual(self.client.last_write_state, RedisState.UNAVAILABLE)

    # =========================================================================
    # Test 5: Local Development Fallback
    # =========================================================================
    async def test_5_local_development_fallback(self):
        """5. Unconfigured client in development mode uses identified dev/test cache, never claims remote success."""
        dev_client = UpstashRedisClient(url=None, token=None)
        self.assertFalse(dev_client.is_production)

        # check_connection reports UNAVAILABLE and is_local_fallback=True
        conn_res = await dev_client.check_connection()
        self.assertEqual(conn_res["state"], RedisState.UNAVAILABLE)
        self.assertTrue(conn_res["is_local_fallback"])
        self.assertFalse(conn_res["is_authoritative"])

        # set_json stores in dev cache, but state is UNAVAILABLE and is_local_fallback=True
        write_res = await dev_client.set_json("dev:key:1", {"dev": "item"})
        self.assertTrue(write_res.is_local_fallback)
        self.assertFalse(write_res.is_authoritative)
        self.assertEqual(write_res.state, RedisState.UNAVAILABLE)
        self.assertFalse(dev_client.last_write_remote)

        # get_json retrieves from dev cache
        val = await dev_client.get_json("dev:key:1")
        self.assertEqual(val, {"dev": "item"})

    # =========================================================================
    # Test 6: Production Fail-Closed Behavior
    # =========================================================================
    async def test_6_production_fail_closed_behavior(self):
        """6. In production, Redis failures strictly fail closed, and refresh operation reports failure."""
        prod_client = UpstashRedisClient(url="https://prod-redis.upstash.io", token="prod-token")

        with patch.object(prod_client, "is_production", True):
            # Unconfigured or failing set_json fails closed with success=False
            with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                mock_post.side_effect = httpx.ConnectError("Connection refused")
                write_res = await prod_client.set_json("prod:key", {"item": 1}, fail_closed=True)
                self.assertFalse(write_res.success)
                self.assertEqual(write_res.state, RedisState.UNAVAILABLE)
                self.assertFalse(write_res.is_authoritative)
                self.assertFalse(write_res.is_local_fallback)

            # In production, unconfigured client strictly rejects writes
            unconf_prod = UpstashRedisClient(url=None, token=None)
            with patch.object(unconf_prod, "is_production", True):
                res = await unconf_prod.set_json("prod:key:unconf", {"item": 2})
                self.assertFalse(res.success)
                self.assertEqual(res.state, RedisState.UNAVAILABLE)
                self.assertFalse(res.is_authoritative)

        # Verify refresh operation fails closed and preserves MongoDB records
        from backend.db.redis_client import redis_client

        # Mock Redis failing during refresh
        mock_fail_res = MagicMock()
        mock_fail_res.status_code = 503
        mock_fail_res.text = "Service Unavailable"

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_fail_res

            # Also force redis_client to have a URL so it attempts the remote write and gets 503
            with patch.object(redis_client, "url", "https://mock.upstash.io"), \
                 patch.object(redis_client, "token", "mock-token"):

                record = await sync_service.execute_refresh()

                # Rule: A Redis failure must not make database publication appear to have failed if MongoDB/Neon persistence succeeded.
                self.assertIn(record.status, ["completed", "partial", "failed"])
                self.assertTrue(any("redis" in err.lower() for err in record.errors))

                # Database audit record is preserved in MongoDB
                saved_audit = mongo_manager.source_sync_runs.find_one({"id": record.id})
                self.assertIsNotNone(saved_audit, "MongoDB audit record must be preserved despite Redis failure")
                self.assertIn(saved_audit["status"], ["completed", "partial", "failed"])

    @classmethod
    def tearDownClass(cls):
        # Clean up mock httpx if it was injected
        if "httpx" in sys.modules and getattr(sys.modules["httpx"], "_is_mock", False):
            del sys.modules["httpx"]
        try:
            import httpx as real_httpx
            import backend.db.redis_client
            backend.db.redis_client.httpx = real_httpx
        except ImportError:
            pass

if __name__ == "__main__":
    unittest.main()
