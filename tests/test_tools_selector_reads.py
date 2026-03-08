"""Tests for T2 selector-based read tools: browse_feed, analytics, conversations, invitations."""

from __future__ import annotations

from typing import Any, Callable, Coroutine, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_mcp_server.core.exceptions import SelectorError


# ── Shared helpers ──


def _mock_browser(page=None):
    browser = MagicMock()
    browser.page = page or MagicMock()
    return browser


async def get_tool_fn(
    mcp: Any, name: str
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    tool = await mcp.get_tool(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' not found")
    return cast(Callable[..., Coroutine[Any, Any, dict[str, Any]]], tool.fn)


def _mock_locator_with_items(items: list[str]):
    """Create a mock locator that returns items via .count() and .nth(i).inner_text()."""
    loc = MagicMock()
    loc.count = AsyncMock(return_value=len(items))
    for i, text in enumerate(items):
        nth_mock = MagicMock()
        nth_mock.inner_text = AsyncMock(return_value=text)
        nth_mock.scroll_into_view_if_needed = AsyncMock()
        loc.nth.side_effect = lambda idx, items=items: _make_nth(items, idx)
    return loc


def _make_nth(items, idx):
    m = MagicMock()
    m.inner_text = AsyncMock(return_value=items[idx] if idx < len(items) else "")
    m.scroll_into_view_if_needed = AsyncMock()
    # For invitation rows: nested locator
    anchor = MagicMock()
    anchor.count = AsyncMock(return_value=1)
    anchor.get_attribute = AsyncMock(return_value=f"/in/user-{idx}")
    m.locator = MagicMock(return_value=MagicMock(first=anchor, count=anchor.count))
    # For conversation items: nested link
    link_mock = MagicMock()
    link_mock.count = AsyncMock(return_value=1)
    link_mock.get_attribute = AsyncMock(return_value=f"/messaging/thread/t-{idx}/")
    m.locator = MagicMock(
        return_value=MagicMock(first=link_mock, count=link_mock.count)
    )
    return m


def _make_selector_error(name="test_chain"):
    return SelectorError(
        message=f"Could not resolve selector chain '{name}'",
        chain_name=name,
        tried_strategies=["css:test"],
        url="https://www.linkedin.com/test",
    )


# ══════════════════════════════════════════
# browse_feed
# ══════════════════════════════════════════


class TestBrowseFeed:
    @pytest.fixture(autouse=True)
    def _patch_deps(self, monkeypatch):
        self.page = MagicMock()
        self.page.evaluate = AsyncMock()
        browser = _mock_browser(self.page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.goto_and_check", AsyncMock()
        )

    async def test_browse_feed_success(self, monkeypatch):
        card_texts = [
            "Author One\nSome interesting post\n5 reactions\n2 comments\n2h ago",
            "Author Two\nAnother post\n10 reactions\n1d ago",
            "Author Three\nThird post content",
        ]
        loc = MagicMock()
        loc.count = AsyncMock(return_value=3)
        loc.nth = lambda idx: _make_nth(card_texts, idx)

        chain_mock = MagicMock()
        chain_mock.resolve = AsyncMock(return_value=loc)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.SELECTORS",
            {"feed": {"post_cards": chain_mock}},
        )

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "browse_feed")
        result = await tool_fn(count=3)

        assert result["status"] == "success"
        assert len(result["data"]["posts"]) == 3
        assert result["data"]["posts"][0]["author"] == "Author One"

    async def test_browse_feed_count_clamping(self, monkeypatch):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=0)
        chain_mock = MagicMock()
        chain_mock.resolve = AsyncMock(return_value=loc)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.SELECTORS",
            {"feed": {"post_cards": chain_mock}},
        )

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "browse_feed")
        # count=999 should be clamped to 50 internally (no crash)
        result = await tool_fn(count=999)
        assert result["status"] == "success"

    async def test_browse_feed_stagnant_scroll(self, monkeypatch):
        loc = MagicMock()
        # Always returns 0 cards to trigger stagnation
        loc.count = AsyncMock(return_value=0)
        chain_mock = MagicMock()
        chain_mock.resolve = AsyncMock(return_value=loc)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.SELECTORS",
            {"feed": {"post_cards": chain_mock}},
        )
        monkeypatch.setattr("linkedin_mcp_server.tools.feed.asyncio.sleep", AsyncMock())

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "browse_feed")
        result = await tool_fn(count=5)
        assert result["status"] == "success"
        assert result["data"]["posts"] == []

    async def test_browse_feed_selector_fail(self, monkeypatch):
        chain_mock = MagicMock()
        chain_mock.resolve = AsyncMock(
            side_effect=_make_selector_error("feed_post_cards")
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.SELECTORS",
            {"feed": {"post_cards": chain_mock}},
        )

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "browse_feed")
        result = await tool_fn(count=3)
        assert result["status"] == "error"


