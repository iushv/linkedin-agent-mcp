"""Tests for core/auth.py login detection and manual-login flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from linkedin_mcp_server.core.auth import (
    is_logged_in,
    wait_for_manual_login,
    warm_up_browser,
)
from linkedin_mcp_server.core.exceptions import AuthenticationError


def _page_with(url: str, *, old_nav: int = 0, new_nav: int = 0) -> MagicMock:
    """Build a mock page whose nav-selector counts are scenario-driven."""
    page = MagicMock()
    page.url = url

    def _locator(selector: str) -> MagicMock:
        locator = MagicMock()
        if "global-nav" in selector:
            locator.count = AsyncMock(return_value=old_nav)
        else:
            locator.count = AsyncMock(return_value=new_nav)
        return locator

    page.locator = MagicMock(side_effect=_locator)
    return page


class TestIsLoggedIn:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "blocked_url",
        [
            "https://www.linkedin.com/login",
            "https://www.linkedin.com/authwall?x=1",
            "https://www.linkedin.com/checkpoint/lg/login-submit",
            "https://www.linkedin.com/uas/login",
        ],
    )
    async def test_auth_blocker_urls_fail_fast(self, blocked_url):
        page = _page_with(blocked_url, old_nav=5, new_nav=5)
        assert await is_logged_in(page) is False
        page.locator.assert_not_called()

    @pytest.mark.asyncio
    async def test_old_nav_selectors_mean_logged_in(self):
        page = _page_with("https://www.linkedin.com/in/someone/", old_nav=1)
        assert await is_logged_in(page) is True

    @pytest.mark.asyncio
    async def test_new_nav_selectors_mean_logged_in(self):
        page = _page_with("https://www.linkedin.com/in/someone/", new_nav=2)
        assert await is_logged_in(page) is True

    @pytest.mark.asyncio
    async def test_authenticated_url_fallback(self):
        page = _page_with("https://www.linkedin.com/feed/")
        assert await is_logged_in(page) is True

    @pytest.mark.asyncio
    async def test_no_nav_and_neutral_url_means_logged_out(self):
        page = _page_with("https://www.linkedin.com/in/someone/")
        assert await is_logged_in(page) is False

    @pytest.mark.asyncio
    async def test_timeout_treated_as_logged_out(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/in/someone/"
        locator = MagicMock()
        locator.count = AsyncMock(side_effect=PlaywrightTimeoutError("slow"))
        page.locator = MagicMock(return_value=locator)

        assert await is_logged_in(page) is False

    @pytest.mark.asyncio
    async def test_unexpected_error_propagates(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/in/someone/"
        locator = MagicMock()
        locator.count = AsyncMock(side_effect=RuntimeError("browser crashed"))
        page.locator = MagicMock(return_value=locator)

        with pytest.raises(RuntimeError):
            await is_logged_in(page)


class TestWarmUpBrowser:
    @pytest.mark.asyncio
    async def test_visits_subset_of_sites(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.core.auth.navigation_delay", lambda: 0.0
        )
        page = MagicMock()
        page.goto = AsyncMock()

        await warm_up_browser(page)

        assert 2 <= page.goto.await_count <= 4
        for call in page.goto.await_args_list:
            assert call.args[0].startswith("https://")

    @pytest.mark.asyncio
    async def test_all_sites_unreachable_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.core.auth.navigation_delay", lambda: 0.0
        )
        page = MagicMock()
        page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("offline"))

        await warm_up_browser(page)

        assert page.goto.await_count >= 2


class TestWaitForManualLogin:
    @pytest.mark.asyncio
    async def test_returns_when_already_logged_in(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.core.auth.is_logged_in",
            AsyncMock(return_value=True),
        )

        await wait_for_manual_login(MagicMock(), timeout=1000)

    @pytest.mark.asyncio
    async def test_times_out_with_authentication_error(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.core.auth.is_logged_in",
            AsyncMock(return_value=False),
        )
        monkeypatch.setattr("linkedin_mcp_server.core.auth.asyncio.sleep", AsyncMock())

        with pytest.raises(AuthenticationError, match="timeout"):
            await wait_for_manual_login(MagicMock(), timeout=0)
