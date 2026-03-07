"""Shared helpers for new read/write automation tools."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from time import monotonic, perf_counter
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from linkedin_mcp_server.config import get_config
from linkedin_mcp_server.core.exceptions import (
    ConcurrencyError,
    InteractionError,
    QuotaExceededError,
    RateLimitError,
    SelectorError,
)
from linkedin_mcp_server.core.responses import (
    read_error,
    read_success,
    write_dry_run,
    write_error,
    write_quota_exceeded,
    write_success,
)
from linkedin_mcp_server.core.safety import (
    acquire_browser_lock,
    acquire_write_lock,
    audit_log,
    check_quota,
    check_session_health,
    record_security_challenge,
    record_successful_write,
    release_browser_lock,
    release_write_lock,
    require_confirmation,
)
from linkedin_mcp_server.core.utils import backoff_with_jitter, detect_rate_limit
from linkedin_mcp_server.drivers.browser import ensure_authenticated

logger = logging.getLogger(__name__)
SLOW_TOOL_SECONDS = 20.0
MIN_NAVIGATION_GAP_SECONDS = 2.5
NAVIGATION_RETRIES = 1
_last_navigation_started_at = 0.0


async def goto_and_check(page: Any, url: str, *, timeout_ms: int | None = None) -> None:
    """Navigate and run baseline challenge/rate-limit checks."""
    effective_timeout_ms = timeout_ms or max(get_config().browser.default_timeout, 15000)
    last_error: Exception | None = None

    for attempt in range(NAVIGATION_RETRIES + 1):
        await _respect_navigation_gap()
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=effective_timeout_ms,
            )
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

    now = monotonic()
    elapsed = now - _last_navigation_started_at
    if _last_navigation_started_at > 0 and elapsed < MIN_NAVIGATION_GAP_SECONDS:
        await asyncio.sleep(MIN_NAVIGATION_GAP_SECONDS - elapsed)

    _last_navigation_started_at = monotonic()


def _should_retry_navigation(exc: Exception) -> bool:
    if isinstance(exc, PlaywrightTimeoutError):
        return True
    if not isinstance(exc, RateLimitError):
        return False

    message = str(exc).lower()
    if any(token in message for token in ("captcha", "challenge", "checkpoint")):
        return False

    wait_seconds = getattr(exc, "suggested_wait_time", None)
    return not isinstance(wait_seconds, int) or wait_seconds <= 30


def normalize_profile_url(profile_url: str) -> str:
    """Normalize LinkedIn profile URLs to canonical /in/<slug>/ shape where possible."""
    normalized_input = profile_url.strip()
    if not normalized_input:
        raise ValueError("Invalid LinkedIn profile URL: empty input")

    if "://" not in normalized_input and normalized_input.startswith(
        ("linkedin.com/", "www.linkedin.com/")
    ):
        normalized_input = f"https://{normalized_input}"
    elif "://" not in normalized_input and normalized_input.startswith("/"):
        normalized_input = f"https://www.linkedin.com{normalized_input}"

    parsed = urlparse(normalized_input)

    if parsed.scheme and parsed.netloc and not parsed.netloc.endswith("linkedin.com"):
        raise ValueError(f"Invalid LinkedIn profile URL: {profile_url}")

    if not parsed.scheme:
        normalized_input = f"https://www.linkedin.com/in/{normalized_input.strip('/')}"
        parsed = urlparse(normalized_input)

    path = parsed.path or ""
    if not path.startswith("/in/") and parsed.netloc.endswith("linkedin.com"):
        if path and path != "/":
            return f"https://www.linkedin.com{path.rstrip('/')}/"
        raise ValueError(f"Invalid LinkedIn profile URL: {profile_url}")

    if not path:
        raise ValueError(f"Invalid LinkedIn profile URL: {profile_url}")

    slug = path.strip("/").split("/")[-1]
    return f"https://www.linkedin.com/in/{slug}/"


def extract_profile_slug(profile_input: str) -> str:
    """Accept a LinkedIn profile slug or URL/path and return the canonical slug."""
    normalized_input = profile_input.strip()
    if not normalized_input:
        raise ValueError("Invalid LinkedIn profile URL: empty input")

    if normalized_input.startswith("in/"):
        normalized_input = f"/{normalized_input}"

    if "linkedin.com" in normalized_input or normalized_input.startswith("/"):
        normalized_url = normalize_profile_url(normalized_input)
        parsed = urlparse(normalized_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "in":
            return parts[1]
        raise ValueError(f"Invalid LinkedIn profile URL: {profile_input}")

    return normalized_input.strip("/")


def parse_count(raw: str) -> int | None:
    """Parse compact count strings like '1,234' or '2.1k'."""
    if not raw:
        return None

    value = raw.strip().lower().replace(",", "")
    try:
        if value.endswith("k"):
            return int(float(value[:-1]) * 1000)
        if value.endswith("m"):
            return int(float(value[:-1]) * 1_000_000)
        return int(float(value))
    except ValueError:
        digits = re.sub(r"[^0-9]", "", value)
        return int(digits) if digits else None


def extract_thread_id_from_url(url: str) -> str | None:
    """Extract LinkedIn messaging thread id from thread URL."""
    match = re.search(r"/messaging/thread/([^/?]+)/?", url)
    if not match:
        return None
    return match.group(1)


def error_code_from_exception(exc: Exception) -> str:
    """Map internal exceptions to stable error code strings."""
    if isinstance(exc, QuotaExceededError):
        return "quota_exceeded"
    if isinstance(exc, ConcurrencyError):
        return "concurrency_error"
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    if isinstance(exc, SelectorError):
        return "selector_error"
    if isinstance(exc, InteractionError):
        return "interaction_error"
    if isinstance(exc, ValueError):
        return "validation_error"
    return "unknown_error"


def _log_tool_completion(
    action: str,
    result: dict[str, Any],
    duration_seconds: float,
    *,
    dry_run: bool = False,
) -> None:
    status = str(result.get("status", "unknown"))
    error_code = result.get("error_code")
    level = logging.INFO
    if status in {"error", "quota_exceeded"} or duration_seconds >= SLOW_TOOL_SECONDS:
        level = logging.WARNING

    message = f"Tool {action} finished with status={status} in {duration_seconds:.2f}s"
    if error_code:
        message += f" error_code={error_code}"

    logger.log(
        level,
        message,
        extra={
            "action": action,
            "status": status,
            "dry_run": dry_run,
            "duration_ms": int(duration_seconds * 1000),
            "error_code": error_code,
        },
    )


async def run_read_tool(
    action: str,
    fetch_fn: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Execute a read action with standardized response envelopes."""
    started_at = perf_counter()
    result: dict[str, Any]
    browser_lock_acquired = False
    try:
        await acquire_browser_lock(action)
        browser_lock_acquired = True
        await ensure_authenticated()
        payload = await fetch_fn()
        result = read_success(action=action, data=payload)
    except Exception as exc:
        result = read_error(
            action=action,
            message=str(exc),
            error_code=error_code_from_exception(exc),
        )
    finally:
        if browser_lock_acquired:
            release_browser_lock()
    _log_tool_completion(action, result, perf_counter() - started_at)
    return result


