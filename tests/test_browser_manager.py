"""Tests for core/browser.py BrowserManager cookie methods."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_mcp_server.core.browser import BrowserManager


class TestNormalizeCookieDomain:
    def test_www_domain(self):
        cookie = {"name": "li_at", "domain": ".www.linkedin.com"}
        result = BrowserManager._normalize_cookie_domain(cookie)
        assert result["domain"] == ".linkedin.com"

    def test_already_correct(self):
        cookie = {"name": "li_at", "domain": ".linkedin.com"}
        result = BrowserManager._normalize_cookie_domain(cookie)
        assert result["domain"] == ".linkedin.com"


class TestExportCookies:
    @pytest.mark.asyncio
    async def test_filters_non_linkedin(self, tmp_path):
        bm = BrowserManager(user_data_dir=tmp_path / "profile")
        bm._context = MagicMock()
        bm._context.cookies = AsyncMock(
            return_value=[
                {"name": "li_at", "domain": ".linkedin.com", "value": "abc"},
                {"name": "GAID", "domain": ".google.com", "value": "xyz"},
            ]
        )

        cookie_path = tmp_path / "cookies.json"
        result = await bm.export_cookies(cookie_path)

        assert result is True
        cookies = json.loads(cookie_path.read_text())
        assert len(cookies) == 1
        assert cookies[0]["name"] == "li_at"


class TestImportCookies:
    @pytest.mark.asyncio
    async def test_only_auth_cookies(self, tmp_path):
        cookie_path = tmp_path / "cookies.json"
        cookie_path.write_text(
            json.dumps(
                [
                    {"name": "li_at", "value": "a", "domain": ".linkedin.com"},
                    {"name": "li_rm", "value": "b", "domain": ".linkedin.com"},
                    {"name": "JSESSIONID", "value": "c", "domain": ".linkedin.com"},
                ]
            )
        )

        bm = BrowserManager(user_data_dir=tmp_path / "profile")
        bm._context = MagicMock()
        bm._context.clear_cookies = AsyncMock()
        bm._context.add_cookies = AsyncMock()

        result = await bm.import_cookies(cookie_path)
        assert result is True

        added = bm._context.add_cookies.call_args[0][0]
        names = {c["name"] for c in added}
        assert names == {"li_at", "li_rm"}

    @pytest.mark.asyncio
    async def test_missing_file(self, tmp_path):
        bm = BrowserManager(user_data_dir=tmp_path / "profile")
        bm._context = MagicMock()

        result = await bm.import_cookies(tmp_path / "nonexistent.json")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_file(self, tmp_path):
        cookie_path = tmp_path / "cookies.json"
        cookie_path.write_text("[]")

        bm = BrowserManager(user_data_dir=tmp_path / "profile")
        bm._context = MagicMock()

        result = await bm.import_cookies(cookie_path)
        assert result is False

    @pytest.mark.asyncio
    async def test_corrupt_file(self, tmp_path):
        cookie_path = tmp_path / "cookies.json"
        cookie_path.write_text("not json ###")

        bm = BrowserManager(user_data_dir=tmp_path / "profile")
        bm._context = MagicMock()

        result = await bm.import_cookies(cookie_path)
        assert result is False