# ══════════════════════════════════════════
# get_my_post_analytics
# ══════════════════════════════════════════


class TestMyPostAnalytics:
    @pytest.fixture(autouse=True)
    def _patch_deps(self, monkeypatch):
        self.page = MagicMock()
        self.page.wait_for_selector = AsyncMock()
        self.page.evaluate = AsyncMock()
        browser = _mock_browser(self.page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.goto_and_check", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.handle_modal_close", AsyncMock()
        )

    async def test_analytics_dom_success(self, monkeypatch):
        card_texts = [
            (
                "Feed post number 1\n"
                "Jane Doe\n"
                "Data Consultant | Building AI systems\n"
                "120 impressions\n"
                "1d ago\n"
                "DOM activity post\n"
                "8 reactions\n"
                "3 comments\n"
                "Repost"
            )
        ]
        loc = MagicMock()
        loc.count = AsyncMock(return_value=1)
        loc.nth = lambda idx: _make_nth(card_texts, idx)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed._resolve_activity_post_cards",
            AsyncMock(return_value=loc),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed._extract_post_url",
            AsyncMock(
                return_value="https://www.linkedin.com/feed/update/urn:li:activity:123/"
            ),
        )
        extract_page = AsyncMock(return_value="")
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.LinkedInExtractor",
            MagicMock(return_value=MagicMock(extract_page=extract_page)),
        )

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_my_post_analytics")
        result = await tool_fn()
        assert result["status"] == "success"
        posts = result["data"]["posts"]
        assert len(posts) == 1
        assert posts[0]["author"] == "Jane Doe"
        assert posts[0]["text_preview"] == "DOM activity post"
        assert posts[0]["reactions"] == 8
        assert posts[0]["comments"] == 3
        assert posts[0]["impressions"] == 120
        assert (
            posts[0]["url"]
            == "https://www.linkedin.com/feed/update/urn:li:activity:123/"
        )
        extract_page.assert_not_called()

    async def test_analytics_profile_fallback_when_dom_empty(self, monkeypatch):
        profile_text = (
            "Jane Doe\n"
            "posted this •\n"
            "2h\n"
            "2h\n"
            "My post\n"
            "50\n"
            "10 comments\n"
            "5 reposts\n"
            "React\n"
            "Comment\n"
            "Repost\n"
            "Send\n"
            "Jane Doe\n"
        )
        extract_page = AsyncMock(return_value=profile_text)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.LinkedInExtractor",
            MagicMock(return_value=MagicMock(extract_page=extract_page)),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed._resolve_activity_post_cards",
            AsyncMock(return_value=MagicMock(count=AsyncMock(return_value=0))),
        )
        monkeypatch.setattr("linkedin_mcp_server.tools.feed.asyncio.sleep", AsyncMock())

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_my_post_analytics")
        result = await tool_fn()
        assert result["status"] == "success"
        posts = result["data"]["posts"]
        assert len(posts) == 1
        assert posts[0]["author"] == "Jane Doe"
        assert posts[0]["reactions"] == 50
        assert posts[0]["comments"] == 10
        assert posts[0]["reposts"] == 5
        assert posts[0]["url"] is None
        assert posts[0]["impressions"] is None
        assert posts[0]["time_ago"] is not None

    async def test_analytics_empty_when_dom_and_profile_empty(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.LinkedInExtractor",
            MagicMock(return_value=MagicMock(extract_page=AsyncMock(return_value=""))),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed._resolve_activity_post_cards",
            AsyncMock(return_value=MagicMock(count=AsyncMock(return_value=0))),
        )
        monkeypatch.setattr("linkedin_mcp_server.tools.feed.asyncio.sleep", AsyncMock())

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_my_post_analytics")
        result = await tool_fn()
        assert result["status"] == "success"
        assert result["data"]["posts"] == []

    async def test_analytics_error_when_dom_and_fallback_fail(self, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed._extract_activity_posts_from_dom",
            AsyncMock(side_effect=RuntimeError("dom failed")),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.LinkedInExtractor",
            MagicMock(
                return_value=MagicMock(
                    extract_page=AsyncMock(side_effect=RuntimeError("boom"))
                )
            ),
        )

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_my_post_analytics")
        result = await tool_fn()

        assert result["status"] == "error"