async def run_write_tool(
    action: str,
    params: dict[str, Any],
    dry_run: bool,
    confirm: bool,
    description: str,
    execute_fn: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Execute a write action with confirmation, quotas, lock, and audit logging."""
    started_at = perf_counter()
    result: dict[str, Any] = write_error(action, "Write action did not run.")
    browser_lock_acquired = False
    lock_acquired = False

    try:
        await acquire_browser_lock(action)
        browser_lock_acquired = True
        await ensure_authenticated()
        await check_session_health()
        await require_confirmation(action, confirm)

        await acquire_write_lock(action)
        lock_acquired = True

        if dry_run:
            result = write_dry_run(action, description)
            return result

        await check_quota(action)

        payload = await execute_fn()
        record_successful_write()
        extra_data = {
            key: value
            for key, value in payload.items()
            if key not in {"message", "resource_url", "warnings"}
        }

        result = write_success(
            action=action,
            message=str(payload.get("message", f"{action} completed.")),
            resource_url=payload.get("resource_url"),
            data=extra_data or None,
            warnings=list(payload.get("warnings", [])),
        )
        return result

    except QuotaExceededError as exc:
        result = write_quota_exceeded(
            action=action,
            message=str(exc),
            data={
                "tool_name": exc.tool_name,
                "limit": exc.limit,
                "used": exc.used,
            },
        )
        return result

    except RateLimitError as exc:
        message = str(exc)
        lowered = message.lower()
        if "captcha" in lowered or "challenge" in lowered:
            await record_security_challenge()

        cooldown_until = None
        wait_seconds = getattr(exc, "suggested_wait_time", None)
        if isinstance(wait_seconds, int) and wait_seconds > 0:
            cooldown = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
            cooldown_until = cooldown.isoformat().replace("+00:00", "Z")

        result = write_error(
            action=action,
            message=message,
            error_code="rate_limit",
            cooldown_until=cooldown_until,
            data=getattr(exc, "context", None),
        )
        return result

    except Exception as exc:
        result = write_error(
            action=action,
            message=str(exc),
            error_code=error_code_from_exception(exc),
            data=getattr(exc, "context", None),
        )
        return result

    finally:
        if lock_acquired:
            release_write_lock()
        if browser_lock_acquired:
            release_browser_lock()
        try:
            await audit_log(action, params, result, dry_run=dry_run)
        except Exception:
            # Audit failures should not change the tool outcome.
            pass
        _log_tool_completion(
            action,
            result,
            perf_counter() - started_at,
            dry_run=dry_run,
        )
