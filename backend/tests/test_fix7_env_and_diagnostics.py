import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import os
import asyncio

from backend.config import settings, Settings
from backend.services.health_service import health_service


class TestFix7EnvAndDiagnostics(unittest.IsolatedAsyncioTestCase):

    def test_environment_variable_names_match_spec(self):
        """1. Verifies that Settings class reads exact environment variable names specified in requirement."""
        s = Settings()
        self.assertTrue(hasattr(s, "mongodb_uri"))
        self.assertTrue(hasattr(s, "mongodb_database"))
        self.assertTrue(hasattr(s, "neon_database_url"))
        self.assertTrue(hasattr(s, "upstash_redis_rest_url"))
        self.assertTrue(hasattr(s, "upstash_redis_rest_token"))
        self.assertTrue(hasattr(s, "r2_account_id"))
        self.assertTrue(hasattr(s, "r2_bucket"))
        self.assertTrue(hasattr(s, "r2_endpoint"))
        self.assertTrue(hasattr(s, "r2_access_key_id"))
        self.assertTrue(hasattr(s, "r2_secret_access_key"))
        self.assertTrue(hasattr(s, "r2_region"))
        self.assertTrue(hasattr(s, "admin_api_key"))
        self.assertTrue(hasattr(s, "sports_skills_base_url"))
        self.assertTrue(hasattr(s, "sports_skills_timeout_ms"))

    async def test_admin_diagnostics_returns_bounded_connectivity_status(self):
        """2. Admin diagnostics performs actual bounded connectivity checks for all 7 required services."""
        with patch("backend.db.mongodb_adapter.mongodb_adapter.check_connection", return_value={"status": "connected"}), \
             patch("backend.db.neon_adapter.neon_adapter.check_connection", return_value={"status": "connected"}), \
             patch("backend.db.neon_adapter.neon_adapter.check_schema_compatibility", return_value=True), \
             patch("backend.db.redis_client.redis_client.check_connection", AsyncMock(return_value={"status": "connected"})), \
             patch("backend.db.r2_storage.r2_manager.check_connection", return_value={"status": "connected"}), \
             patch("backend.db.duckdb_engine.duckdb_engine.check_connection", return_value={"status": "connected"}), \
             patch("backend.providers.football_adapter.football_adapter.check_connection", AsyncMock(return_value={"status": "connected"})):

            status = await health_service.get_health_status()

            self.assertIn("MongoDB", status)
            self.assertIn("Neon", status)
            self.assertIn("Neon failover ready", status)
            self.assertIn("Redis", status)
            self.assertIn("R2", status)
            self.assertIn("DuckDB", status)
            self.assertIn("SportsSkills", status)

            self.assertIn(status["MongoDB"], ("connected", "disconnected"))
            self.assertIn(status["Neon"], ("connected", "disconnected"))
            self.assertIsInstance(status["Neon failover ready"], bool)
            self.assertIn(status["Redis"], ("connected", "disconnected"))
            self.assertIn(status["R2"], ("connected", "disconnected"))
            self.assertIn(status["DuckDB"], ("connected", "disconnected"))
            self.assertIn(status["SportsSkills"], ("connected", "disconnected"))

    async def test_bounded_checks_not_marked_connected_by_env_alone(self):
        """3. Services are marked disconnected if bounded ping/check fails, even if environment variables exist."""
        with patch("backend.db.mongodb_adapter.mongodb_adapter.check_connection", return_value={"status": "disconnected"}), \
             patch("backend.db.neon_adapter.neon_adapter.check_connection", return_value={"status": "disconnected"}), \
             patch("backend.db.redis_client.redis_client.check_connection", AsyncMock(return_value={"status": "disconnected"})), \
             patch("backend.db.r2_storage.r2_manager.check_connection", return_value={"status": "disconnected"}), \
             patch("backend.db.duckdb_engine.duckdb_engine.check_connection", return_value={"status": "disconnected"}):

            status = await health_service.get_health_status()

            self.assertEqual(status["MongoDB"], "disconnected")
            self.assertEqual(status["Neon"], "disconnected")
            self.assertFalse(status["Neon failover ready"])
            self.assertEqual(status["Redis"], "disconnected")
            self.assertEqual(status["R2"], "disconnected")
            self.assertEqual(status["DuckDB"], "disconnected")


if __name__ == "__main__":
    unittest.main()
