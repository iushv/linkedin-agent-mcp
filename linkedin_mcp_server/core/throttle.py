"""Adaptive throttle that tracks LinkedIn response times and auto-adjusts delays.

When LinkedIn starts responding slowly (a leading indicator of rate limiting),
the throttle ramps up a multiplier that all timing functions use.  When
responses return to normal, the multiplier cools back down.
"""

from __future__ import annotations

import logging
from collections import deque
from statistics import median
from time import monotonic

logger = logging.getLogger(__name__)

# Sliding window for response-time samples
_WINDOW_SECONDS = 120.0

# Number of initial samples used to establish the baseline
_BASELINE_SAMPLE_COUNT = 5

# A response is considered "slow" when it exceeds this ratio of the baseline
_SLOW_THRESHOLD_RATIO = 2.0

# After N consecutive slow responses, ramp the multiplier up
_RAMP_UP_AFTER = 3

# After N consecutive fast responses, ramp the multiplier down
_COOLDOWN_AFTER_FAST = 5

# Hard ceiling on the delay multiplier
_MAX_MULTIPLIER = 3.0

# Step sizes for ramping
_RAMP_UP_STEP = 0.5
_COOLDOWN_STEP = 0.25


class AdaptiveThrottle:
    """Singleton that tracks navigation latencies and adjusts delay multipliers."""

    _instance: AdaptiveThrottle | None = None

    def __init__(self) -> None:
        self._response_times: deque[tuple[float, float]] = deque()
        self._baseline_ms: float = 0.0
        self._baseline_locked = False
        self._multiplier: float = 1.0
        self._consecutive_slow: int = 0
        self._consecutive_fast: int = 0

    @classmethod
    def get(cls) -> AdaptiveThrottle:
        """Return the singleton instance, creating it if needed."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def record(self, response_time_ms: float) -> None:
        """Record a navigation response time and update the multiplier."""
        now = monotonic()
        self._response_times.append((now, response_time_ms))
        self._prune_window(now)

        # Establish baseline from early samples
        if not self._baseline_locked:
            if len(self._response_times) >= _BASELINE_SAMPLE_COUNT:
                samples = [rt for _, rt in self._response_times]
                self._baseline_ms = median(samples[:_BASELINE_SAMPLE_COUNT])
                self._baseline_locked = True
                logger.debug(
                    "Adaptive throttle baseline established: %.0fms", self._baseline_ms
                )
            return

        is_slow = response_time_ms > self._baseline_ms * _SLOW_THRESHOLD_RATIO

        if is_slow:
            self._consecutive_slow += 1
            self._consecutive_fast = 0
            if self._consecutive_slow >= _RAMP_UP_AFTER:
                old = self._multiplier
                self._multiplier = min(
                    self._multiplier + _RAMP_UP_STEP, _MAX_MULTIPLIER
                )
                if self._multiplier != old:
                    logger.info(
                        "Adaptive throttle ramped up: %.2f -> %.2f "
                        "(response %.0fms, baseline %.0fms)",
                        old,
                        self._multiplier,
                        response_time_ms,
                        self._baseline_ms,
                    )
        else:
            self._consecutive_fast += 1
            self._consecutive_slow = 0
            if (
                self._consecutive_fast >= _COOLDOWN_AFTER_FAST
                and self._multiplier > 1.0
            ):
                old = self._multiplier
                self._multiplier = max(self._multiplier - _COOLDOWN_STEP, 1.0)
                if self._multiplier != old:
                    logger.info(
                        "Adaptive throttle cooled down: %.2f -> %.2f",
                        old,
                        self._multiplier,
                    )

    def get_multiplier(self) -> float:
        """Return current delay multiplier (1.0 = normal, up to 3.0)."""
        return self._multiplier

    def _prune_window(self, now: float) -> None:
        """Remove entries older than the sliding window."""
        cutoff = now - _WINDOW_SECONDS
        while self._response_times and self._response_times[0][0] < cutoff:
            self._response_times.popleft()

    def reset(self) -> None:
        """Reset to fresh state (for testing)."""
        self._response_times.clear()
        self._baseline_ms = 0.0
        self._baseline_locked = False
        self._multiplier = 1.0
        self._consecutive_slow = 0
        self._consecutive_fast = 0

    @classmethod
    def reset_singleton(cls) -> None:
        """Reset the singleton instance (for testing)."""
        if cls._instance is not None:
            cls._instance.reset()
        cls._instance = None
