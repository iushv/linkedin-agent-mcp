"""Tests for saved-jobs queue tools."""

from __future__ import annotations

from typing import Any, Callable, Coroutine, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP

from tests.helpers.page_mocks import MockPageBuilder

_COMMON = "linkedin_mcp_server.tools._common"
_SAVED = "linkedin_mcp_server.tools.saved_jobs"


async def get_tool_fn(
    mcp: Any, name: str
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    tool = await mcp.get_tool(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' not found")
    return cast(Callable[..., Coroutine[Any, Any, dict[str, Any]]], tool.fn)


def _make_browser(page: MagicMock) -> MagicMock:
    browser = MagicMock()
    browser.page = page
    return browser


@pytest.fixture(autouse=True)
def _patch_auth():
    with patch(f"{_COMMON}.ensure_authenticated", new_callable=AsyncMock):
        yield


class TestSaveJob:
    @pytest.mark.asyncio
    async def test_save_job_dry_run(self, isolate_safety):
        from linkedin_mcp_server.tools.saved_jobs import register_saved_job_tools

        mcp = FastMCP("test")
        register_saved_job_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "save_job")

        result = await tool_fn(
            job_url="https://www.linkedin.com/jobs/view/4252026496/",
            confirm=True,
            dry_run=True,
        )

        assert result["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_save_job_success(self, isolate_safety):
        from linkedin_mcp_server.tools.saved_jobs import register_saved_job_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/jobs/view/4252026496")
            .build()
        )
        body = MagicMock()
        body.inner_text = AsyncMock(
            return_value="Senior AI Engineer\nMastercard\nSingapore"
        )
        page.locator = MagicMock(return_value=body)

        mcp = FastMCP("test")
        register_saved_job_tools(mcp)

        unsave_locator = MagicMock()
        unsave_locator.count = AsyncMock(return_value=0)
        unsave_locator.first = unsave_locator
        save_chain = MagicMock()
        unsave_chain = MagicMock()
        unsave_chain.find = AsyncMock(return_value=unsave_locator)

        with (
            patch(
                f"{_SAVED}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=_make_browser(page),
            ),
            patch(f"{_SAVED}.goto_and_check", new_callable=AsyncMock),
            patch(f"{_SAVED}.click_element", new_callable=AsyncMock),
            patch(f"{_SAVED}.detect_rate_limit_post_action", new_callable=AsyncMock),
            patch(
                f"{_SAVED}.SELECTORS",
                {"jobs": {"save_button": save_chain, "unsave_button": unsave_chain}},
            ),
        ):
            tool_fn = await get_tool_fn(mcp, "save_job")
            result = await tool_fn(
                job_url="https://www.linkedin.com/jobs/view/4252026496",
                confirm=True,
                dry_run=False,
            )

        assert result["status"] == "success"
        assert result["data"]["job_id"] == "4252026496"
        assert (
            result["data"]["job_url"] == "https://www.linkedin.com/jobs/view/4252026496"
        )


class TestGetSavedJobs:
    @pytest.mark.asyncio
    async def test_get_saved_jobs_parses_cards(self):
        from linkedin_mcp_server.tools.saved_jobs import register_saved_job_tools

        row = MagicMock()
        row.inner_text = AsyncMock(
            return_value=(
                "Senior AI Engineer\nMastercard\nSingapore\n2 days ago\nSaved"
            )
        )
        link = MagicMock()
        link.count = AsyncMock(return_value=1)
        link.get_attribute = AsyncMock(return_value="/jobs/view/4252026496/")
        row.locator = MagicMock(return_value=MagicMock(first=link))

        rows = MagicMock()
        rows.count = AsyncMock(return_value=1)
        rows.nth = lambda idx: row

        page = MagicMock()
        browser = _make_browser(page)
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)

        mcp = FastMCP("test")
        register_saved_job_tools(mcp)

        with (
            patch(
                f"{_SAVED}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(f"{_SAVED}.goto_and_check", new_callable=AsyncMock),
            patch(f"{_SAVED}.SELECTORS", {"jobs": {"saved_job_cards": chain}}),
        ):
            tool_fn = await get_tool_fn(mcp, "get_saved_jobs")
            result = await tool_fn(limit=10)

        assert result["status"] == "success"
        data = result["data"]
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["title"] == "Senior AI Engineer"
        assert data["jobs"][0]["company"] == "Mastercard"
        assert data["jobs"][0]["job_id"] == "4252026496"

    @pytest.mark.asyncio
    async def test_get_saved_jobs_drops_invalid_rows(self):
        from linkedin_mcp_server.tools.saved_jobs import register_saved_job_tools

        invalid_row = MagicMock()
        invalid_row.inner_text = AsyncMock(return_value="Incomplete")
        invalid_link = MagicMock()
        invalid_link.count = AsyncMock(return_value=0)
        invalid_row.locator = MagicMock(return_value=MagicMock(first=invalid_link))

        rows = MagicMock()
        rows.count = AsyncMock(return_value=1)
        rows.nth = lambda idx: invalid_row

        page = MagicMock()
        browser = _make_browser(page)
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)

        mcp = FastMCP("test")
        register_saved_job_tools(mcp)

        with (
            patch(
                f"{_SAVED}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(f"{_SAVED}.goto_and_check", new_callable=AsyncMock),
            patch(f"{_SAVED}.SELECTORS", {"jobs": {"saved_job_cards": chain}}),
        ):
            tool_fn = await get_tool_fn(mcp, "get_saved_jobs")
            result = await tool_fn(limit=10)

        assert result["status"] == "success"
        assert result["data"]["jobs"] == []

    @pytest.mark.asyncio
    async def test_get_saved_jobs_returns_pagination_metadata(self):
        from linkedin_mcp_server.core.pagination import encode_next_cursor
        from linkedin_mcp_server.tools.saved_jobs import register_saved_job_tools

        row = MagicMock()
        row.inner_text = AsyncMock(
            return_value="Senior AI Engineer\nMastercard\nSingapore\n2 days ago"
        )
        link = MagicMock()
        link.count = AsyncMock(return_value=1)
        link.get_attribute = AsyncMock(return_value="/jobs/view/4252026496/")
        row.locator = MagicMock(return_value=MagicMock(first=link))

        rows = MagicMock()
        rows.count = AsyncMock(return_value=1)
        rows.nth = lambda idx: row

        page = MagicMock()
        browser = _make_browser(page)
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)

        mcp = FastMCP("test")
        register_saved_job_tools(mcp)

        with (
            patch(
                f"{_SAVED}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(f"{_SAVED}.goto_and_check", new_callable=AsyncMock),
            patch(f"{_SAVED}.SELECTORS", {"jobs": {"saved_job_cards": chain}}),
        ):
            tool_fn = await get_tool_fn(mcp, "get_saved_jobs")
            result = await tool_fn(limit=1, next_cursor=encode_next_cursor(3))

        assert result["status"] == "success"
        assert result["data"]["page"] == 3
