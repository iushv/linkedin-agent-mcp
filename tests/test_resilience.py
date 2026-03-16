"""Tests for CAPTCHA cascade prevention and session degradation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from linkedin_mcp_server.core.exceptions import RateLimitError
from linkedin_mcp_server.core.safety import (
    get_captcha_count,
    get_session_health,
    is_session_degraded,
    record_security_challenge,
    reset_safety_state,
)

_COMMON = "linkedin_mcp_server.tools._common"


@pytest.fixture(autouse=True)
def _clean_safety():
    reset_safety_state()
    yield
    reset_safety_state()


class TestSessionDegradation:
    def test_not_degraded_initially(self):
        assert not is_session_degraded()
        assert get_captcha_count() == 0

    @pytest.mark.asyncio
    async def test_degraded_after_one_captcha(self):
        await record_security_challenge()
        assert is_session_degraded()
        assert get_captcha_count() == 1

    @pytest.mark.asyncio
    async def test_health_includes_degraded_flag(self):
        await record_security_challenge()
        health = get_session_health()
        assert health["degraded"] is True

    @pytest.mark.asyncio
    async def test_graduated_captcha_count(self):
        for _ in range(3):
            await record_security_challenge()
        assert get_captcha_count() == 3


class TestRunReadToolCascade:
    """Verify run_read_tool propagates CAPTCHA detection to session health."""

    @pytest.mark.asyncio
    async def test_captcha_error_records_security_challenge(self):
        """When a read tool raises RateLimitError with CAPTCHA, record_security_challenge is called."""
        from linkedin_mcp_server.tools._common import run_read_tool

        async def _fetch_that_hits_captcha():
            raise RateLimitError(
                "CAPTCHA challenge detected.", suggested_wait_time=3600
            )

        with (
            patch(f"{_COMMON}.acquire_browser_lock", new_callable=AsyncMock),
            patch(f"{_COMMON}.release_browser_lock"),
            patch(f"{_COMMON}.ensure_authenticated", new_callable=AsyncMock),
            patch(f"{_COMMON}.check_session_health", new_callable=AsyncMock),
        ):
            result = await run_read_tool(
                action="test_read",
                fetch_fn=_fetch_that_hits_captcha,
            )

        assert result["status"] == "error"
        assert result["error_code"] == "rate_limit"
        # The CAPTCHA should have been recorded
        assert get_captcha_count() == 1
        assert is_session_degraded()

    @pytest.mark.asyncio
    async def test_non_captcha_rate_limit_does_not_record(self):
        """Regular rate limits should NOT trigger record_security_challenge."""
        from linkedin_mcp_server.tools._common import run_read_tool

        async def _fetch_throttled():
            raise RateLimitError("Rate limit message detected.", suggested_wait_time=1800)

        with (
            patch(f"{_COMMON}.acquire_browser_lock", new_callable=AsyncMock),
            patch(f"{_COMMON}.release_browser_lock"),
            patch(f"{_COMMON}.ensure_authenticated", new_callable=AsyncMock),
            patch(f"{_COMMON}.check_session_health", new_callable=AsyncMock),
        ):
            result = await run_read_tool(
                action="test_read",
                fetch_fn=_fetch_throttled,
            )

        assert result["status"] == "error"
        assert get_captcha_count() == 0

    @pytest.mark.asyncio
    async def test_run_read_tool_checks_session_health(self):
        """run_read_tool should call check_session_health before executing."""
        from linkedin_mcp_server.tools._common import run_read_tool

        health_mock = AsyncMock()

        async def _fetch_ok():
            return {"test": "data"}

        with (
            patch(f"{_COMMON}.acquire_browser_lock", new_callable=AsyncMock),
            patch(f"{_COMMON}.release_browser_lock"),
            patch(f"{_COMMON}.ensure_authenticated", new_callable=AsyncMock),
            patch(f"{_COMMON}.check_session_health", health_mock),
        ):
            await run_read_tool(action="test_read", fetch_fn=_fetch_ok)

        health_mock.assert_awaited_once()


class TestProxyConfig:
    """Verify proxy configuration validation."""

    def test_valid_http_proxy(self):
        from linkedin_mcp_server.config.schema import BrowserConfig

        config = BrowserConfig(proxy_server="http://proxy:8080")
        config.validate()  # Should not raise

    def test_valid_socks5_proxy(self):
        from linkedin_mcp_server.config.schema import BrowserConfig

        config = BrowserConfig(proxy_server="socks5://proxy:1080")
        config.validate()  # Should not raise

    def test_invalid_proxy_scheme(self):
        from linkedin_mcp_server.config.schema import BrowserConfig, ConfigurationError

        config = BrowserConfig(proxy_server="ftp://proxy:21")
        with pytest.raises(ConfigurationError, match="proxy_server must start with"):
            config.validate()

    def test_no_proxy_is_valid(self):
        from linkedin_mcp_server.config.schema import BrowserConfig

        config = BrowserConfig()
        config.validate()  # Should not raise
