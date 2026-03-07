"""Safety controls for write actions: confirmation, locking, quotas, and audit logs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .exceptions import (
    ConcurrencyError,
    InteractionError,
    QuotaExceededError,
    RateLimitError,
)

STATE_DIR = Path.home() / ".linkedin-mcp"
QUOTAS_FILE = STATE_DIR / "quotas.json"
AUDIT_LOG_FILE = STATE_DIR / "audit.log"
CONFIG_FILE = STATE_DIR / "config.json"

DAILY_QUOTAS: dict[str, int] = {
    "create_post": 10,
    "send_message": 25,
    "send_connection_request": 20,
    "react_to_post": 50,
    "comment_on_post": 30,
}
SESSION_QUOTAS: dict[str, int] = {
    "update_profile_headline": 2,
    "set_open_to_work": 2,
    "add_profile_skills": 2,
    "set_featured_skills": 2,
}

DEFAULT_CONFIRMATION_CONFIG: dict[str, Any] = {
    "auto_approve_write_tools": [],
    "quotas": {},
    "captcha_disable_threshold": 3,
    "captcha_disable_minutes": 15,
}

_write_lock = asyncio.Lock()
_browser_lock = asyncio.Lock()
_state_lock = asyncio.Lock()


@dataclass
class SessionHealth:
    """In-memory session health tracking for repeated challenge handling."""

    consecutive_captchas: int = 0
    disabled_until: datetime | None = None


_session_health = SessionHealth()
_session_quota_counts: dict[str, int] = {}


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


async def _read_json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        if not path.exists():
            return default.copy()
        try:
            parsed = json.loads(path.read_text())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return default.copy()

    return await asyncio.to_thread(_read)


async def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    def _write() -> None:
        _ensure_state_dir()
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    await asyncio.to_thread(_write)


async def load_safety_config() -> dict[str, Any]:
    """Load write-safety configuration from ~/.linkedin-mcp/config.json."""
    config = await _read_json_file(CONFIG_FILE, DEFAULT_CONFIRMATION_CONFIG)

    merged = DEFAULT_CONFIRMATION_CONFIG.copy()
    merged.update(config)
    if not isinstance(merged.get("auto_approve_write_tools"), list):
        merged["auto_approve_write_tools"] = []
    if not isinstance(merged.get("quotas"), dict):
        merged["quotas"] = {}
    return merged


async def require_confirmation(tool_name: str, confirm: bool) -> None:
    """Require explicit confirmation unless tool is auto-approved in config."""
    if confirm:
        return

    config = await load_safety_config()
    auto_approved = {
        str(name) for name in config.get("auto_approve_write_tools", []) if name
    }
    if tool_name in auto_approved:
        return

    raise InteractionError(
        f"Tool '{tool_name}' requires confirm=True unless auto-approved in ~/.linkedin-mcp/config.json",
        action="confirmation_required",
        context={"tool_name": tool_name},
    )


async def acquire_write_lock(tool_name: str, timeout: float = 30) -> None:
    """Acquire exclusive write lock for a tool call."""
    try:
        await asyncio.wait_for(_write_lock.acquire(), timeout=timeout)
    except TimeoutError as exc:
        raise ConcurrencyError(
            f"Timed out acquiring write lock for '{tool_name}'. Retry shortly."
        ) from exc


def release_write_lock() -> None:
    """Release write lock if held."""
    if _write_lock.locked():
        _write_lock.release()


async def acquire_browser_lock(tool_name: str, timeout: float = 60) -> None:
    """Acquire exclusive access to the shared browser page."""
    try:
        await asyncio.wait_for(_browser_lock.acquire(), timeout=timeout)
    except TimeoutError as exc:
        raise ConcurrencyError(
            f"Timed out acquiring browser lock for '{tool_name}'. Retry shortly."
        ) from exc


def release_browser_lock() -> None:
    """Release browser lock if held."""
    if _browser_lock.locked():
        _browser_lock.release()


def _today_key() -> str:
    return date.today().isoformat()


def _default_quota_state() -> dict[str, Any]:
    return {"date": _today_key(), "counts": {}}


async def _read_quota_state() -> dict[str, Any]:
    state = await _read_json_file(QUOTAS_FILE, _default_quota_state())
    if state.get("date") != _today_key():
        return _default_quota_state()
    if not isinstance(state.get("counts"), dict):
        state["counts"] = {}
    return state


async def _write_quota_state(state: dict[str, Any]) -> None:
    await _write_json_file(QUOTAS_FILE, state)


async def _resolve_limit(tool_name: str) -> int | None:
    config = await load_safety_config()
    quota_overrides = config.get("quotas", {})

    if tool_name in quota_overrides:
        try:
            value = int(quota_overrides[tool_name])
            return value if value > 0 else None
        except (TypeError, ValueError):
            return DAILY_QUOTAS.get(tool_name)

    return DAILY_QUOTAS.get(tool_name)


async def check_quota(tool_name: str) -> dict[str, int | None]:
    """Increment and verify daily quota for a tool."""
    session_limit = SESSION_QUOTAS.get(tool_name)
    if session_limit is not None:
        session_used = int(_session_quota_counts.get(tool_name, 0))
        if session_used >= session_limit:
            raise QuotaExceededError(
                f"Session quota exceeded for '{tool_name}' ({session_used}/{session_limit})",
                tool_name=tool_name,
                limit=session_limit,
                used=session_used,
            )

    limit = await _resolve_limit(tool_name)
    if limit is None:
        if session_limit is not None:
            _session_quota_counts[tool_name] = int(_session_quota_counts.get(tool_name, 0)) + 1
        return {"limit": None, "used": None, "remaining": None}

    async with _state_lock:
        state = await _read_quota_state()
        counts = state["counts"]
        used = int(counts.get(tool_name, 0))

        if used >= limit:
            raise QuotaExceededError(
                f"Daily quota exceeded for '{tool_name}' ({used}/{limit})",
                tool_name=tool_name,
                limit=limit,
                used=used,
            )

        used += 1
        counts[tool_name] = used
        state["date"] = _today_key()
        await _write_quota_state(state)

    if session_limit is not None:
        _session_quota_counts[tool_name] = int(_session_quota_counts.get(tool_name, 0)) + 1

    return {"limit": limit, "used": used, "remaining": max(limit - used, 0)}


async def audit_log(
    tool_name: str,
    params: dict[str, Any],
    result: dict[str, Any],
    dry_run: bool,
) -> None:
    """Append an audit entry for write tool invocation."""

    def _write() -> None:
        _ensure_state_dir()
        params_blob = json.dumps(params, sort_keys=True, default=str)
        params_hash = hashlib.sha256(params_blob.encode("utf-8")).hexdigest()[:16]

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tool": tool_name,
            "dry_run": dry_run,
            "params_hash": params_hash,
            "status": result.get("status", "unknown"),
        }
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    await asyncio.to_thread(_write)


async def execute_or_dry_run(
    dry_run: bool,
    description: str,
    action_fn: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Execute an async action or return a dry-run preview payload."""
    if dry_run:
        return {
            "status": "dry_run",
            "would_do": description,
        }

    return await action_fn()


