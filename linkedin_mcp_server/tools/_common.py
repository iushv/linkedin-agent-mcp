"""Shared helpers for new read/write automation tools."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Awaitable, Callable
from urllib.parse import unquote, urljoin, urlparse

from linkedin_mcp_server.core.exceptions import (
    AuthenticationError,
    ConcurrencyError,
    ElementNotFoundError,
    InteractionError,
    NetworkError,
    ProfileNotFoundError,
    QuotaExceededError,
    RateLimitError,
    ScrapingError,
    SelectorError,
)
from linkedin_mcp_server.exceptions import (
    CredentialsNotFoundError,
    SessionExpiredError,
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
    get_engagement_cooldown_seconds,
    get_session_health,
    is_engagement_degraded,
    record_security_challenge,
    record_successful_write,
    release_browser_lock,
    release_write_lock,
    require_confirmation,
)
from linkedin_mcp_server.core.navigation import (
    FEED_NAVIGATION_TIMEOUT_MS as FEED_NAVIGATION_TIMEOUT_MS,
    NAVIGATION_RETRIES as NAVIGATION_RETRIES,
    effective_navigation_timeout_ms as effective_navigation_timeout_ms,
    goto_and_check as goto_and_check,
)
from linkedin_mcp_server.core.utils import detect_rate_limit
from linkedin_mcp_server.drivers.browser import ensure_authenticated

logger = logging.getLogger(__name__)
SLOW_TOOL_SECONDS = 20.0


_ACTIVITY_URN_RE = re.compile(r"urn:li:activity:\d+")


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


def normalize_post_reference(post_url: str) -> str:
    """Normalize LinkedIn post references to canonical feed/update URLs.

    Accepted forms:
    - full LinkedIn post URL
    - relative LinkedIn post path
    - raw ``urn:li:activity:<id>``
    """
    candidate = post_url.strip()
    if not candidate:
        raise ValueError("Invalid LinkedIn post URL: empty input")

    urn_match = _ACTIVITY_URN_RE.fullmatch(candidate)
    if urn_match:
        return f"https://www.linkedin.com/feed/update/{urn_match.group(0)}"

    if "://" not in candidate and candidate.startswith(
        ("linkedin.com/", "www.linkedin.com/")
    ):
        candidate = f"https://{candidate}"
    elif "://" not in candidate and candidate.startswith("/"):
        candidate = urljoin("https://www.linkedin.com", candidate)
    elif "://" not in candidate and candidate.startswith(
        ("feed/update/", "posts/", "activity-")
    ):
        candidate = urljoin("https://www.linkedin.com/", candidate)
    elif candidate.startswith("http://linkedin.com"):
        candidate = candidate.replace("http://linkedin.com", "https://linkedin.com", 1)
    elif candidate.startswith("http://www.linkedin.com"):
        candidate = candidate.replace(
            "http://www.linkedin.com",
            "https://www.linkedin.com",
            1,
        )

    decoded_candidate = unquote(candidate)
    embedded_urn = _ACTIVITY_URN_RE.search(decoded_candidate)
    if embedded_urn:
        return f"https://www.linkedin.com/feed/update/{embedded_urn.group(0)}"

    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc.endswith("linkedin.com"):
        raise ValueError(f"Invalid LinkedIn post URL: {post_url}")

    path = parsed.path or ""
    if not any(marker in path for marker in ("/feed/update/", "/posts/", "/activity-")):
        raise ValueError(f"Invalid LinkedIn post URL: {post_url}")

    return f"https://www.linkedin.com{path.rstrip('/')}/"


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


async def ensure_engagement_allowed() -> None:
    """Block engagement-like writes while the session is in cooldown."""
    if not is_engagement_degraded():
        return

    wait_seconds = get_engagement_cooldown_seconds() or 300
    raise RateLimitError(
        "Session temporarily degraded for engagement actions after a recent LinkedIn challenge.",
        suggested_wait_time=wait_seconds,
        context={"session_health": get_session_health()},
    )


def error_code_from_exception(exc: Exception) -> str:
    """Map internal exceptions to stable error code strings."""
    if isinstance(exc, QuotaExceededError):
        return "quota_exceeded"
    if isinstance(exc, ConcurrencyError):
        return "concurrency_error"
    if isinstance(exc, RateLimitError):
        return "rate_limit"
    if isinstance(exc, ElementNotFoundError):
        return "element_not_found"
    if isinstance(exc, SelectorError):
        return "selector_error"
    if isinstance(exc, InteractionError):
        return "interaction_error"
    if isinstance(exc, ProfileNotFoundError):
        return "profile_not_found"
    if isinstance(exc, AuthenticationError):
        return "authentication_failed"
    if isinstance(exc, SessionExpiredError):
        return "session_expired"
    if isinstance(exc, CredentialsNotFoundError):
        return "authentication_not_found"
    if isinstance(exc, NetworkError):
        return "network_error"
    if isinstance(exc, ScrapingError):
        return "scraping_error"
    if isinstance(exc, ValueError):
        return "validation_error"
    return "unknown_error"


_LEGACY_RESOLUTIONS: dict[str, str] = {
    "authentication_not_found": "Run with --login to create a browser profile.",
    "session_expired": "Run with --login to create a new browser profile.",
    "authentication_failed": "Run with --login to re-authenticate.",
    "rate_limit": "LinkedIn rate limit detected. Wait before trying again.",
    "profile_not_found": "Check the profile URL is correct and the profile exists.",
    "element_not_found": "LinkedIn page structure may have changed. Please report this issue.",
    "selector_error": "LinkedIn UI may have changed. Check selector telemetry and update locator chains.",
    "interaction_error": "Retry with visible browser mode to inspect UI state.",
    "network_error": "Check your network connection and try again.",
    "scraping_error": "Failed to extract data from LinkedIn. The page structure may have changed.",
}


async def run_legacy_read_tool(
    action: str,
    fetch_fn: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run a read tool with the standard envelope plus legacy top-level keys.

    Pre-envelope tools (person/company/job) returned their payload at the top
    level on success and ``{error, message, resolution}`` on failure. This
    wrapper produces the standard ``run_read_tool`` envelope and mirrors those
    legacy keys additively so existing clients keep working for one release.
    """
    result = await run_read_tool(action, fetch_fn)

    if result.get("status") == "success":
        for key, value in (result.get("data") or {}).items():
            result.setdefault(key, value)
    else:
        error_code = str(result.get("error_code") or "unknown_error")
        result.setdefault("error", error_code)
        resolution = _LEGACY_RESOLUTIONS.get(error_code)
        if resolution:
            result.setdefault("resolution", resolution)

    return result


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


