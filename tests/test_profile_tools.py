"""Tests for profile editing tools."""

from __future__ import annotations

from typing import Any, Callable, Coroutine, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP

_COMMON = "linkedin_mcp_server.tools._common"
_PROFILE = "linkedin_mcp_server.tools.profile"


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


def _headline_selectors(
    current: str = "Data Consultant @ EXL",
) -> dict[str, dict[str, Any]]:
    headline_locator = MagicMock()
    headline_locator.inner_text = AsyncMock(return_value=current)

    headline_chain = MagicMock()
    headline_chain.find = AsyncMock(return_value=headline_locator)

    input_locator = MagicMock()
    input_locator.fill = AsyncMock()
    input_chain = MagicMock()
    input_chain.find = AsyncMock(return_value=input_locator)

    click_chain = MagicMock()
    click_chain.find = AsyncMock(return_value=MagicMock())

    return {
        "profile": {
            "headline_text": headline_chain,
            "intro_edit": click_chain,
            "headline_input": input_chain,
            "modal_save": click_chain,
            "open_to_work_button": click_chain,
            "open_to_work_job_title": input_chain,
            "open_to_work_location": input_chain,
            "open_to_work_recruiters_only": click_chain,
            "open_to_work_public": click_chain,
            "open_to_work_remove": click_chain,
            "skills_add_button": click_chain,
            "skill_input": input_chain,
            "featured_skills_button": click_chain,
        }
    }


class TestUpdateProfileHeadline:
    @pytest.mark.asyncio
    async def test_dry_run_returns_preview(self):
        from linkedin_mcp_server.tools.profile import register_profile_tools

        page = MagicMock()
        mcp = FastMCP("test")
        register_profile_tools(mcp)

        with (
            patch(
                f"{_PROFILE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=_make_browser(page),
            ),
            patch(f"{_PROFILE}.goto_and_check", new_callable=AsyncMock),
            patch(f"{_PROFILE}.SELECTORS", _headline_selectors()),
        ):
            tool_fn = await get_tool_fn(mcp, "update_profile_headline")
            result = await tool_fn(
                headline="AI/ML Engineer | Agentic Systems",
                dry_run=True,
                confirm=False,
            )

        assert result["status"] == "dry_run"
        assert result["data"]["previous_headline"] == "Data Consultant @ EXL"
        assert result["data"]["new_headline"] == "AI/ML Engineer | Agentic Systems"

    @pytest.mark.asyncio
    async def test_success_returns_old_and_new_values(self, isolate_safety):
        from linkedin_mcp_server.tools.profile import register_profile_tools

        page = MagicMock()
        selectors = _headline_selectors()
        mcp = FastMCP("test")
        register_profile_tools(mcp)

        with (
            patch(
                f"{_PROFILE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=_make_browser(page),
            ),
            patch(f"{_PROFILE}.goto_and_check", new_callable=AsyncMock),
            patch(f"{_PROFILE}.click_element", new_callable=AsyncMock),
            patch(f"{_PROFILE}.type_text", new_callable=AsyncMock),
            patch(f"{_PROFILE}.wait_for_modal", new_callable=AsyncMock),
            patch(f"{_PROFILE}.detect_rate_limit_post_action", new_callable=AsyncMock),
            patch(f"{_PROFILE}.SELECTORS", selectors),
        ):
            tool_fn = await get_tool_fn(mcp, "update_profile_headline")
            result = await tool_fn(
                headline="AI/ML Engineer | Agentic Systems",
                dry_run=False,
                confirm=True,
            )

        assert result["status"] == "success"
        assert result["data"]["previous_headline"] == "Data Consultant @ EXL"
        assert result["data"]["new_headline"] == "AI/ML Engineer | Agentic Systems"


