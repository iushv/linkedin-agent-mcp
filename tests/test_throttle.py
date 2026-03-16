"""Tests for the adaptive throttle module."""

import pytest

from linkedin_mcp_server.core.throttle import AdaptiveThrottle


@pytest.fixture(autouse=True)
def _reset_throttle():
    """Ensure each test starts with a fresh throttle."""
    AdaptiveThrottle.reset_singleton()
    yield
    AdaptiveThrottle.reset_singleton()


class TestAdaptiveThrottle:
    def test_initial_multiplier_is_one(self):
        t = AdaptiveThrottle.get()
        assert t.get_multiplier() == 1.0

    def test_baseline_established_after_n_samples(self):
        t = AdaptiveThrottle.get()
        for _ in range(5):
            t.record(500.0)
        assert t._baseline_locked
        assert t._baseline_ms > 0

    def test_multiplier_stays_at_one_for_normal_responses(self):
        t = AdaptiveThrottle.get()
        # Establish baseline
        for _ in range(5):
            t.record(500.0)
        # Normal responses
        for _ in range(20):
            t.record(600.0)
        assert t.get_multiplier() == 1.0

    def test_ramps_up_after_consecutive_slow_responses(self):
        t = AdaptiveThrottle.get()
        # Establish baseline at 500ms
        for _ in range(5):
            t.record(500.0)
        # 3 consecutive slow responses (> 2x baseline)
        for _ in range(3):
            t.record(1200.0)
        assert t.get_multiplier() > 1.0

    def test_multiplier_capped_at_max(self):
        t = AdaptiveThrottle.get()
        for _ in range(5):
            t.record(500.0)
        # Many slow responses to push multiplier to max
        for _ in range(30):
            t.record(2000.0)
        assert t.get_multiplier() <= 3.0

    def test_cools_down_after_fast_responses(self):
        t = AdaptiveThrottle.get()
        for _ in range(5):
            t.record(500.0)
        # Ramp up
        for _ in range(6):
            t.record(1500.0)
        ramped = t.get_multiplier()
        assert ramped > 1.0
        # Cool down with fast responses
        for _ in range(10):
            t.record(400.0)
        assert t.get_multiplier() < ramped

    def test_reset_clears_state(self):
        t = AdaptiveThrottle.get()
        for _ in range(5):
            t.record(500.0)
        for _ in range(6):
            t.record(1500.0)
        assert t.get_multiplier() > 1.0
        t.reset()
        assert t.get_multiplier() == 1.0
        assert not t._baseline_locked

    def test_singleton_returns_same_instance(self):
        a = AdaptiveThrottle.get()
        b = AdaptiveThrottle.get()
        assert a is b

    def test_reset_singleton_creates_new_instance(self):
        a = AdaptiveThrottle.get()
        AdaptiveThrottle.reset_singleton()
        b = AdaptiveThrottle.get()
        assert a is not b
