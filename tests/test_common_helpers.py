"""Tests for tools/_common.py helper functions and run_read/write_tool pipelines."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from linkedin_mcp_server.core.exceptions import (
    ConcurrencyError,
    InteractionError,
    QuotaExceededError,
    RateLimitError,
    SelectorError,
)
from linkedin_mcp_server.tools._common import (
    error_code_from_exception,
    extract_profile_slug,
    extract_thread_id_from_url,
    goto_and_check,
    normalize_profile_url,
    parse_count,
    run_read_tool,
    run_write_tool,
)


# ---------------------------------------------------------------------------
# normalize_profile_url
# ---------------------------------------------------------------------------


class TestNormalizeProfileUrl:
    def test_full_https(self):
        result = normalize_profile_url("https://www.linkedin.com/in/user/")
        assert result == "https://www.linkedin.com/in/user/"

    def test_bare_username(self):
        result = normalize_profile_url("user")
        assert result == "https://www.linkedin.com/in/user/"

    def test_company_with_scheme(self):
        result = normalize_profile_url("https://www.linkedin.com/company/acme/")
        assert result == "https://www.linkedin.com/company/acme/"

    def test_trailing_slashes(self):
        result = normalize_profile_url("https://www.linkedin.com/in/user///")
        assert result == "https://www.linkedin.com/in/user/"

    def test_no_scheme_company_rejects_or_preserves(self):
        result = normalize_profile_url("/company/acme/")
        assert result == "https://www.linkedin.com/company/acme/"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            normalize_profile_url("")


# ---------------------------------------------------------------------------
# extract_profile_slug
# ---------------------------------------------------------------------------


class TestExtractProfileSlug:
    def test_bare_username(self):
        assert extract_profile_slug("user") == "user"

    def test_full_url(self):
        assert extract_profile_slug("https://www.linkedin.com/in/user/") == "user"

    def test_profile_path(self):
        assert extract_profile_slug("/in/user/") == "user"

    def test_profile_path_without_leading_slash(self):
        assert extract_profile_slug("in/user") == "user"

    def test_company_url_raises(self):
        with pytest.raises(ValueError):
            extract_profile_slug("https://www.linkedin.com/company/acme/")


# ---------------------------------------------------------------------------
# parse_count
# ---------------------------------------------------------------------------


class TestParseCount:
    def test_plain(self):
        assert parse_count("1234") == 1234

    def test_comma(self):
        assert parse_count("1,234") == 1234

    def test_k_suffix(self):
        assert parse_count("2.1k") == 2100

    def test_m_suffix(self):
        assert parse_count("1.5M") == 1500000

    def test_empty(self):
        assert parse_count("") is None

    def test_garbage(self):
        assert parse_count("abc") is None


# ---------------------------------------------------------------------------
# extract_thread_id_from_url
# ---------------------------------------------------------------------------


class TestExtractThreadId:
    def test_valid(self):
        url = "https://www.linkedin.com/messaging/thread/abc123/"
        assert extract_thread_id_from_url(url) == "abc123"

    def test_no_match(self):
        assert extract_thread_id_from_url("/in/user/") is None


# ---------------------------------------------------------------------------
# error_code_from_exception
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc,expected_code",
    [
        (QuotaExceededError("q", tool_name="t", limit=1, used=1), "quota_exceeded"),
        (ConcurrencyError("c"), "concurrency_error"),
        (RateLimitError("r"), "rate_limit"),
        (
            SelectorError("s", chain_name="c", tried_strategies=[], url=None),
            "selector_error",
        ),
        (InteractionError("i", action="a"), "interaction_error"),
        (ValueError("v"), "validation_error"),
        (RuntimeError("r"), "unknown_error"),
    ],
    ids=[
        "quota_exceeded",
        "concurrency",
        "rate_limit",
        "selector",
        "interaction",
        "value_error",
        "runtime_error",
    ],
)
def test_error_code_from_exception(exc, expected_code):
    assert error_code_from_exception(exc) == expected_code


# ---------------------------------------------------------------------------
# run_read_tool
# ---------------------------------------------------------------------------

_SAFETY_PREFIX = "linkedin_mcp_server.tools._common"


class TestRunReadTool:
    @pytest.mark.asyncio
    async def test_success(self):
        with (
            patch(f"{_SAFETY_PREFIX}.ensure_authenticated", new_callable=AsyncMock),
            patch(f"{_SAFETY_PREFIX}.acquire_browser_lock", new_callable=AsyncMock),
            patch(f"{_SAFETY_PREFIX}.release_browser_lock") as mock_release,
        ):
            result = await run_read_tool(
                action="test_read",
                fetch_fn=AsyncMock(return_value={"items": [1, 2]}),
            )
        assert result["status"] == "success"
        assert result["data"] == {"items": [1, 2]}
        mock_release.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        with (
            patch(f"{_SAFETY_PREFIX}.ensure_authenticated", new_callable=AsyncMock),
            patch(f"{_SAFETY_PREFIX}.acquire_browser_lock", new_callable=AsyncMock),
            patch(f"{_SAFETY_PREFIX}.release_browser_lock") as mock_release,
        ):
            result = await run_read_tool(
                action="test_read",
                fetch_fn=AsyncMock(side_effect=ValueError("bad input")),
            )
        assert result["status"] == "error"
        assert result["error_code"] == "validation_error"
        mock_release.assert_called_once()


# ---------------------------------------------------------------------------
# run_write_tool
# ---------------------------------------------------------------------------


class TestRunWriteTool:
    """Test all run_write_tool branches with mocked safety hooks."""

    @pytest.fixture(autouse=True)
    def _mock_safety(self, isolate_safety, monkeypatch):
        """Stub out auth + safety so we can test branch logic in isolation."""
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.ensure_authenticated", AsyncMock())
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.acquire_browser_lock", AsyncMock())
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.release_browser_lock", lambda: None)
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.check_session_health", AsyncMock())
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.require_confirmation", AsyncMock())
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.acquire_write_lock", AsyncMock())
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.release_write_lock", lambda: None)
        monkeypatch.setattr(
            f"{_SAFETY_PREFIX}.check_quota",
            AsyncMock(return_value={"limit": 10, "used": 1, "remaining": 9}),
        )
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.audit_log", AsyncMock())
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.record_successful_write", lambda: None)
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.record_security_challenge", AsyncMock())

    async def _call(self, *, dry_run=False, confirm=True, execute_fn=None):
        if execute_fn is None:
            execute_fn = AsyncMock(
                return_value={"message": "done", "resource_url": "https://x"}
            )
        return await run_write_tool(
            action="test_write",
            params={"text": "hello"},
            dry_run=dry_run,
            confirm=confirm,
            description="Test write action.",
            execute_fn=execute_fn,
        )

    @pytest.mark.asyncio
    async def test_dry_run(self):
        execute_fn = AsyncMock()
        result = await self._call(dry_run=True, execute_fn=execute_fn)
        assert result["status"] == "dry_run"
        execute_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirm_required(self, monkeypatch):
        monkeypatch.setattr(
            f"{_SAFETY_PREFIX}.require_confirmation",
            AsyncMock(
                side_effect=InteractionError(
                    "requires confirm=True", action="confirmation_required"
                )
            ),
        )
        result = await self._call(confirm=False)
        assert result["error_code"] == "interaction_error"

    @pytest.mark.asyncio
    async def test_quota_exceeded(self, monkeypatch):
        monkeypatch.setattr(
            f"{_SAFETY_PREFIX}.check_quota",
            AsyncMock(
                side_effect=QuotaExceededError("quota", tool_name="t", limit=1, used=1)
            ),
        )
        result = await self._call()
        assert result["status"] == "quota_exceeded"

    @pytest.mark.asyncio
    async def test_rate_limit_captcha(self, monkeypatch):
        mock_record = AsyncMock()
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.record_security_challenge", mock_record)
        result = await self._call(
            execute_fn=AsyncMock(side_effect=RateLimitError("captcha detected"))
        )
        assert result["error_code"] == "rate_limit"
        mock_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_releases_lock(self, monkeypatch):
        mock_release = MagicMock()
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.release_write_lock", mock_release)
        mock_browser_release = MagicMock()
        monkeypatch.setattr(
            f"{_SAFETY_PREFIX}.release_browser_lock", mock_browser_release
        )
        mock_audit = AsyncMock()
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.audit_log", mock_audit)

        result = await self._call()
        assert result["status"] == "success"
        mock_release.assert_called_once()
        mock_browser_release.assert_called_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generic_error_releases_lock(self, monkeypatch):
        mock_release = MagicMock()
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.release_write_lock", mock_release)
        mock_browser_release = MagicMock()
        monkeypatch.setattr(
            f"{_SAFETY_PREFIX}.release_browser_lock", mock_browser_release
        )

        result = await self._call(
            execute_fn=AsyncMock(side_effect=RuntimeError("unexpected"))
        )
        assert result["error_code"] == "unknown_error"
        mock_release.assert_called_once()
        mock_browser_release.assert_called_once()


class TestGotoAndCheck:
    @pytest.mark.asyncio
    async def test_uses_explicit_timeout_override(self, monkeypatch):
        page = MagicMock()
        page.goto = AsyncMock()

        monkeypatch.setattr(
            f"{_SAFETY_PREFIX}.get_config",
            lambda: MagicMock(browser=MagicMock(default_timeout=5000)),
        )
        monkeypatch.setattr(f"{_SAFETY_PREFIX}._respect_navigation_gap", AsyncMock())
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.detect_rate_limit", AsyncMock())

        await goto_and_check(
            page,
            "https://www.linkedin.com/jobs/search/?keywords=python",
            timeout_ms=45000,
        )

        page.goto.assert_awaited_once_with(
            "https://www.linkedin.com/jobs/search/?keywords=python",
            wait_until="domcontentloaded",
            timeout=45000,
        )

    @pytest.mark.asyncio
    async def test_retries_timeout_once(self, monkeypatch):
        page = MagicMock()
        page.goto = AsyncMock(side_effect=[PlaywrightTimeoutError("boom"), None])

        monkeypatch.setattr(
            f"{_SAFETY_PREFIX}.get_config",
            lambda: MagicMock(browser=MagicMock(default_timeout=5000)),
        )
        monkeypatch.setattr(f"{_SAFETY_PREFIX}._respect_navigation_gap", AsyncMock())
        mock_backoff = AsyncMock(return_value=0.0)
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.backoff_with_jitter", mock_backoff)
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.detect_rate_limit", AsyncMock())

        await goto_and_check(page, "https://www.linkedin.com/feed/")

        assert page.goto.await_count == 2
        mock_backoff.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_retry_captcha_rate_limit(self, monkeypatch):
        page = MagicMock()
        page.goto = AsyncMock(
            side_effect=RateLimitError(
                "CAPTCHA challenge detected.",
                suggested_wait_time=3600,
            )
        )

        monkeypatch.setattr(
            f"{_SAFETY_PREFIX}.get_config",
            lambda: MagicMock(browser=MagicMock(default_timeout=5000)),
        )
        monkeypatch.setattr(f"{_SAFETY_PREFIX}._respect_navigation_gap", AsyncMock())
        mock_backoff = AsyncMock(return_value=0.0)
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.backoff_with_jitter", mock_backoff)
        monkeypatch.setattr(f"{_SAFETY_PREFIX}.detect_rate_limit", AsyncMock())

        with pytest.raises(RateLimitError):
            await goto_and_check(page, "https://www.linkedin.com/feed/")

        mock_backoff.assert_not_awaited()