class TestOpenToWork:
    @pytest.mark.asyncio
    async def test_preview_returns_current_state(self):
        from linkedin_mcp_server.tools.profile import register_profile_tools

        page = MagicMock()
        # The OTW detection iterates selectors calling page.locator(sel).first
        # and checking count(). Return a locator that matches one of them.
        otw_locator = MagicMock()
        otw_first = MagicMock()
        otw_first.count = AsyncMock(return_value=1)
        otw_locator.first = otw_first
        page.locator = MagicMock(return_value=otw_locator)
        mcp = FastMCP("test")
        register_profile_tools(mcp)

        with (
            patch(
                f"{_PROFILE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=_make_browser(page),
            ),
            patch(f"{_PROFILE}.goto_and_check", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "set_open_to_work")
            result = await tool_fn(
                enabled=True,
                visibility="recruiters_only",
                job_titles=["AI Engineer"],
                job_types=["full_time"],
                locations=["Singapore"],
                dry_run=True,
                confirm=False,
            )

        assert result["status"] == "dry_run"
        assert result["data"]["currently_enabled"] is True


class TestAddProfileSkills:
    @pytest.mark.asyncio
    async def test_preview_returns_requested_skills(self):
        from linkedin_mcp_server.tools.profile import register_profile_tools

        mcp = FastMCP("test")
        register_profile_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "add_profile_skills")
        result = await tool_fn(
            skills=["Generative AI", "RAG Pipelines"],
            dry_run=True,
            confirm=False,
        )

        assert result["status"] == "dry_run"
        assert result["data"]["skills_to_add"] == ["Generative AI", "RAG Pipelines"]

    @pytest.mark.asyncio
    async def test_success_returns_added_skills(self, isolate_safety):
        from linkedin_mcp_server.tools.profile import register_profile_tools

        page = MagicMock()
        selectors = _headline_selectors()
        mcp = FastMCP("test")
        register_profile_tools(mcp)

        with (
            patch(
                f"{_PROFILE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=_make_browser(page),
            ),
            patch(f"{_PROFILE}.goto_and_check", new_callable=AsyncMock),
            patch(f"{_PROFILE}.click_element", new_callable=AsyncMock),
            patch(f"{_PROFILE}.wait_for_modal", new_callable=AsyncMock),
            patch(f"{_PROFILE}.detect_rate_limit_post_action", new_callable=AsyncMock),
            patch(f"{_PROFILE}.SELECTORS", selectors),
        ):
            tool_fn = await get_tool_fn(mcp, "add_profile_skills")
            result = await tool_fn(
                skills=["Generative AI", "RAG Pipelines"],
                dry_run=False,
                confirm=True,
            )

        assert result["status"] == "success"
        assert result["data"]["added_skills"] == ["Generative AI", "RAG Pipelines"]


class TestFeaturedSkills:
    @pytest.mark.asyncio
    async def test_failure_returns_current_order_context(self, isolate_safety):
        from linkedin_mcp_server.tools.profile import register_profile_tools

        page = MagicMock()
        page.get_by_text = MagicMock(
            return_value=MagicMock(count=AsyncMock(return_value=0))
        )
        featured_rows = MagicMock()
        featured_rows.count = AsyncMock(return_value=2)
        featured_rows.nth = lambda idx: MagicMock(
            inner_text=AsyncMock(return_value=["Python", "SQL"][idx])
        )

        def _locator(selector: str):
            if selector == "span.pv-skill-category-entity__name-text":
                return featured_rows
            return MagicMock(inner_text=AsyncMock(return_value=""))

        page.locator = MagicMock(side_effect=_locator)
        selectors = _headline_selectors()
        mcp = FastMCP("test")
        register_profile_tools(mcp)

        with (
            patch(
                f"{_PROFILE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=_make_browser(page),
            ),
            patch(f"{_PROFILE}.goto_and_check", new_callable=AsyncMock),
            patch(f"{_PROFILE}.click_element", new_callable=AsyncMock),
            patch(f"{_PROFILE}.wait_for_modal", new_callable=AsyncMock),
            patch(f"{_PROFILE}.SELECTORS", selectors),
        ):
            tool_fn = await get_tool_fn(mcp, "set_featured_skills")
            result = await tool_fn(
                featured_skills=["Generative AI", "Python"],
                dry_run=False,
                confirm=True,
            )

        assert result["status"] == "error"
        assert result["error_code"] == "interaction_error"
        assert result["data"]["current_order"] == ["Python", "SQL"]
