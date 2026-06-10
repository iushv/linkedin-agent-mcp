"""Tests for linkedin_mcp_server.drivers.browser singleton lifecycle."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from linkedin_mcp_server.config.schema import AppConfig
from linkedin_mcp_server.drivers.browser import (
    _goto_feed_with_retry,
    get_or_create_browser,
    reset_browser_for_testing,
)


@pytest.fixture(autouse=True)
def _reset_browser():
    """Ensure clean singleton state for each test."""
    reset_browser_for_testing()
    yield
    reset_browser_for_testing()


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch, tmp_path):
    """Provide a test config so get_config() never triggers argparse."""
    config = AppConfig()
    config.browser.user_data_dir = str(tmp_path / "profile")
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.get_config", lambda: config
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.ensure_browser_binary",
        lambda *, headless: None,
    )


def _make_mock_browser(*, logged_in: bool = True) -> MagicMock:
    """Create a mock BrowserManager with controllable login state."""
    browser = MagicMock()
    browser.start = AsyncMock()
    browser.close = AsyncMock()
    browser.page = MagicMock()
    browser.page.goto = AsyncMock()
    browser.page.set_default_timeout = MagicMock()
    browser.import_cookies = AsyncMock(return_value=False)
    browser.export_cookies = AsyncMock(return_value=False)
    return browser


@pytest.mark.asyncio
async def test_get_or_create_browser_auth_success(monkeypatch):
    """Successful auth assigns singleton and returns browser."""
    mock_browser = _make_mock_browser()

    with (
        patch(
            "linkedin_mcp_server.drivers.browser.BrowserManager",
            return_value=mock_browser,
        ),
        patch(
            "linkedin_mcp_server.drivers.browser.is_logged_in",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await get_or_create_browser()

    assert result is mock_browser
    mock_browser.start.assert_awaited_once()
    mock_browser.page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_or_create_browser_auth_failure_cleans_up(monkeypatch):
    """Failed auth closes browser and does NOT assign singleton."""
    from linkedin_mcp_server.core import AuthenticationError

    mock_browser = _make_mock_browser()

    with (
        patch(
            "linkedin_mcp_server.drivers.browser.BrowserManager",
            return_value=mock_browser,
        ),
        patch(
            "linkedin_mcp_server.drivers.browser.is_logged_in",
            new_callable=AsyncMock,
            return_value=False,
        ),
        pytest.raises(AuthenticationError),
    ):
        await get_or_create_browser()

    # Browser must be closed on failure
    mock_browser.close.assert_awaited_once()

    # Singleton must NOT be set — next call should create fresh browser
    from linkedin_mcp_server.drivers.browser import _browser

    assert _browser is None


@pytest.mark.asyncio
async def test_singleton_returns_existing_browser(monkeypatch):
    """Second call returns the same browser instance (singleton)."""
    mock_browser = _make_mock_browser()

    with (
        patch(
            "linkedin_mcp_server.drivers.browser.BrowserManager",
            return_value=mock_browser,
        ) as ctor,
        patch(
            "linkedin_mcp_server.drivers.browser.is_logged_in",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        first = await get_or_create_browser()
        second = await get_or_create_browser()

    assert first is second
    # Constructor should only be called once
    ctor.assert_called_once()


@pytest.mark.asyncio
async def test_goto_feed_with_retry_retries_timeout_once(monkeypatch):
    page = MagicMock()
    page.goto = AsyncMock(
        side_effect=[
            PlaywrightTimeoutError("navigation timed out"),
            None,
        ]
    )

    backoff = AsyncMock(return_value=3.0)
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.backoff_with_jitter", backoff
    )

    await _goto_feed_with_retry(page)

    assert page.goto.await_count == 2
    first_call = page.goto.await_args_list[0]
    assert first_call.kwargs["wait_until"] == "domcontentloaded"
    assert first_call.kwargs["timeout"] == 45_000
    backoff.assert_awaited_once()


@pytest.mark.asyncio
async def test_goto_feed_with_retry_respects_higher_config_timeout(
    monkeypatch, tmp_path
):
    config = AppConfig()
    config.browser.user_data_dir = str(tmp_path / "profile")
    config.browser.default_timeout = 60_000
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.get_config", lambda: config
    )

    page = MagicMock()
    page.goto = AsyncMock(return_value=None)

    await _goto_feed_with_retry(page)

    page.goto.assert_awaited_once()
    assert page.goto.await_args_list[0].kwargs["timeout"] == 60_000