async def check_session_health() -> None:
    """Raise if the current session is temporarily disabled due to challenges."""
    now = datetime.now(timezone.utc)
    if _session_health.disabled_until and now < _session_health.disabled_until:
        seconds = int((_session_health.disabled_until - now).total_seconds())
        raise RateLimitError(
            "Session temporarily disabled after repeated challenges.",
            suggested_wait_time=max(seconds, 1),
        )


async def record_security_challenge() -> dict[str, Any]:
    """Record a security challenge event and disable writes if threshold reached."""
    config = await load_safety_config()
    threshold = int(config.get("captcha_disable_threshold", 3))
    disable_minutes = int(config.get("captcha_disable_minutes", 15))

    _session_health.consecutive_captchas += 1
    if _session_health.consecutive_captchas >= threshold:
        _session_health.disabled_until = datetime.now(timezone.utc) + timedelta(
            minutes=disable_minutes
        )

    return get_session_health()


def record_successful_write() -> None:
    """Reset challenge counter after a successful write path."""
    _session_health.consecutive_captchas = 0
    _session_health.disabled_until = None


def get_session_health() -> dict[str, Any]:
    """Get current in-memory session health state."""
    return {
        "consecutive_captchas": _session_health.consecutive_captchas,
        "disabled_until": _session_health.disabled_until.isoformat() + "Z"
        if _session_health.disabled_until
        else None,
    }


def reset_safety_state() -> None:
    """Reset in-memory safety state for tests."""
    _session_health.consecutive_captchas = 0
    _session_health.disabled_until = None
    _session_quota_counts.clear()
    if _write_lock.locked():
        _write_lock.release()
    if _browser_lock.locked():
        _browser_lock.release()
