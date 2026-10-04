"""
Telemetry, Diagnostics, and Operational Budgets.
Tracks metrics, records requested/returned, provider call counts, database queries,
and monitors operational budgets to catch regressions and N+1 query patterns.
"""

import time
from typing import Dict, Any, List, Optional
from backend.models.schemas import BaseModel, Field

class OperationTelemetry(BaseModel):
    operation: str
    sport: Optional[str] = None
    competition: Optional[str] = None
    event_count: int = 0
    records_requested: int = 0
    records_returned: int = 0
    fields_requested: List[str] = Field(default_factory=list)
    cache_hit: bool = False
    provider_call: bool = False
    provider_call_count: int = 0
    database_query_count: int = 0
    duration_ms: float = 0.0
    violations: List[str] = Field(default_factory=list)

class TelemetryService:
    def __init__(self):
        self._history: List[OperationTelemetry] = []

    def record_operation(
        self,
        operation: str,
        sport: Optional[str] = None,
        competition: Optional[str] = None,
        event_count: int = 0,
        records_requested: int = 0,
        records_returned: int = 0,
        fields_requested: Optional[List[str]] = None,
        cache_hit: bool = False,
        provider_call_count: int = 0,
        database_query_count: int = 0,
        duration_ms: float = 0.0,
    ) -> OperationTelemetry:
        violations: List[str] = []

        # Budget Check 1: N+1 Database Queries Detection
        # If database_query_count > 5 * (1 + event_count) when event_count > 2
        if event_count > 5 and database_query_count > (event_count * 2):
            violations.append(
                f"BUDGET_VIOLATION: N+1 query detected! {database_query_count} DB queries for {event_count} events."
            )

        # Budget Check 2: Max Provider Calls per Refresh
        if operation == "refresh" and provider_call_count > 20:
            violations.append(
                f"BUDGET_VIOLATION: High provider call count ({provider_call_count}) exceeding budget (20)."
            )

        telemetry = OperationTelemetry(
            operation=operation,
            sport=sport,
            competition=competition,
            event_count=event_count,
            records_requested=records_requested,
            records_returned=records_returned,
            fields_requested=fields_requested or [],
            cache_hit=cache_hit,
            provider_call=provider_call_count > 0,
            provider_call_count=provider_call_count,
            database_query_count=database_query_count,
            duration_ms=duration_ms,
            violations=violations,
        )
        self._history.append(telemetry)
        if len(self._history) > 500:
            self._history = self._history[-500:]
        return telemetry

    def get_recent_metrics(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [t.model_dump() for t in self._history[-limit:]]

telemetry_service = TelemetryService()
