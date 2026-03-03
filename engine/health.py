"""
POC7: MCP Health Monitor -- tracks server health, latency, and fallback state.
Actively gates agent execution when MCP is degraded.
"""

import collections
import statistics
import time


class MCPHealthMonitor:
    """Tracks MCP server health: ping status, latency, error rate.

    When 3+ consecutive failures occur, activates fallback mode.
    Callers check fallback_active to gate agent execution.
    """

    def __init__(self, mcp_client, max_history=50):
        self._client = mcp_client
        self._latencies = collections.deque(maxlen=max_history)
        self._ping_results = collections.deque(maxlen=max_history)
        self._consecutive_errors = 0
        self._total_checks = 0
        self._total_failures = 0
        self._fallback_active = False
        self._force_fail_count = 0

    def simulate_degradation(self, num_failures=3):
        """Force next N health checks to fail (for testing).

        This queues forced failures so the next N calls to check_health()
        return unhealthy without actually pinging the server.  Used to
        demonstrate health gating and fallback activation.
        """
        self._force_fail_count = num_failures

    def check_health(self) -> dict:
        """Run a health check: ping the MCP server.

        If simulate_degradation() was called, the next N checks will
        return forced failures to test fallback activation.
        """
        self._total_checks += 1

        # Forced failure path (simulated degradation)
        if self._force_fail_count > 0:
            self._force_fail_count -= 1
            self._consecutive_errors += 1
            self._total_failures += 1
            self._ping_results.append(False)
            if self._consecutive_errors >= 3:
                self._fallback_active = True
            return {
                "healthy": False,
                "latency_ms": -1,
                "consecutive_errors": self._consecutive_errors,
                "fallback_active": self._fallback_active,
            }

        # Real health check
        t0 = time.perf_counter()
        try:
            ping_ok = self._client.ping()
            latency = (time.perf_counter() - t0) * 1000  # ms
            self._latencies.append(latency)
            self._ping_results.append(ping_ok)
            if ping_ok:
                self._consecutive_errors = 0
                self._fallback_active = False
            else:
                self._consecutive_errors += 1
                self._total_failures += 1
        except Exception:
            self._consecutive_errors += 1
            self._total_failures += 1
            self._ping_results.append(False)
            ping_ok = False
            latency = -1

        if self._consecutive_errors >= 3:
            self._fallback_active = True

        return {
            "healthy": ping_ok,
            "latency_ms": latency,
            "consecutive_errors": self._consecutive_errors,
            "fallback_active": self._fallback_active,
        }

    @property
    def fallback_active(self) -> bool:
        return self._fallback_active

    def get_stats(self) -> dict:
        """Return aggregate health statistics for audit."""
        lats = [l for l in self._latencies if l > 0]
        return {
            "total_checks": self._total_checks,
            "total_failures": self._total_failures,
            "uptime_pct": ((self._total_checks - self._total_failures)
                           / self._total_checks * 100)
                          if self._total_checks else 100.0,
            "avg_latency_ms": statistics.mean(lats) if lats else 0,
            "max_latency_ms": max(lats) if lats else 0,
            "min_latency_ms": min(lats) if lats else 0,
            "fallback_active": self._fallback_active,
        }
