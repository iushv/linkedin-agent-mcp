"""Smoke tests for newly added automation tool modules."""

from typing import Any, Callable, Coroutine
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from linkedin_mcp_server.core.exceptions import ElementNotFoundError


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

    async def test_get_engagement_health_returns_read_envelope(self):
        from linkedin_mcp_server.tools.engagement import register_engagement_tools

        mcp = FastMCP("test")
        register_engagement_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_engagement_health")
        result = await tool_fn()

        assert result["status"] == "success"
        assert result["action"] == "get_engagement_health"
        assert "data" in result


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

    async def test_send_connection_request_dismisses_overlay(self, monkeypatch):
        events: list[str] = []

        async def _run_write_tool(*, execute_fn, **kwargs):
            del kwargs
            return await execute_fn()

        page = MagicMock()
        page.get_by_role = MagicMock()
        page.get_by_text = MagicMock()

        connect_button = MagicMock()
        connect_button.count = AsyncMock(return_value=0)

        menu_connect = MagicMock()
        menu_connect.count = AsyncMock(return_value=1)
        menu_connect.first = MagicMock()

        page.get_by_role.side_effect = lambda role, name=None, **kwargs: (
            connect_button
            if role == "button" and name == "Connect"
            else menu_connect
        )
        page.get_by_text.return_value = menu_connect

        browser = MagicMock(page=page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.run_write_tool",
            _run_write_tool,
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.goto_and_check",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.handle_modal_close",
            AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("dismiss")),
        )

        # Replace the SELECTORS chain so .find() returns a stand-in locator.
        more_locator = MagicMock()
        more_actions_chain = MagicMock()
        more_actions_chain.find = AsyncMock(return_value=more_locator)
        from linkedin_mcp_server.core.selectors import SELECTORS
        monkeypatch.setitem(SELECTORS["network"], "more_actions", more_actions_chain)

        # Capture click ordering through the overlay-protected helper.
        async def _record_click(_page, locator, **_kwargs):
            if locator is more_locator:
                events.append("more")
            elif locator is menu_connect.first:
                events.append("menu")
            else:
                events.append("other")

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network._click_with_overlay_protection",
            _record_click,
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.click_element",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.detect_rate_limit_post_action",
            AsyncMock(),
        )

        from linkedin_mcp_server.tools.network import register_network_tools

        mcp = FastMCP("test")
        register_network_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "send_connection_request")
        await tool_fn("https://www.linkedin.com/in/test-user/", confirm=True, dry_run=False)

        assert events[:3] == ["dismiss", "more", "menu"]

    async def test_click_with_overlay_protection_falls_back_to_js_on_intercept(self):
        from linkedin_mcp_server.tools.network import _click_with_overlay_protection

        page = MagicMock()
        page.evaluate = AsyncMock()

        sentinel_handle = object()
        locator = MagicMock()
        locator.click = AsyncMock(side_effect=TimeoutError("click timed out"))
        locator.element_handle = AsyncMock(return_value=sentinel_handle)

        await _click_with_overlay_protection(page, locator, timeout_ms=100)

        page.evaluate.assert_awaited_once()
        evaluated_args = page.evaluate.call_args.args
        assert evaluated_args[1] is sentinel_handle

    async def test_click_with_overlay_protection_raises_overlay_suspected_when_js_also_fails(
        self,
    ):
        from linkedin_mcp_server.tools.network import _click_with_overlay_protection

        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=RuntimeError("js click failed"))

        locator = MagicMock()
        locator.click = AsyncMock(side_effect=TimeoutError("click timed out"))
        locator.element_handle = AsyncMock(return_value=object())

        with pytest.raises(ElementNotFoundError) as exc_info:
            await _click_with_overlay_protection(page, locator, timeout_ms=100)

        assert exc_info.value.context["overlay_suspected"] is True

    async def test_element_not_found_error_constructor_accepts_context(self):
        exc = ElementNotFoundError("missing", context={"k": "v"})
        assert str(exc) == "missing"
        assert exc.context == {"k": "v"}
        # Default empty dict when context omitted
        assert ElementNotFoundError("oops").context == {}

    async def test_send_connection_request_direct_connect_overlay_falls_back_to_js(
        self, monkeypatch
    ):
        async def _run_write_tool(*, execute_fn, **kwargs):
            del kwargs
            return await execute_fn()

        page = MagicMock()
        page.evaluate = AsyncMock()

        connect_button_first = MagicMock()
        connect_button_first.click = AsyncMock(side_effect=TimeoutError("intercepted"))
        connect_button_first.element_handle = AsyncMock(return_value=object())

        connect_button = MagicMock()
        connect_button.count = AsyncMock(return_value=1)
        connect_button.first = connect_button_first

        page.get_by_role = MagicMock(return_value=connect_button)

        browser = MagicMock(page=page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.run_write_tool", _run_write_tool
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.goto_and_check", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.handle_modal_close", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.click_element", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.detect_rate_limit_post_action",
            AsyncMock(),
        )

        from linkedin_mcp_server.tools.network import register_network_tools

        mcp = FastMCP("test")
        register_network_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "send_connection_request")

        await tool_fn(
            "https://www.linkedin.com/in/test-user/", confirm=True, dry_run=False
        )

        page.evaluate.assert_awaited_once()

    async def test_send_connection_request_raises_element_not_found_when_all_strategies_fail(
        self, monkeypatch
    ):
        """All overlay-protected click paths fail → ElementNotFoundError(overlay_suspected)."""
        async def _run_write_tool(*, execute_fn, **kwargs):
            del kwargs
            return await execute_fn()

        page = MagicMock()
        page.get_by_role = MagicMock()
        page.get_by_text = MagicMock()

        connect_button = MagicMock()
        connect_button.count = AsyncMock(return_value=0)

        menu_connect = MagicMock()
        menu_connect.count = AsyncMock(return_value=0)

        page.get_by_role.side_effect = lambda role, name=None, **kwargs: (
            connect_button
            if role == "button" and name == "Connect"
            else menu_connect
        )
        page.get_by_text.return_value = menu_connect

        browser = MagicMock(page=page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.run_write_tool",
            _run_write_tool,
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.goto_and_check",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.handle_modal_close",
            AsyncMock(),
        )
        # The More-actions locator can't even be resolved — this raises before
        # the click helper runs.
        more_actions_chain = MagicMock()
        more_actions_chain.find = AsyncMock(
            side_effect=RuntimeError("locator chain unresolvable")
        )
        from linkedin_mcp_server.core.selectors import SELECTORS
        monkeypatch.setitem(SELECTORS["network"], "more_actions", more_actions_chain)

        from linkedin_mcp_server.tools.network import register_network_tools

        mcp = FastMCP("test")
        register_network_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "send_connection_request")

        with pytest.raises(ElementNotFoundError) as exc_info:
            await tool_fn(
                "https://www.linkedin.com/in/test-user/",
                confirm=True,
                dry_run=False,
            )

        assert exc_info.value.context["overlay_suspected"] is True


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
