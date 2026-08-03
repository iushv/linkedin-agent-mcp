"""Tests for write-safety controls (confirmation, quotas, audit, dry-run)."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from linkedin_mcp_server.core.exceptions import (
    ConcurrencyError,
    InteractionError,
    QuotaExceededError,
    RateLimitError,
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
    execute_or_dry_run,
    record_security_challenge,
    record_successful_write,
    release_browser_lock,
    release_write_lock,
    require_confirmation,
    reset_safety_state,
)


@pytest.fixture
def safety_paths(tmp_path, monkeypatch):
    """Redirect safety state files to temporary paths for tests."""
    monkeypatch.setattr("linkedin_mcp_server.core.safety.STATE_DIR", tmp_path)
    monkeypatch.setattr(
        "linkedin_mcp_server.core.safety.QUOTAS_FILE", tmp_path / "quotas.json"
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.core.safety.AUDIT_LOG_FILE", tmp_path / "audit.log"
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.core.safety.CONFIG_FILE", tmp_path / "config.json"
    )
    return tmp_path


class TestConfirmation:
    async def test_require_confirmation_raises_without_confirm(self, safety_paths):
        with pytest.raises(InteractionError, match="requires confirm=True"):
            await require_confirmation("create_post", confirm=False)

    async def test_require_confirmation_accepts_auto_approved(self, safety_paths):
        config = {
            "auto_approve_write_tools": ["create_post"],
            "quotas": {},
            "captcha_disable_threshold": 3,
            "captcha_disable_minutes": 15,
        }
        (safety_paths / "config.json").write_text(json.dumps(config))

        await require_confirmation("create_post", confirm=False)  # no raise


class TestQuota:
    async def test_check_quota_increments_and_limits(self, safety_paths, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.core.safety.DAILY_QUOTAS", {"create_post": 1}
        )

        first = await check_quota("create_post")
        assert first["used"] == 1
        assert first["limit"] == 1

        with pytest.raises(QuotaExceededError):
            await check_quota("create_post")

    async def test_check_quota_unlimited_tool(self, safety_paths, monkeypatch):
        monkeypatch.setattr("linkedin_mcp_server.core.safety.DAILY_QUOTAS", {})
        result = await check_quota("unknown_tool")
        assert result["limit"] is None

    async def test_check_quota_enforces_session_limit(self, safety_paths, monkeypatch):
        monkeypatch.setattr("linkedin_mcp_server.core.safety.DAILY_QUOTAS", {})

        await check_quota("update_profile_headline")
        await check_quota("update_profile_headline")

        with pytest.raises(QuotaExceededError):
            await check_quota("update_profile_headline")


class TestDryRun:
    async def test_execute_or_dry_run_returns_preview(self):
        async def _action():
            return {"status": "success"}

        result = await execute_or_dry_run(
            dry_run=True,
            description="Would create a post",
            action_fn=_action,
        )
        assert result["status"] == "dry_run"
        assert "Would create" in result["would_do"]

    async def test_execute_or_dry_run_executes_action(self):
        async def _action():
            return {"status": "success", "message": "done"}

        result = await execute_or_dry_run(
            dry_run=False,
            description="unused",
            action_fn=_action,
        )
        assert result["status"] == "success"


# --- P7: Safety mechanisms ---


class TestWriteLock:
    async def test_acquire_release(self, safety_paths, monkeypatch):
        import linkedin_mcp_server.core.safety as safety

        # Reset lock state
        if safety._write_lock.locked():
            safety._write_lock.release()

        await acquire_write_lock("test_tool")
        assert safety._write_lock.locked()
        release_write_lock()
        assert not safety._write_lock.locked()

    async def test_double_acquire_raises(self, safety_paths, monkeypatch):
        import linkedin_mcp_server.core.safety as safety

        if safety._write_lock.locked():
            safety._write_lock.release()

        await acquire_write_lock("test_tool")
        with pytest.raises(ConcurrencyError):
            await acquire_write_lock("test_tool_2", timeout=0.01)

        # Cleanup
        release_write_lock()


class TestBrowserLock:
    async def test_acquire_release(self, safety_paths, monkeypatch):
        import linkedin_mcp_server.core.safety as safety

        if safety._browser_lock.locked():
            safety._browser_lock.release()

        await acquire_browser_lock("test_tool")
        assert safety._browser_lock.locked()
        release_browser_lock()
        assert not safety._browser_lock.locked()

    async def test_double_acquire_raises(self, safety_paths, monkeypatch):
        import linkedin_mcp_server.core.safety as safety

        if safety._browser_lock.locked():
            safety._browser_lock.release()

        await acquire_browser_lock("test_tool")
        with pytest.raises(ConcurrencyError):
            await acquire_browser_lock("test_tool_2", timeout=0.01)

        release_browser_lock()


class TestAuditLog:
    async def test_writes_entry(self, safety_paths):
        await audit_log(
            tool_name="create_post",
            params={"text": "hello"},
            result={"status": "success"},
            dry_run=False,
        )

        audit_file = safety_paths / "audit.log"
        assert audit_file.exists()
        entry = json.loads(audit_file.read_text().strip())
        assert entry["tool"] == "create_post"
        assert entry["dry_run"] is False
        assert "params_hash" in entry
        assert "timestamp" in entry
        assert entry["status"] == "success"


class TestSessionHealth:
    async def test_first_challenge_degrades_without_disabling(
        self, safety_paths, monkeypatch
    ):
        import linkedin_mcp_server.core.safety as safety

        safety.reset_safety_state()

        health = await record_security_challenge()
        assert health["consecutive_captchas"] == 1
        assert health["disabled_until"] is None
        assert health["degraded_until"] is not None
        assert is_engagement_degraded() is True
        assert get_engagement_cooldown_seconds() is not None

    async def test_blocks_after_challenges(self, safety_paths, monkeypatch):
        import linkedin_mcp_server.core.safety as safety

        safety._session_health.consecutive_captchas = 0
        safety._session_health.degraded_until = None
        safety._session_health.disabled_until = None

        # Hit threshold (default = 3)
        for _ in range(3):
            await record_security_challenge()

        with pytest.raises(RateLimitError, match="temporarily disabled"):
            await check_session_health()


class TestEngagementHealthTool:
    async def test_returns_standard_read_envelope(self, safety_paths):
        from fastmcp import FastMCP

        from linkedin_mcp_server.tools.engagement import register_engagement_tools
        import linkedin_mcp_server.core.safety as safety

        reset_safety_state()
        safety._session_health.consecutive_captchas = 1
        await record_security_challenge()

        mcp = FastMCP("test")
        register_engagement_tools(mcp)
        tool = await mcp.get_tool("get_engagement_health")

        assert tool is not None
        result = await cast(Any, tool).fn()

        assert result["status"] == "success"
        assert result["action"] == "get_engagement_health"
        assert set(result["data"].keys()) == {
            "degraded",
            "cooldown_seconds_remaining",
            "consecutive_captchas",
            "degraded_until",
            "disabled_until",
        }
        assert result["data"]["degraded"] is True
        assert result["data"]["cooldown_seconds_remaining"] is not None
        assert result["data"]["consecutive_captchas"] >= 1

    async def test_successful_write_resets(self, safety_paths, monkeypatch):
        import linkedin_mcp_server.core.safety as safety

        safety._session_health.consecutive_captchas = 0
        safety._session_health.degraded_until = None
        safety._session_health.disabled_until = None

        # Expired cooldowns should be cleared on the next successful write.
        await record_security_challenge()
        safety._session_health.degraded_until = None

        # Reset via successful write
        record_successful_write()

        # Should pass now
        await check_session_health()
        health = get_session_health()
        assert health["consecutive_captchas"] == 0
        assert health["degraded_until"] is None

    async def test_successful_write_does_not_clear_active_degraded_window(
        self, safety_paths, monkeypatch
    ):
        await record_security_challenge()
        record_successful_write()

        health = get_session_health()
        assert health["consecutive_captchas"] == 1
        assert health["degraded_until"] is not None
