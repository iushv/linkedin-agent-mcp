"""Tests for the centralized timing engine."""

from linkedin_mcp_server.core.timing import (
    navigation_delay,
    scroll_count,
    scroll_distance,
    scroll_pause,
    search_scroll_count,
    viewport_dimensions,
)


class TestNavigationDelay:
    def test_within_bounds(self):
        for _ in range(200):
            d = navigation_delay()
            assert 1.2 <= d <= 5.0, f"navigation_delay={d} out of [1.2, 5.0]"

    def test_varies(self):
        values = {navigation_delay() for _ in range(50)}
        assert len(values) > 5, "navigation_delay should produce varied results"


class TestScrollPause:
    def test_within_bounds(self):
        for _ in range(200):
            p = scroll_pause()
            assert 0.3 <= p <= 1.2, f"scroll_pause={p} out of [0.3, 1.2]"


class TestScrollDistance:
    def test_within_bounds(self):
        for _ in range(200):
            d = scroll_distance(720)
            assert 360 <= d <= 864, f"scroll_distance={d} for vh=720 out of bounds"

    def test_scales_with_viewport(self):
        small = [scroll_distance(400) for _ in range(50)]
        large = [scroll_distance(1200) for _ in range(50)]
        assert max(small) < max(large), "larger viewport should yield larger scrolls"


class TestScrollCount:
    def test_within_bounds(self):
        for _ in range(200):
            c = scroll_count()
            assert 3 <= c <= 7, f"scroll_count={c} out of [3, 7]"


class TestSearchScrollCount:
    def test_within_bounds(self):
        for _ in range(200):
            c = search_scroll_count()
            assert 1 <= c <= 3, f"search_scroll_count={c} out of [1, 3]"


class TestViewportDimensions:
    def test_returns_valid_tuple(self):
        for _ in range(50):
            w, h = viewport_dimensions()
            assert isinstance(w, int) and w > 0
            assert isinstance(h, int) and h > 0

    def test_from_known_pool(self):
        from linkedin_mcp_server.core.timing import _VIEWPORT_POOL

        for _ in range(50):
            dims = viewport_dimensions()
            assert dims in _VIEWPORT_POOL
