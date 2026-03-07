"""Tests for job recommendation tools."""

from __future__ import annotations

from typing import Any, Callable, Coroutine, cast
from unittest.mock import AsyncMock, MagicMock

from fastmcp import FastMCP


async def get_tool_fn(
    mcp: Any, name: str
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    tool = await mcp.get_tool(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' not found")
    return cast(Callable[..., Coroutine[Any, Any, dict[str, Any]]], tool.fn)


def _make_recommendation_row(
    *,
    title: str | None,
    company: str | None,
    location: str | None,
    href: str | None,
) -> MagicMock:
    row = MagicMock()

    def _locator(selector: str):
        locator = MagicMock()
        if "jobs/view" in selector:
            locator.count = AsyncMock(return_value=1 if href else 0)
            locator.first = MagicMock(get_attribute=AsyncMock(return_value=href))
            return locator

        if "title" in selector and title:
            locator.count = AsyncMock(return_value=1)
            locator.first = MagicMock(inner_text=AsyncMock(return_value=title))
            return locator

        if "subtitle" in selector or "company" in selector:
            locator.count = AsyncMock(return_value=1 if company else 0)
            locator.first = MagicMock(inner_text=AsyncMock(return_value=company))
            return locator

        locator.count = AsyncMock(return_value=1 if location else 0)
        locator.first = MagicMock(inner_text=AsyncMock(return_value=location))
        return locator

    row.locator = MagicMock(side_effect=_locator)
    return row


class TestJobRecommendations:
    async def test_recommendations_parse_valid_cards(self, monkeypatch):
        rows = MagicMock()
        rows.count = AsyncMock(return_value=2)
        rows.nth = lambda idx: [
            _make_recommendation_row(
                title="Senior AI Engineer",
                company="Mastercard",
                location="Singapore",
                href="/jobs/view/4252026496/",
            ),
            _make_recommendation_row(
                title="Noise Row",
                company=None,
                location=None,
                href=None,
            ),
        ][idx]

        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        page = MagicMock()
        browser = MagicMock(page=page)

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.recommendations.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.recommendations.goto_and_check",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.recommendations.SELECTORS",
            {"jobs": {"recommendation_cards": chain}},
        )

        from linkedin_mcp_server.tools.recommendations import (
            register_recommendation_tools,
        )

        mcp = FastMCP("test")
        register_recommendation_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_job_recommendations")
        result = await tool_fn(limit=10)

        assert result["status"] == "success"
        jobs = result["data"]["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Senior AI Engineer"
        assert jobs[0]["job_id"] == "4252026496"

    async def test_recommendations_pagination_metadata(self, monkeypatch):
        rows = MagicMock()
        rows.count = AsyncMock(return_value=1)
        rows.nth = lambda idx: _make_recommendation_row(
            title="Senior AI Engineer",
            company="Mastercard",
            location="Singapore",
            href="/jobs/view/4252026496/",
        )

        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        page = MagicMock()
        browser = MagicMock(page=page)

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.recommendations.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.recommendations.goto_and_check",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.recommendations.SELECTORS",
            {"jobs": {"recommendation_cards": chain}},
        )

        from linkedin_mcp_server.core.pagination import encode_next_cursor
        from linkedin_mcp_server.tools.recommendations import (
            register_recommendation_tools,
        )

        mcp = FastMCP("test")
        register_recommendation_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_job_recommendations")
        result = await tool_fn(limit=1, next_cursor=encode_next_cursor(2))

        assert result["status"] == "success"
        assert result["data"]["page"] == 2

    async def test_recommendations_fallback_parses_body_text(self, monkeypatch):
        chain = MagicMock()
        chain.resolve = AsyncMock(side_effect=RuntimeError("no cards"))
        page = MagicMock()
        body = MagicMock()
        body.inner_text = AsyncMock(
            return_value="\n".join(
                [
                    "Top job picks for you",
                    "Based on your profile, preferences, and activity like applies, searches, and saves",
                    "Gen AI Developer (Verified job)",
                    "Gen AI Developer",
                    "PwC India",
                    "•",
                    "Kolkata (Hybrid)",
                    "Actively reviewing applicants",
                    "Promoted",
                    "Easy Apply",
                    "AI Engineer (LLM & Voice Pipeline)",
                    "Uplers",
                    "•",
                    "New Delhi (Hybrid)",
                    "1 company alumni works here",
                    "Promoted",
                ]
            )
        )
        page.locator = MagicMock(return_value=body)
        browser = MagicMock(page=page)

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.recommendations.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.recommendations.goto_and_check",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.recommendations.SELECTORS",
            {"jobs": {"recommendation_cards": chain}},
        )

        from linkedin_mcp_server.tools.recommendations import (
            register_recommendation_tools,
        )

        mcp = FastMCP("test")
        register_recommendation_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_job_recommendations")
        result = await tool_fn(limit=10)

        assert result["status"] == "success"
        jobs = result["data"]["jobs"]
        assert len(jobs) == 2
        assert jobs[0]["title"] == "Gen AI Developer"
        assert jobs[0]["company"] == "PwC India"
        assert jobs[0]["location"] == "Kolkata (Hybrid)"
        assert jobs[1]["title"] == "AI Engineer (LLM & Voice Pipeline)"
