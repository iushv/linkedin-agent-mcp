"""Smoke tests for newly added automation tool modules."""

from typing import Any, Callable, Coroutine
from unittest.mock import AsyncMock

from fastmcp import FastMCP


async def get_tool_fn(
    mcp: FastMCP, name: str
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    tool = await mcp.get_tool(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' not found")
    return tool.fn  # type: ignore[attr-defined]


class TestPostTools:
    async def test_create_post_routes_to_write_runner(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.post.run_write_tool",
            AsyncMock(return_value={"status": "dry_run", "action": "create_post"}),
        )

        from linkedin_mcp_server.tools.post import register_post_tools

        mcp = FastMCP("test")
        register_post_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "create_post")
        result = await tool_fn("hello", dry_run=True, confirm=True)

        assert result["status"] == "dry_run"
        assert result["action"] == "create_post"


class TestEngagementTools:
    async def test_react_to_post_routes_to_write_runner(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.engagement.run_write_tool",
            AsyncMock(return_value={"status": "dry_run", "action": "react_to_post"}),
        )

        from linkedin_mcp_server.tools.engagement import register_engagement_tools

        mcp = FastMCP("test")
        register_engagement_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "react_to_post")
        result = await tool_fn(
            "https://www.linkedin.com/feed/update/urn:li:activity:1/",
            dry_run=True,
            confirm=True,
        )

        assert result["status"] == "dry_run"
        assert result["action"] == "react_to_post"


class TestMessagingTools:
    async def test_get_conversations_routes_to_read_runner(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.run_read_tool",
            AsyncMock(
                return_value={
                    "status": "success",
                    "action": "get_conversations",
                    "data": {"conversations": []},
                }
            ),
        )

        from linkedin_mcp_server.tools.messaging import register_messaging_tools

        mcp = FastMCP("test")
        register_messaging_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_conversations")
        result = await tool_fn(limit=3)

        assert result["status"] == "success"
        assert result["action"] == "get_conversations"


class TestNetworkTools:
    async def test_send_connection_routes_to_write_runner(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.run_write_tool",
            AsyncMock(
                return_value={
                    "status": "dry_run",
                    "action": "send_connection_request",
                }
            ),
        )

        from linkedin_mcp_server.tools.network import register_network_tools

        mcp = FastMCP("test")
        register_network_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "send_connection_request")
        result = await tool_fn(
            "https://www.linkedin.com/in/test-user/",
            dry_run=True,
            confirm=True,
        )

        assert result["status"] == "dry_run"
        assert result["action"] == "send_connection_request"


class TestFeedTools:
    async def test_browse_feed_routes_to_read_runner(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.run_read_tool",
            AsyncMock(
                return_value={
                    "status": "success",
                    "action": "browse_feed",
                    "data": {"posts": []},
                }
            ),
        )

        from linkedin_mcp_server.tools.feed import register_feed_tools

        mcp = FastMCP("test")
        register_feed_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "browse_feed")
        result = await tool_fn(count=1)

        assert result["status"] == "success"
        assert result["action"] == "browse_feed"
