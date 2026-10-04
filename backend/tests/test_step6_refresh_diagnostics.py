import unittest
from backend.engine.pipeline import execute_prediction_pipeline
from backend.services.sync_service import SyncService

class TestStep6RefreshDiagnostics(unittest.TestCase):
    def setUp(self):
        from backend.db.failover_manager import failover_manager
        from backend.db.database_router import database_router, RoutingMode
        failover_manager.reset_state_for_tests()
        database_router.set_routing_mode(RoutingMode.NEON_ONLY)

    def test_pipeline_diagnostics_counters_and_stop_reasons(self):
        """
        Verify that execute_prediction_pipeline produces accurate diagnostic counters
        and candidate stop reasons.
        """
        from datetime import datetime, timezone, timedelta
        future_iso = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT15:00:00Z")

        mock_fixtures = [
            # 1. Invalid fixture (missing ID) -> REJECTED_INVALID
            {
                "id": "",
                "sport": "football",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": future_iso,
                "status": "scheduled",
            },
            # 2. Past fixture -> REJECTED_PAST
            {
                "id": "past_1",
                "sport": "football",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": "2020-01-01T15:00:00Z",
                "status": "scheduled",
            },
            # 3. Completed fixture -> REJECTED_COMPLETED
            {
                "id": "completed_1",
                "sport": "football",
                "homeTeam": "Arsenal",
                "awayTeam": "Chelsea",
                "kickoffUtc": future_iso,
                "status": "completed",
            },
            # 4. Eligible fixture without history -> INSUFFICIENT_HISTORY
            {
                "id": "eligible_no_hist",
                "sport": "football",
                "league": "Premier League",
                "homeTeam": "Unknown Team A",
                "awayTeam": "Unknown Team B",
                "kickoffUtc": future_iso,
                "status": "scheduled",
            },
        ]

        result = execute_prediction_pipeline(
            active_model="ELO + POISSON",
            is_subscriber_feed=True,
            custom_fixtures=mock_fixtures,
        )

        diagnostics = result.get("diagnostics", {})
        self.assertIn("fixturesEligible", diagnostics)
        self.assertEqual(diagnostics["fixturesEligible"], 1)
        self.assertEqual(diagnostics["fixturesRejectedInvalid"], 1)
        self.assertEqual(diagnostics["fixturesRejectedPast"], 1)
        self.assertEqual(diagnostics["fixturesRejectedCompleted"], 1)
        self.assertEqual(diagnostics["fixturesInsufficientHistory"], 1)

        stop_reasons = diagnostics.get("candidateStopReasons", {})
        self.assertEqual(stop_reasons.get("REJECTED_INVALID"), 1)
        self.assertEqual(stop_reasons.get("REJECTED_PAST"), 1)
        self.assertEqual(stop_reasons.get("REJECTED_COMPLETED"), 1)
        self.assertEqual(stop_reasons.get("INSUFFICIENT_HISTORY"), 1)

if __name__ == "__main__":
    unittest.main()
