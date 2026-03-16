"""Centralized timing engine for human-like browser behavior.

All delay, scroll, and viewport randomization routes through this module
so that LinkedIn's bot detection sees variable, human-like patterns instead
of deterministic constants.
"""

import random

# Common desktop resolutions (StatCounter Global Stats top 7, 2024-2025)
_VIEWPORT_POOL: list[tuple[int, int]] = [
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1280, 720),
    (1920, 1080),
    (1600, 900),
    (1280, 800),
]


def navigation_delay() -> float:
    """Random delay between page navigations.

    Uses a truncated normal distribution centered around 2.5s.  Real users
    exhibit a bell-curve of response times — this is far less fingerprintable
    than a flat constant.

    Returns the delay in seconds (caller is responsible for sleeping).
    """
    value = random.gauss(2.5, 0.8)
    return max(1.2, min(value, 5.0))


def scroll_pause() -> float:
    """Random pause between scroll actions.  Uniform [0.3, 1.2]s."""
    return random.uniform(0.3, 1.2)


def scroll_distance(viewport_height: int) -> int:
    """Random scroll distance in pixels.

    Simulates variable mouse-wheel speeds by scrolling between 50% and 120%
    of the viewport height.
    """
    return int(random.uniform(viewport_height * 0.5, viewport_height * 1.2))


def scroll_count() -> int:
    """Random number of scrolls for a full page.  Uniform [3, 7]."""
    return random.randint(3, 7)


def search_scroll_count() -> int:
    """Lighter scroll count for search result pages.  Uniform [1, 3]."""
    return random.randint(1, 3)


def viewport_dimensions() -> tuple[int, int]:
    """Pick a viewport from the pool of common desktop resolutions.

    Called once per session at browser startup.
    """
    return random.choice(_VIEWPORT_POOL)
