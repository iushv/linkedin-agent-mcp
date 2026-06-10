"""Tests for Patchright browser bootstrap doctor."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from linkedin_mcp_server.config.schema import AppConfig
from linkedin_mcp_server.core import AuthenticationError
from linkedin_mcp_server.drivers.browser import (
    ensure_browser_binary,
    get_or_create_browser,
    reset_browser_binary_check,
    reset_browser_for_testing,
)


def _dry_run_output(chromium_path: Path, shell_path: Path) -> str:
    return (
        "Chrome for Testing 145.0.0.0 (playwright chromium v1208)\n"
        f"  Install location:    {chromium_path}\n"
        "Chrome Headless Shell 145.0.0.0 (playwright chromium-headless-shell v1208)\n"
        f"  Install location:    {shell_path}\n"
    )


@pytest.fixture(autouse=True)
def _reset_browser_state():
    reset_browser_for_testing()
    reset_browser_binary_check()
    yield
    reset_browser_for_testing()
    reset_browser_binary_check()


def test_ensure_browser_binary_returns_when_paths_exist(tmp_path):
    chromium = tmp_path / "chromium-1208"
    shell = tmp_path / "chromium_headless_shell-1208"
    chromium.mkdir(parents=True)
    shell.mkdir(parents=True)

    dry_run = MagicMock(returncode=0, stdout=_dry_run_output(chromium, shell), stderr="")
    with patch(
        "linkedin_mcp_server.drivers.browser.subprocess.run",
        return_value=dry_run,
    ) as mock_run:
        ensure_browser_binary(headless=True)

    assert mock_run.call_count == 1
    assert "--dry-run" in mock_run.call_args[0][0]


def test_ensure_browser_binary_installs_when_missing(tmp_path):
    chromium = tmp_path / "chromium-1208"
    shell = tmp_path / "chromium_headless_shell-1208"
    dry_run = MagicMock(returncode=0, stdout=_dry_run_output(chromium, shell), stderr="")
    install = MagicMock(returncode=0, stdout="installed", stderr="")

    def _run_side_effect(cmd, **kwargs):
        del kwargs
        if "--dry-run" in cmd:
            return dry_run
        chromium.mkdir(parents=True)
        shell.mkdir(parents=True)
        return install

    with patch(
        "linkedin_mcp_server.drivers.browser.subprocess.run",
        side_effect=_run_side_effect,
    ) as mock_run:
        ensure_browser_binary(headless=True)

    assert mock_run.call_count == 2


def test_ensure_browser_binary_install_failure_raises(tmp_path):
    chromium = tmp_path / "chromium-1208"
    shell = tmp_path / "chromium_headless_shell-1208"
    dry_run = MagicMock(returncode=0, stdout=_dry_run_output(chromium, shell), stderr="")
    install = MagicMock(returncode=1, stdout="", stderr="permission denied")

    with patch(
        "linkedin_mcp_server.drivers.browser.subprocess.run",
        side_effect=[dry_run, install],
    ):
        with pytest.raises(RuntimeError, match="patchright install chromium"):
            ensure_browser_binary(headless=True)


def test_ensure_browser_binary_timeout_raises():
    with patch(
        "linkedin_mcp_server.drivers.browser.subprocess.run",
        side_effect=subprocess.TimeoutExpired("cmd", 15),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            ensure_browser_binary(headless=True)


@pytest.mark.asyncio
async def test_get_or_create_browser_runs_doctor_once(monkeypatch, tmp_path):
    config = AppConfig()
    config.browser.user_data_dir = str(tmp_path / "profile")
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.get_config",
        lambda: config,
    )

    doctor = MagicMock()
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.ensure_browser_binary",
        doctor,
    )

    browser = MagicMock()
    browser.start = AsyncMock()
    browser.close = AsyncMock()
    browser.page = MagicMock()
    browser.page.goto = AsyncMock()
    browser.page.set_default_timeout = MagicMock()
    browser.import_cookies = AsyncMock(return_value=False)

    with (
        patch(
            "linkedin_mcp_server.drivers.browser.BrowserManager",
            return_value=browser,
        ),
        patch(
            "linkedin_mcp_server.drivers.browser.is_logged_in",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        first = await get_or_create_browser()
        second = await get_or_create_browser()

    assert first is second
    doctor.assert_called_once_with(headless=True)


@pytest.mark.asyncio
async def test_get_or_create_browser_rechecks_when_headless_requirement_changes(
    monkeypatch, tmp_path
):
    config = AppConfig()
    config.browser.user_data_dir = str(tmp_path / "profile")
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.get_config",
        lambda: config,
    )

    doctor = MagicMock()
    monkeypatch.setattr(
        "linkedin_mcp_server.drivers.browser.ensure_browser_binary",
        doctor,
    )

    browser = MagicMock()
    browser.start = AsyncMock()
    browser.close = AsyncMock()
    browser.page = MagicMock()
    browser.page.goto = AsyncMock()
    browser.page.set_default_timeout = MagicMock()
    browser.import_cookies = AsyncMock(return_value=False)

    with (
        patch(
            "linkedin_mcp_server.drivers.browser.BrowserManager",
            return_value=browser,
        ),
        patch(
            "linkedin_mcp_server.drivers.browser.is_logged_in",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        with pytest.raises(AuthenticationError):
            await get_or_create_browser(headless=False)
        with pytest.raises(AuthenticationError):
            await get_or_create_browser(headless=True)

    assert doctor.call_count == 2
    assert doctor.call_args_list[0].kwargs == {"headless": False}
    assert doctor.call_args_list[1].kwargs == {"headless": True}