# ══════════════════════════════════════════
# get_profile_analytics
# ══════════════════════════════════════════


class TestProfileAnalytics:
    @pytest.fixture(autouse=True)
    def _patch_deps(self, monkeypatch):
        self.page = MagicMock()
        self.page.wait_for_selector = AsyncMock()
        self.page.evaluate = AsyncMock()
        browser = _mock_browser(self.page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.goto_and_check", AsyncMock()
        )

    async def test_success(self, monkeypatch):
        body_loc = MagicMock()
        body_loc.inner_text = AsyncMock(
            return_value="120 profile views\n45 search appearances\n800 post impressions"
        )
        self.page.locator = MagicMock(return_value=body_loc)

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_profile_analytics")
        result = await tool_fn()
        assert result["status"] == "success"
        data = result["data"]
        assert data["profile_views"] == 120
        assert data["search_appearances"] == 45
        assert data["post_impressions"] == 800

    async def test_missing_metrics(self, monkeypatch):
        body_loc = MagicMock()
        body_loc.inner_text = AsyncMock(return_value="Nothing useful here")
        self.page.locator = MagicMock(return_value=body_loc)

        from linkedin_mcp_server.tools.feed import register_feed_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_profile_analytics")
        result = await tool_fn()
        assert result["status"] == "success"
        data = result["data"]
        assert data["profile_views"] is None
        assert data["search_appearances"] is None
        assert data["post_impressions"] is None


# ══════════════════════════════════════════
# get_conversations
# ══════════════════════════════════════════


class TestGetConversations:
    @pytest.fixture(autouse=True)
    def _patch_deps(self, monkeypatch):
        self.page = MagicMock()
        browser = _mock_browser(self.page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.goto_and_check", AsyncMock()
        )

    async def test_conversations_success(self, monkeypatch):
        row_texts = [
            "Alice Smith\nHey there!\nYesterday",
            "Bob Jones\nUnread message here\n5m ago",
        ]

        def make_row(idx):
            m = MagicMock()
            m.inner_text = AsyncMock(return_value=row_texts[idx])
            # link locator
            link = MagicMock()
            link.count = AsyncMock(return_value=1)
            link.get_attribute = AsyncMock(
                return_value=f"/messaging/thread/thread-{idx}/"
            )
            m.locator = MagicMock(return_value=MagicMock(first=link, count=link.count))
            return m

        loc = MagicMock()
        loc.count = AsyncMock(return_value=2)
        loc.nth = lambda idx: make_row(idx)

        chain_mock = MagicMock()
        chain_mock.resolve = AsyncMock(return_value=loc)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.SELECTORS",
            {"messaging": {"conversation_items": chain_mock}},
        )

        from linkedin_mcp_server.tools.messaging import register_messaging_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_messaging_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_conversations")
        result = await tool_fn()
        assert result["status"] == "success"
        convos = result["data"]["conversations"]
        assert len(convos) == 2
        assert convos[0]["name"] == "Alice Smith"

    async def test_conversations_selector_fail(self, monkeypatch):
        chain_mock = MagicMock()
        chain_mock.resolve = AsyncMock(
            side_effect=_make_selector_error("messaging_conversation_items")
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.SELECTORS",
            {"messaging": {"conversation_items": chain_mock}},
        )

        from linkedin_mcp_server.tools.messaging import register_messaging_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_messaging_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_conversations")
        result = await tool_fn()
        assert result["status"] == "error"


# ══════════════════════════════════════════
# read_conversation
# ══════════════════════════════════════════


