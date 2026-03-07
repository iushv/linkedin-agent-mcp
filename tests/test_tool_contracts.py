"""Tests for MCP tool annotation schema contracts."""

from __future__ import annotations

import inspect

import pytest


@pytest.fixture(scope="module")
def mcp_server():
    from linkedin_mcp_server.server import create_mcp_server

    return create_mcp_server()


READ_TOOLS = [
    "get_person_profile",
    "get_company_profile",
    "search_jobs",
    "search_people",
    "get_company_people",
    "get_job_details",
    "get_saved_jobs",
    "get_job_recommendations",
    "browse_feed",
]

WRITE_TOOLS_NON_DESTRUCTIVE = [
    "create_post",
    "send_message",
    "react_to_post",
    "send_connection_request",
    "save_job",
    "update_profile_headline",
    "set_open_to_work",
    "add_profile_skills",
    "set_featured_skills",
]

DESTRUCTIVE_TOOLS = ["delete_post"]


class TestAnnotations:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", READ_TOOLS)
    async def test_read_tools_readonly_hint(self, mcp_server, tool_name):
        tool = await mcp_server.get_tool(tool_name)
        assert tool is not None, f"Tool {tool_name} not found"
        annotations = tool.annotations
        assert annotations is not None, f"Tool {tool_name} has no annotations"
        assert annotations.readOnlyHint is True, (
            f"Tool {tool_name} should have readOnlyHint=True"
        )

    @pytest.mark.asyncio
    async def test_destructive_hints(self, mcp_server):
        for name in DESTRUCTIVE_TOOLS:
            tool = await mcp_server.get_tool(name)
            assert tool is not None
            assert tool.annotations.destructiveHint is True, (
                f"{name} should be destructive"
            )

        for name in WRITE_TOOLS_NON_DESTRUCTIVE:
            tool = await mcp_server.get_tool(name)
            assert tool is not None
            assert tool.annotations.destructiveHint is False, (
                f"{name} should NOT be destructive"
            )

    @pytest.mark.asyncio
    async def test_write_tools_accept_confirm_dry_run(self, mcp_server):
        all_write = WRITE_TOOLS_NON_DESTRUCTIVE + DESTRUCTIVE_TOOLS
        for name in all_write:
            tool = await mcp_server.get_tool(name)
            assert tool is not None, f"Tool {name} not found"
            sig = inspect.signature(tool.fn)
            params = set(sig.parameters.keys())
            assert "confirm" in params, f"{name} missing confirm param"
            assert "dry_run" in params, f"{name} missing dry_run param"