async def ensure_page_healthy(page: Any) -> None:
    """Pre-flight check that the page is not in a CAPTCHA/challenge state.

    Call this before attempting UI interactions (clicks, form fills) to
    fail fast instead of burning 20+ seconds on doomed attempts.

    Raises:
        RateLimitError: If the page shows CAPTCHA or security challenge indicators.
    """
    await detect_rate_limit(page)


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
        warnings: list[str] | None = None
        if "_warnings" in payload:
            warnings = [str(item) for item in payload.pop("_warnings") or [] if item]
        result = read_success(action=action, data=payload, warnings=warnings or None)
    except RateLimitError as exc:
        message = str(exc)
        if _should_record_security_challenge(exc):
            await record_security_challenge()

        warnings: list[str] = []
        wait_seconds = getattr(exc, "suggested_wait_time", None)
        if isinstance(wait_seconds, int) and wait_seconds > 0:
            warnings.append(f"Suggested wait: {wait_seconds}s before retrying.")
        challenge_type = getattr(exc, "challenge_type", None)
        if challenge_type:
            warnings.append(f"Challenge type: {challenge_type}.")

        result = read_error(
            action=action,
            message=message,
            error_code="rate_limit",
            warnings=warnings or None,
        )
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

        if dry_run:
            result = write_dry_run(action, description)
            return result

        await require_confirmation(action, confirm)

        await acquire_write_lock(action)
        lock_acquired = True

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
        session_health: dict[str, Any] | None = None
        if _should_record_security_challenge(exc):
            session_health = await record_security_challenge()
        else:
            session_health = get_session_health()

        cooldown_until = None
        wait_seconds = getattr(exc, "suggested_wait_time", None)
        if isinstance(wait_seconds, int) and wait_seconds > 0:
            cooldown = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
            cooldown_until = cooldown.isoformat().replace("+00:00", "Z")

        challenge_type = getattr(exc, "challenge_type", None)
        if challenge_type in {"captcha", "checkpoint"}:
            error_code = "captcha_required"
        else:
            error_code = "rate_limit"

        result = write_error(
            action=action,
            message=message,
            error_code=error_code,
            cooldown_until=cooldown_until,
            data=_merge_rate_limit_data(
                getattr(exc, "context", None),
                challenge_type=challenge_type,
                session_health=session_health,
            ),
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


def _should_record_security_challenge(exc: RateLimitError) -> bool:
    challenge_type = getattr(exc, "challenge_type", None)
    if challenge_type in {"captcha", "checkpoint"}:
        return True

    lowered = str(exc).lower()
    return any(token in lowered for token in ("captcha", "challenge", "checkpoint"))


def _merge_rate_limit_data(
    context: dict[str, Any] | None,
    *,
    challenge_type: str | None,
    session_health: dict[str, Any] | None,
) -> dict[str, Any] | None:
    data: dict[str, Any] = {}
    if context:
        data.update(context)
    if challenge_type:
        data["challenge_type"] = challenge_type
    if session_health:
        data["session_health"] = session_health
    return data or None