class TestReadConversation:
    @pytest.fixture(autouse=True)
    def _patch_deps(self, monkeypatch):
        self.page = MagicMock()
        self.page.url = "https://www.linkedin.com/messaging/thread/t-123/"
        browser = _mock_browser(self.page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.goto_and_check", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.click_element", AsyncMock()
        )

    def _setup_thread_messages(self, monkeypatch, msgs):
        def make_msg(idx):
            m = MagicMock()
            m.inner_text = AsyncMock(return_value=msgs[idx])
            return m

        loc = MagicMock()
        loc.count = AsyncMock(return_value=len(msgs))
        loc.nth = lambda idx: make_msg(idx)

        chain_mock = MagicMock()
        chain_mock.resolve = AsyncMock(return_value=loc)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.SELECTORS",
            {
                "messaging": {"thread_messages": chain_mock},
                "common": {"message_button": MagicMock()},
            },
        )

    async def test_read_by_thread_id(self, monkeypatch):
        self._setup_thread_messages(
            monkeypatch, ["Alice\n2:00 PM\nHello!", "Bob\n2:01 PM\nHi there"]
        )

        from linkedin_mcp_server.tools.messaging import register_messaging_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_messaging_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "read_conversation")
        result = await tool_fn(thread_id="t-123")
        assert result["status"] == "success"
        assert result["data"]["thread_id"] == "t-123"
        assert len(result["data"]["messages"]) == 2

    async def test_read_by_profile_url(self, monkeypatch):
        self.page.url = "https://www.linkedin.com/messaging/"  # no thread in URL
        self._setup_thread_messages(monkeypatch, ["Alice\n2:00 PM\nHello!"])

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.messaging.normalize_profile_url",
            lambda url: f"https://www.linkedin.com/in/{url}/",
        )

        from linkedin_mcp_server.tools.messaging import register_messaging_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_messaging_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "read_conversation")
        result = await tool_fn(profile_url="alice")
        assert result["status"] == "success"
        # thread_id may be None when URL doesn't contain a thread path
        assert "thread_id" in result["data"]

    async def test_read_neither_arg(self, monkeypatch):
        self._setup_thread_messages(monkeypatch, [])

        from linkedin_mcp_server.tools.messaging import register_messaging_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_messaging_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "read_conversation")
        result = await tool_fn()
        assert result["status"] == "error"
        assert result["error_code"] == "validation_error"


# ══════════════════════════════════════════
# get_pending_invitations
# ══════════════════════════════════════════


class TestGetPendingInvitations:
    @pytest.fixture(autouse=True)
    def _patch_deps(self, monkeypatch):
        self.page = MagicMock()
        browser = _mock_browser(self.page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.goto_and_check", AsyncMock()
        )

    def _make_invitation_rows(self, monkeypatch, rows):
        def make_row(idx):
            text, href = rows[idx]
            m = MagicMock()
            m.inner_text = AsyncMock(return_value=text)
            anchor = MagicMock()
            anchor.count = AsyncMock(return_value=1)
            anchor.get_attribute = AsyncMock(return_value=href)
            first_mock = MagicMock()
            first_mock.count = anchor.count
            first_mock.get_attribute = anchor.get_attribute
            m.locator = MagicMock(
                return_value=MagicMock(first=first_mock, count=anchor.count)
            )
            return m

        loc = MagicMock()
        loc.count = AsyncMock(return_value=len(rows))
        loc.nth = lambda idx: make_row(idx)

        chain_mock = MagicMock()
        chain_mock.resolve = AsyncMock(return_value=loc)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.SELECTORS",
            {"network": {"invitation_rows": chain_mock}},
        )

    async def test_invitations_success(self, monkeypatch):
        self._make_invitation_rows(
            monkeypatch,
            [
                ("Jane Doe\nSoftware Engineer\n5 mutual connections", "/in/janedoe"),
                ("Bob Smith\nDesigner", "/in/bobsmith"),
            ],
        )

        from linkedin_mcp_server.tools.network import register_network_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_network_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_pending_invitations")
        result = await tool_fn()
        assert result["status"] == "success"
        invitations = result["data"]["invitations"]
        assert len(invitations) == 2
        assert invitations[0]["name"] == "Jane Doe"
        assert invitations[0]["headline"] == "Software Engineer"
        assert invitations[0]["mutual_connections"] == 5
        assert invitations[0]["invitation_index"] == 0
        assert invitations[0]["profile_url"] == "https://www.linkedin.com/in/janedoe"

    async def test_invitations_relative_href(self, monkeypatch):
        self._make_invitation_rows(
            monkeypatch,
            [
                ("User\nTitle", "/in/relative-path"),
            ],
        )

        from linkedin_mcp_server.tools.network import register_network_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_network_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_pending_invitations")
        result = await tool_fn()
        inv = result["data"]["invitations"][0]
        assert inv["profile_url"].startswith("https://www.linkedin.com")

    async def test_invitations_selector_fail(self, monkeypatch):
        chain_mock = MagicMock()
        chain_mock.resolve = AsyncMock(
            side_effect=_make_selector_error("network_invitation_rows")
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.network.SELECTORS",
            {"network": {"invitation_rows": chain_mock}},
        )

        from linkedin_mcp_server.tools.network import register_network_tools
        from fastmcp import FastMCP

        mcp = FastMCP("test")
        register_network_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_pending_invitations")
        result = await tool_fn()
        assert result["status"] == "error"
