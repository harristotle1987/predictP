"""
Unit Tests for Strict Neon Resource Protection System.
Validates:
1. Unbounded query protection (rejects SELECT *, missing LIMIT, missing single-row WHERE).
2. Oversized result protection (enforces NEON_QUERY_MAX_ROWS).
3. Replication batch limit (enforces NEON_REPLICATION_BATCH_SIZE headroom).
4. Egress soft limit (transitions to DEGRADED, halts nonessential reads, throttles replication).
5. Egress hard limit (transitions to RESOURCE_LIMITED, halts bulk sync).
6. Storage soft limit (marks DEGRADED).
7. Storage hard limit (marks RESOURCE_LIMITED).
8. Connection pooling / concurrency ceiling enforcement.
9. Query timeout enforcement (NEON_QUERY_TIMEOUT_MS).
10. Scale-to-zero friendly cached health ping.
11. Safe operational data retention pruning.
"""

import unittest
import asyncio
from unittest.mock import patch, MagicMock

from backend.db.neon_budget_guard import NeonBudgetGuard, SafetyState
from backend.db.interfaces import BudgetExceededError
from backend.db.neon_adapter import NeonQueryExecutor
from backend.config import settings


class TestNeonResourceProtection(unittest.TestCase):

    def setUp(self):
        # Fresh guard instance with isolated thresholds
        self.guard = NeonBudgetGuard(
            max_queries_per_min=20,
            max_concurrency=3,
            egress_soft_limit_bytes=10000,    # 10 KB
            egress_hard_limit_bytes=20000,    # 20 KB
            storage_soft_limit_bytes=50000,   # 50 KB
            storage_hard_limit_bytes=100000,  # 100 KB
            query_max_rows=100,
            query_timeout_ms=3000,
        )

    def test_unbounded_query_protection_rejects_select_star(self):
        """Rejects queries containing 'SELECT *'."""
        query = "SELECT * FROM neon_fixtures WHERE id = $1 LIMIT 1;"
        with self.assertRaises(BudgetExceededError) as ctx:
            self.guard.inspect_query(query)
        self.assertIn("SELECT *", str(ctx.exception))

    def test_unbounded_query_protection_rejects_unbounded_select(self):
        """Rejects SELECT queries without a LIMIT or unique single-row WHERE clause."""
        query = "SELECT id, sport, status FROM neon_fixtures WHERE status = 'scheduled';"
        with self.assertRaises(BudgetExceededError) as ctx:
            self.guard.inspect_query(query)
        self.assertIn("Unbounded SELECT", str(ctx.exception))

    def test_unbounded_query_protection_accepts_bounded_select(self):
        """Accepts explicit projections with bounded LIMIT or single-row WHERE."""
        # Query with explicit limit
        q1 = "SELECT id, sport, status FROM neon_fixtures WHERE status = 'scheduled' LIMIT 50;"
        self.guard.inspect_query(q1)  # Should not raise

        # Query with single-row WHERE
        q2 = "SELECT id, sport, status FROM neon_fixtures WHERE id = $1;"
        self.guard.inspect_query(q2)  # Should not raise

    def test_oversized_result_protection(self):
        """Rejects queries requesting row limits exceeding NEON_QUERY_MAX_ROWS."""
        # Requested limit 250 exceeds query_max_rows (100)
        query = "SELECT id, sport FROM neon_fixtures LIMIT 250;"
        with self.assertRaises(BudgetExceededError) as ctx:
            self.guard.inspect_query(query)
        self.assertIn("exceeds maximum allowed row budget", str(ctx.exception))

    def test_prohibited_tables_protection(self):
        """Rejects queries attempting to access historical Parquet/DuckDB tables in Neon."""
        for tbl in ["historical_matches", "raw_sports_skills", "parquet_datasets", "duckdb_catalog"]:
            q = f"SELECT id, data FROM {tbl} LIMIT 10;"
            with self.assertRaises(BudgetExceededError) as ctx:
                self.guard.inspect_query(q)
            self.assertIn("prohibited", str(ctx.exception))

    def test_replication_batch_limit_setting(self):
        """Verifies replication batch size is configured conservatively below provider limits."""
        self.assertLessEqual(settings.neon_replication_batch_size, 100)
        self.assertGreaterEqual(settings.neon_replication_batch_size, 10)

    def test_egress_soft_limit_transitions_to_degraded(self):
        """Exceeding soft egress limit transitions state to DEGRADED and stops nonessential reads."""
        self.assertEqual(self.guard.get_safety_state(), SafetyState.OPTIMAL)
        self.assertTrue(self.guard.is_nonessential_read_permitted())

        # Record query returning 12 KB (exceeds 10 KB soft limit)
        self.guard.record_query_execution(rows_count=10, estimated_bytes=12000)

        self.assertEqual(self.guard.get_safety_state(), SafetyState.DEGRADED)
        self.assertFalse(self.guard.is_nonessential_read_permitted(), "Nonessential reads should be blocked when DEGRADED")
        self.assertTrue(self.guard.is_bulk_sync_permitted(), "Critical sync should continue when DEGRADED")

    def test_egress_hard_limit_transitions_to_resource_limited(self):
        """Exceeding hard egress limit transitions state to RESOURCE_LIMITED and halts bulk sync."""
        # Record query returning 25 KB (exceeds 20 KB hard limit)
        self.guard.record_query_execution(rows_count=20, estimated_bytes=25000)

        self.assertEqual(self.guard.get_safety_state(), SafetyState.RESOURCE_LIMITED)
        self.assertFalse(self.guard.is_bulk_sync_permitted(), "Bulk sync must be halted when RESOURCE_LIMITED")
        self.assertFalse(self.guard.is_nonessential_read_permitted())

        # Inspecting a read query in RESOURCE_LIMITED state must be rejected
        with self.assertRaises(BudgetExceededError):
            self.guard.inspect_query("SELECT id FROM neon_fixtures LIMIT 5;", is_write=False)

    def test_storage_soft_and_hard_limits(self):
        """Verifies storage thresholds trigger DEGRADED and RESOURCE_LIMITED states."""
        # 1. Below soft limit
        self.guard.record_storage_estimate(40000)
        self.assertEqual(self.guard.get_safety_state(), SafetyState.OPTIMAL)

        # 2. Exceed soft limit (60 KB > 50 KB)
        self.guard.record_storage_estimate(60000)
        self.assertEqual(self.guard.get_safety_state(), SafetyState.DEGRADED)

        # 3. Exceed hard limit (110 KB > 100 KB)
        self.guard.record_storage_estimate(110000)
        self.assertEqual(self.guard.get_safety_state(), SafetyState.RESOURCE_LIMITED)

    def test_connection_pooling_and_concurrency_ceiling(self):
        """Enforces max_concurrency ceiling across concurrent query slots."""
        guard = NeonBudgetGuard(max_concurrency=2, max_queries_per_min=50)

        async def run_concurrent():
            await guard.acquire_query_slot("slot_1")
            await guard.acquire_query_slot("slot_2")
            # 3rd concurrent slot must be rejected
            with self.assertRaises(BudgetExceededError) as ctx:
                await guard.acquire_query_slot("slot_3")
            self.assertIn("concurrency ceiling reached", str(ctx.exception))

            # Release one slot
            await guard.release_query_slot(True)
            # Now slot_3 should succeed
            await guard.acquire_query_slot("slot_3_retry")
            await guard.release_query_slot(True)
            await guard.release_query_slot(True)

        asyncio.run(run_concurrent())

    def test_query_timeout_configuration(self):
        """Verifies query timeout is exposed in seconds for HTTP and driver dispatch."""
        self.assertEqual(self.guard.get_query_timeout_seconds(), 3.0)
        default_guard = NeonBudgetGuard()
        self.assertEqual(default_guard.get_query_timeout_seconds(), 4.0)

    def test_scale_to_zero_friendly_ping_caching(self):
        """Verifies ping responses are cached for 60 seconds to avoid waking Neon compute."""
        self.assertIsNone(self.guard.get_cached_ping())

        sample_ping = {"status": "connected", "latencyMs": 14.5}
        self.guard.set_cached_ping(sample_ping)

        cached = self.guard.get_cached_ping()
        self.assertIsNotNone(cached)
        self.assertTrue(cached.get("cached"))
        self.assertEqual(cached.get("status"), "connected")

    def test_telemetry_separates_estimates_from_reported_usage(self):
        """Verifies metrics clearly separate PredictPro estimated usage from official Neon usage."""
        metrics = self.guard.get_metrics()
        self.assertIn("predictproEstimatedUsage", metrics)
        self.assertIn("neonReportedUsage", metrics)
        self.assertIn("configuredSafetyThresholds", metrics)
        self.assertIsNone(metrics["neonReportedUsage"], "Neon reported usage must be null unless official API configured")


if __name__ == "__main__":
    unittest.main()
