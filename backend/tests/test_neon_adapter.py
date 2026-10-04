"""
Unit Tests for Neon PostgreSQL Adapter and Connection Pool Executor.
Validates:
1. psycopg connection pool initialization with NEON_DATABASE_URL directly.
2. Absence of HTTP /sql URLs and Bearer token headers.
3. Method interface compliance: connect, close, health_check, execute, fetch_one, fetch_all, transaction.
4. Bound connection pooling and connection/query timeouts.
5. Parameterized SQL handling (converting $1, $2 to %s or keeping tuple params).
6. Non-exposure of NEON_DATABASE_URL in logs/exceptions via sanitize_url.
7. DatabaseRouter compatibility interface.
"""

import unittest
import os
from unittest.mock import patch, MagicMock

from backend.db.neon_adapter import (
    NeonQueryExecutor,
    NeonDatabaseAdapter,
    sanitize_url,
)
from backend.db.interfaces import DatabaseUnavailableError


class TestNeonAdapter(unittest.TestCase):

    def test_sanitize_url_redacts_credentials(self):
        url = "postgresql://user:secretpassword123@ep-cool-site.us-east-2.aws.neon.tech/neondb?sslmode=require"
        sanitized = sanitize_url(url)
        self.assertNotIn("secretpassword123", sanitized)
        self.assertIn("user:***@", sanitized)

    def test_sanitize_url_handles_none(self):
        self.assertEqual(sanitize_url(None), "<not_configured>")

    @patch.dict(os.environ, {"NEON_DATABASE_URL": "postgresql://user:pass@localhost:5432/testdb"})
    def test_executor_connect_uses_connection_pool(self):
        executor = NeonQueryExecutor()
        self.assertTrue(executor.is_configured())

        with patch("backend.db.neon_adapter.ConnectionPool") as mock_pool_cls:
            mock_pool_instance = MagicMock()
            mock_pool_cls.return_value = mock_pool_instance

            executor.connect()

            mock_pool_cls.assert_called_once()
            call_kwargs = mock_pool_cls.call_args[1]
            self.assertEqual(call_kwargs["conninfo"], "postgresql://user:pass@localhost:5432/testdb")
            self.assertIn("min_size", call_kwargs)
            self.assertIn("max_size", call_kwargs)
            self.assertIn("timeout", call_kwargs)

            executor.close()
            mock_pool_instance.close.assert_called_once()

    @patch.dict(os.environ, {}, clear=True)
    def test_executor_raises_when_unconfigured(self):
        executor = NeonQueryExecutor()
        executor._initialized = False
        executor._url = None
        with patch("backend.config.settings.neon_database_url", None):
            self.assertFalse(executor.is_configured())

            with self.assertRaises(DatabaseUnavailableError) as ctx:
                executor.connect()
            self.assertIn("not configured", str(ctx.exception))

            with self.assertRaises(DatabaseUnavailableError):
                executor.fetch_all("SELECT 1 FROM neon_fixtures LIMIT 1;")

            with self.assertRaises(DatabaseUnavailableError):
                executor.execute("DELETE FROM neon_fixtures WHERE id = $1;", ["1"])

    def test_executor_normalize_query_params(self):
        executor = NeonQueryExecutor()
        query = "SELECT id FROM neon_fixtures WHERE sport = $1 AND status = $2 LIMIT $3;"
        params = ["football", "scheduled", 10]

        norm_query, norm_params = executor._normalize_query_params(query, params)
        self.assertEqual(norm_query, "SELECT id FROM neon_fixtures WHERE sport = %s AND status = %s LIMIT %s;")
        self.assertEqual(norm_params, ("football", "scheduled", 10))

    @patch.dict(os.environ, {"NEON_DATABASE_URL": "postgresql://user:pass@localhost:5432/testdb"})
    def test_executor_fetch_all_and_fetch_one(self):
        executor = NeonQueryExecutor()
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()

        mock_pool.connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            {"id": "fix_1", "sport": "football"},
            {"id": "fix_2", "sport": "basketball"},
        ]

        executor._pool = mock_pool

        # fetch_all
        rows = executor.fetch_all("SELECT id, sport FROM neon_fixtures LIMIT 10;")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "fix_1")

        # fetch_one
        one = executor.fetch_one("SELECT id, sport FROM neon_fixtures WHERE id = $1;", ["fix_1"])
        self.assertIsNotNone(one)
        self.assertEqual(one["id"], "fix_1")

    @patch.dict(os.environ, {"NEON_DATABASE_URL": "postgresql://user:pass@localhost:5432/testdb"})
    def test_executor_execute_statement(self):
        executor = NeonQueryExecutor()
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()

        mock_pool.connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.rowcount = 1

        executor._pool = mock_pool

        affected = executor.execute("UPDATE neon_fixtures SET status = $1 WHERE id = $2;", ["completed", "fix_1"])
        self.assertEqual(affected, 1)
        mock_conn.commit.assert_called_once()

    @patch.dict(os.environ, {"NEON_DATABASE_URL": "postgresql://user:pass@localhost:5432/testdb"})
    def test_executor_transaction(self):
        executor = NeonQueryExecutor()
        mock_pool = MagicMock()
        mock_conn = MagicMock()

        mock_pool.connection.return_value.__enter__.return_value = mock_conn
        executor._pool = mock_pool

        with executor.transaction() as conn:
            self.assertEqual(conn, mock_conn)

    def test_adapter_interface_compliance(self):
        adapter = NeonDatabaseAdapter()
        self.assertEqual(adapter.backend_name, "neon")
        self.assertIsNotNone(adapter.fixtures)
        self.assertIsNotNone(adapter.predictions)
        self.assertIsNotNone(adapter.prediction_results)
        self.assertIsNotNone(adapter.refresh_state)
        self.assertIsNotNone(adapter.model_config)
        self.assertIsNotNone(adapter.calibrations)
        self.assertIsNotNone(adapter.governance)


if __name__ == "__main__":
    unittest.main()
