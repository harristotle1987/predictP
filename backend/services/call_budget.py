import time
from typing import Dict, Any, List

class CallBudgetGuard:
    """
    Guards provider call limits and records granular execution metrics.
    """
    def __init__(self, max_allowed_calls: int = 50):
        self.max_allowed_calls = max_allowed_calls
        self.calls_requested = 0
        self.calls_executed = 0
        self.cache_hits = 0
        self.skipped_calls = 0
        self.failures = 0
        self.records_returned = 0
        self.start_time = time.perf_counter()

    def record_call_request(self) -> bool:
        """
        Returns True if within budget, False if budget exceeded.
        """
        self.calls_requested += 1
        if self.calls_executed >= self.max_allowed_calls:
            self.skipped_calls += 1
            return False
        return True

    def record_call_executed(self, records_count: int = 0):
        self.calls_executed += 1
        self.records_returned += records_count

    def record_cache_hit(self, records_count: int = 0):
        self.cache_hits += 1
        self.records_returned += records_count

    def record_failure(self):
        self.failures += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "callsRequested": self.calls_requested,
            "callsExecuted": self.calls_executed,
            "cacheHits": self.cache_hits,
            "skippedCalls": self.skipped_calls,
            "failures": self.failures,
            "recordsReturned": self.records_returned,
            "elapsedMs": round((time.perf_counter() - self.start_time) * 1000, 2),
        }
