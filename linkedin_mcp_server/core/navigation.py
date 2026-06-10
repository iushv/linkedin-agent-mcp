"""Throttled page navigation with challenge and rate-limit checks.

Lives in ``core`` so lower layers (resolver, drivers) can navigate without
importing from ``tools`` — tool modules re-export these helpers via
``tools._common`` for backward compatibility.
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic, perf_counter
from typing import Any

from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from linkedin_mcp_server.config import get_config
from linkedin_mcp_server.core.exceptions import RateLimitError
from linkedin_mcp_server.core.throttle import AdaptiveThrottle
from linkedin_mcp_server.core.timing import navigation_delay
from linkedin_mcp_server.core.utils import backoff_with_jitter, detect_rate_limit

logger = logging.getLogger(__name__)

NAVIGATION_RETRIES = 2
_last_navigation_started_at = 0.0

FEED_NAVIGATION_TIMEOUT_MS = 45_000
"""Minimum timeout for feed-based navigations (feed, post composer)."""


def effective_navigation_timeout_ms(minimum_ms: int = 15000) -> int:
    """Return the effective Playwright navigation timeout for a tool.

    The result is ``max(user_configured_default, minimum_ms)`` so
    tool-specific floors cannot be underridden by a lower ``--timeout``.
    """
    return max(get_config().browser.default_timeout, minimum_ms)


async def goto_and_check(page: Any, url: str, *, timeout_ms: int | None = None) -> None:
    """Navigate and run baseline challenge/rate-limit checks."""
    effective_timeout_ms = timeout_ms or effective_navigation_timeout_ms()
    last_error: Exception | None = None

    for attempt in range(NAVIGATION_RETRIES + 1):
        await _respect_navigation_gap()
        try:
            nav_start = perf_counter()
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=effective_timeout_ms,
            )
            elapsed_ms = (perf_counter() - nav_start) * 1000
            AdaptiveThrottle.get().record(elapsed_ms)
            await detect_rate_limit(page)
            return
        except Exception as exc:
            last_error = exc
            if not _should_retry_navigation(exc) or attempt >= NAVIGATION_RETRIES:
                raise

            delay = await backoff_with_jitter(
                attempt,
                base_seconds=3,
                max_seconds=20,
            )
            logger.warning(
                "Retrying navigation for %s after %s (attempt %d/%d, backoff %.2fs)",
                url,
                type(exc).__name__,
                attempt + 1,
                NAVIGATION_RETRIES + 1,
                delay,
            )

    if last_error is not None:
        raise last_error


async def _respect_navigation_gap() -> None:
    global _last_navigation_started_at

    gap = navigation_delay() * AdaptiveThrottle.get().get_multiplier()
    now = monotonic()
    elapsed = now - _last_navigation_started_at
    if _last_navigation_started_at > 0 and elapsed < gap:
        await asyncio.sleep(gap - elapsed)

    _last_navigation_started_at = monotonic()


def _should_retry_navigation(exc: Exception) -> bool:
    if isinstance(exc, (PlaywrightTimeoutError, TimeoutError)):
        return True
    if not isinstance(exc, RateLimitError):
        return False

    message = str(exc).lower()
    if any(token in message for token in ("captcha", "challenge", "checkpoint")):
        return False

    wait_seconds = getattr(exc, "suggested_wait_time", None)
    return not isinstance(wait_seconds, int) or wait_seconds <= 30
