"""P1 golden-path integration tests for write tools.

These tests exercise the REAL run_write_tool pipeline (lock → quota → confirm →
execute → audit) with only browser page IO mocked.

Key: patch where imported, not where defined.
  - click_element in tools.post → patch tools.post.click_element
  - get_or_create_browser in tools.post → patch tools.post.get_or_create_browser
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers.page_mocks import MockPageBuilder

# Common patch target
_COMMON = "linkedin_mcp_server.tools._common"


async def get_tool_fn(
    mcp: Any, name: str
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """Extract tool function from FastMCP by name using public API."""
    tool = await mcp.get_tool(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' not found")
    return tool.fn


async def _goto_and_check(p, url):
    """Async side_effect for goto_and_check to avoid unawaited coroutine warnings."""
    await p.goto(url)


def _make_browser(page: MagicMock) -> MagicMock:
    browser = MagicMock()
    browser.page = page
    return browser


@pytest.fixture(autouse=True)
def _patch_auth():
    """Stub ensure_authenticated in _common where run_write_tool calls it."""
    with patch(f"{_COMMON}.ensure_authenticated", new_callable=AsyncMock):
        yield


# ---------------------------------------------------------------------------
# create_post
# ---------------------------------------------------------------------------

_POST = "linkedin_mcp_server.tools.post"


class TestCreatePostGoldenPath:
    @pytest.mark.asyncio
    async def test_creates_post_through_full_pipeline(self, isolate_safety):
        from fastmcp import FastMCP

        from linkedin_mcp_server.tools.post import register_post_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/feed/")
            .with_modal(visible=True)
            .with_post_url("https://www.linkedin.com/feed/update/urn:li:activity:123/")
            .build()
        )

        browser = _make_browser(page)
        mcp = FastMCP("test")
        register_post_tools(mcp)

        with (
            patch(
                f"{_POST}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_POST}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_POST}.click_element", new_callable=AsyncMock),
            patch(f"{_POST}.type_text", new_callable=AsyncMock),
            patch(f"{_POST}.wait_for_modal", new_callable=AsyncMock),
            patch(f"{_POST}.dismiss_modal", new_callable=AsyncMock),
            patch(f"{_POST}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "create_post")
            result = await tool_fn(
                text="Hello LinkedIn!",
                confirm=True,
                dry_run=False,
            )

        assert result["status"] == "success"
        assert "create_post" in result.get("action", "")

    @pytest.mark.asyncio
    async def test_dry_run_skips_execution(self, isolate_safety):
        from fastmcp import FastMCP

        from linkedin_mcp_server.tools.post import register_post_tools

        mcp = FastMCP("test")
        register_post_tools(mcp)

        # dry_run exits before execute_fn — browser never called
        tool_fn = await get_tool_fn(mcp, "create_post")
        result = await tool_fn(
            text="test",
            confirm=True,
            dry_run=True,
        )

        assert result["status"] == "dry_run"


# ---------------------------------------------------------------------------
# react_to_post
# ---------------------------------------------------------------------------

_ENGAGE = "linkedin_mcp_server.tools.engagement"


class TestReactToPostGoldenPath:
    @pytest.mark.asyncio
    async def test_reacts_through_full_pipeline(self, isolate_safety):
        from fastmcp import FastMCP

        from linkedin_mcp_server.tools.engagement import register_engagement_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/feed/update/urn:li:activity:123/")
            .on_role("button", "Like", count=1)
            .build()
        )

        browser = _make_browser(page)
        mcp = FastMCP("test")
        register_engagement_tools(mcp)

        mock_like_locator = MagicMock()
        mock_like_locator.hover = AsyncMock()
        mock_like_locator.click = AsyncMock()
        mock_like_locator.first = mock_like_locator

        with (
            patch(
                f"{_ENGAGE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_ENGAGE}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_ENGAGE}.click_element", new_callable=AsyncMock),
            patch(f"{_ENGAGE}.detect_rate_limit_post_action", new_callable=AsyncMock),
            patch(
                f"{_ENGAGE}.SELECTORS",
                {
                    "engagement": {
                        "like": MagicMock(
                            find=AsyncMock(return_value=mock_like_locator)
                        )
                    },
                },
            ),
        ):
            tool_fn = await get_tool_fn(mcp, "react_to_post")
            result = await tool_fn(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
                reaction="like",
                confirm=True,
                dry_run=False,
            )

        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------

_MSG = "linkedin_mcp_server.tools.messaging"


class TestSendMessageGoldenPath:
    @pytest.mark.asyncio
    async def test_sends_message_through_full_pipeline(self, isolate_safety):
        from fastmcp import FastMCP

        from linkedin_mcp_server.tools.messaging import register_messaging_tools

        page = (
            MockPageBuilder().on_goto("https://www.linkedin.com/in/testuser/").build()
        )
        page.url = "https://www.linkedin.com/messaging/thread/abc123/"

        browser = _make_browser(page)
        mcp = FastMCP("test")
        register_messaging_tools(mcp)

        with (
            patch(
                f"{_MSG}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_MSG}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_MSG}.click_element", new_callable=AsyncMock),
            patch(f"{_MSG}.type_text", new_callable=AsyncMock),
            patch(f"{_MSG}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "send_message")
            result = await tool_fn(
                profile_url="testuser",
                text="Hello!",
                confirm=True,
                dry_run=False,
            )

        assert result["status"] == "success"
        assert "Message sent" in result["message"]


# ---------------------------------------------------------------------------
# send_connection_request
# ---------------------------------------------------------------------------

_NET = "linkedin_mcp_server.tools.network"


class TestSendConnectionGoldenPath:
    @pytest.mark.asyncio
    async def test_sends_connection_through_full_pipeline(self, isolate_safety):
        from fastmcp import FastMCP

        from linkedin_mcp_server.tools.network import register_network_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/in/testuser/")
            .on_role("button", "Connect", count=1)
            .build()
        )

        browser = _make_browser(page)
        mcp = FastMCP("test")
        register_network_tools(mcp)

        with (
            patch(
                f"{_NET}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_NET}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_NET}.click_element", new_callable=AsyncMock),
            patch(f"{_NET}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "send_connection_request")
            result = await tool_fn(
                profile_url="testuser",
                confirm=True,
                dry_run=False,
            )

        assert result["status"] == "success"
        assert "Connection request sent" in result["message"]


# ---------------------------------------------------------------------------
# create_poll
# ---------------------------------------------------------------------------


class TestCreatePoll:
    @pytest.mark.asyncio
    async def test_dry_run(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.post import register_post_tools

        mcp = FastMCP("test")
        register_post_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "create_poll")
        result = await tool_fn(
            question="Test?",
            options=["A", "B"],
            confirm=True,
            dry_run=True,
        )
        assert result["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_success_uses_select_option(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.post import register_post_tools

        duration_locator = MagicMock()
        duration_locator.select_option = AsyncMock()

        poll_q_chain = MagicMock(
            find=AsyncMock(return_value=MagicMock(fill=AsyncMock()))
        )
        duration_chain = MagicMock(find=AsyncMock(return_value=duration_locator))

        selectors = {
            "post_composer": {
                "trigger": MagicMock(),
                "text_editor": MagicMock(),
                "poll_button": MagicMock(),
                "poll_question": poll_q_chain,
                "poll_option_1": MagicMock(
                    find=AsyncMock(
                        return_value=MagicMock(
                            count=AsyncMock(return_value=1),
                            first=MagicMock(fill=AsyncMock()),
                        )
                    )
                ),
                "poll_option_2": MagicMock(
                    find=AsyncMock(
                        return_value=MagicMock(
                            count=AsyncMock(return_value=1),
                            first=MagicMock(fill=AsyncMock()),
                        )
                    )
                ),
                "duration_dropdown": duration_chain,
                "submit": MagicMock(),
            },
        }

        page = MockPageBuilder().on_goto("https://www.linkedin.com/feed/").build()
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_post_tools(mcp)

        with (
            patch(
                f"{_POST}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_POST}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_POST}.click_element", new_callable=AsyncMock),
            patch(f"{_POST}.type_text", new_callable=AsyncMock),
            patch(f"{_POST}.wait_for_modal", new_callable=AsyncMock),
            patch(f"{_POST}.dismiss_modal", new_callable=AsyncMock),
            patch(f"{_POST}.detect_rate_limit_post_action", new_callable=AsyncMock),
            patch(f"{_POST}.SELECTORS", selectors),
        ):
            tool_fn = await get_tool_fn(mcp, "create_poll")
            result = await tool_fn(
                question="Test?",
                options=["A", "B"],
                confirm=True,
                dry_run=False,
            )

        assert result["status"] == "success"
        duration_locator.select_option.assert_awaited_once()


# ---------------------------------------------------------------------------
# delete_post
# ---------------------------------------------------------------------------


class TestDeletePost:
    @pytest.mark.asyncio
    async def test_dry_run(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.post import register_post_tools

        mcp = FastMCP("test")
        register_post_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "delete_post")
        result = await tool_fn(
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
            confirm=True,
            dry_run=True,
        )
        assert result["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_success(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.post import register_post_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/feed/update/urn:li:activity:123/")
            .build()
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_post_tools(mcp)

        with (
            patch(
                f"{_POST}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_POST}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_POST}.click_element", new_callable=AsyncMock),
            patch(f"{_POST}.click_and_confirm", new_callable=AsyncMock),
            patch(f"{_POST}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "delete_post")
            result = await tool_fn(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# repost
# ---------------------------------------------------------------------------


class TestRepost:
    @pytest.mark.asyncio
    async def test_repost_instant(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.post import register_post_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/feed/update/urn:li:activity:123/")
            .build()
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_post_tools(mcp)

        with (
            patch(
                f"{_POST}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_POST}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_POST}.click_element", new_callable=AsyncMock),
            patch(f"{_POST}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "repost")
            result = await tool_fn(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_repost_with_comment(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.post import register_post_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/feed/update/urn:li:activity:123/")
            .build()
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_post_tools(mcp)

        with (
            patch(
                f"{_POST}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_POST}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_POST}.click_element", new_callable=AsyncMock),
            patch(f"{_POST}.type_text", new_callable=AsyncMock),
            patch(f"{_POST}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "repost")
            result = await tool_fn(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
                comment="Great post!",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# comment_on_post
# ---------------------------------------------------------------------------


class TestCommentOnPost:
    @pytest.mark.asyncio
    async def test_dry_run(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.engagement import register_engagement_tools

        mcp = FastMCP("test")
        register_engagement_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "comment_on_post")
        result = await tool_fn(
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
            text="Great!",
            confirm=True,
            dry_run=True,
        )
        assert result["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_success(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.engagement import register_engagement_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/feed/update/urn:li:activity:123/")
            .build()
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_engagement_tools(mcp)

        with (
            patch(
                f"{_ENGAGE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_ENGAGE}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_ENGAGE}.click_element", new_callable=AsyncMock),
            patch(f"{_ENGAGE}.type_text", new_callable=AsyncMock),
            patch(f"{_ENGAGE}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "comment_on_post")
            result = await tool_fn(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
                text="Great!",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# reply_to_comment
# ---------------------------------------------------------------------------


class TestReplyToComment:
    @pytest.mark.asyncio
    async def test_success(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.engagement import register_engagement_tools

        reply_btn = MagicMock()
        reply_btn.click = AsyncMock()
        reply_box = MagicMock()
        reply_box.count = AsyncMock(return_value=1)
        reply_box.click = AsyncMock()
        reply_box.fill = AsyncMock()
        reply_box.type = AsyncMock()
        post_btn = MagicMock()
        post_btn.click = AsyncMock()

        comment = MagicMock()
        comment.scroll_into_view_if_needed = AsyncMock()

        def _locator(sel):
            """Match the exact selectors used by reply_to_comment._execute:
            1. "button:has-text('Reply')" → reply_btn
            2. "div.comments-comment-box__editor, textarea..." → reply_box
            3. "button:has-text('Post')" → post_btn
            """
            m = MagicMock()
            if "has-text('Post')" in sel:
                m.first = post_btn
            elif "has-text('Reply')" in sel and "editor" not in sel:
                m.first = reply_btn
            else:
                # editor / textarea selector
                m.first = reply_box
                m.count = reply_box.count
            return m

        comment.locator = _locator

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/feed/update/urn:li:activity:123/")
            .build()
        )
        page.locator = MagicMock(
            return_value=MagicMock(nth=MagicMock(return_value=comment))
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_engagement_tools(mcp)

        with (
            patch(
                f"{_ENGAGE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_ENGAGE}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_ENGAGE}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "reply_to_comment")
            result = await tool_fn(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
                comment_index=0,
                text="Thanks!",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_negative_index_error_envelope(self, isolate_safety):
        """comment_index < 0 → ValueError wrapped by run_write_tool as validation_error."""
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.engagement import register_engagement_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/feed/update/urn:li:activity:123/")
            .build()
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_engagement_tools(mcp)

        with (
            patch(
                f"{_ENGAGE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_ENGAGE}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_ENGAGE}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "reply_to_comment")
            result = await tool_fn(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
                comment_index=-1,
                text="bad",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "error"
        assert result["error_code"] == "validation_error"


# ---------------------------------------------------------------------------
# like_comment
# ---------------------------------------------------------------------------


class TestLikeComment:
    @pytest.mark.asyncio
    async def test_dry_run(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.engagement import register_engagement_tools

        mcp = FastMCP("test")
        register_engagement_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "like_comment")
        result = await tool_fn(
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
            comment_index=0,
            confirm=True,
            dry_run=True,
        )
        assert result["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_success(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.engagement import register_engagement_tools

        like_btn = MagicMock()
        like_btn.count = AsyncMock(return_value=1)
        like_btn.click = AsyncMock()

        comment = MagicMock()
        comment.scroll_into_view_if_needed = AsyncMock()
        comment.locator = MagicMock(
            return_value=MagicMock(first=like_btn, count=like_btn.count)
        )

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/feed/update/urn:li:activity:123/")
            .build()
        )
        page.locator = MagicMock(
            return_value=MagicMock(nth=MagicMock(return_value=comment))
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_engagement_tools(mcp)

        with (
            patch(
                f"{_ENGAGE}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_ENGAGE}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_ENGAGE}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "like_comment")
            result = await tool_fn(
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
                comment_index=0,
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# follow_person
# ---------------------------------------------------------------------------


class TestFollowPerson:
    @pytest.mark.asyncio
    async def test_dry_run(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.network import register_network_tools

        mcp = FastMCP("test")
        register_network_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "follow_person")
        result = await tool_fn(
            profile_url="testuser",
            confirm=True,
            dry_run=True,
        )
        assert result["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_success_direct_follow(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.network import register_network_tools

        follow_btn = MagicMock()
        follow_btn.count = AsyncMock(return_value=1)
        follow_btn.first = MagicMock(click=AsyncMock())

        page = (
            MockPageBuilder().on_goto("https://www.linkedin.com/in/testuser/").build()
        )
        page.get_by_role = MagicMock(return_value=follow_btn)
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_network_tools(mcp)

        with (
            patch(
                f"{_NET}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_NET}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_NET}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "follow_person")
            result = await tool_fn(
                profile_url="testuser",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# respond_to_invitation
# ---------------------------------------------------------------------------


class TestRespondToInvitation:
    @pytest.mark.asyncio
    async def test_accept(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.network import register_network_tools

        accept_btn = MagicMock()
        accept_btn.count = AsyncMock(return_value=1)
        accept_btn.first = MagicMock(click=AsyncMock())

        anchor = MagicMock()
        anchor.count = AsyncMock(return_value=1)
        anchor.get_attribute = AsyncMock(return_value="/in/testuser")

        row = MagicMock()
        row.locator = MagicMock(
            return_value=MagicMock(first=anchor, count=anchor.count)
        )
        row.get_by_role = MagicMock(return_value=accept_btn)

        loc = MagicMock()
        loc.count = AsyncMock(return_value=1)
        loc.nth = MagicMock(return_value=row)

        chain_mock = MagicMock(resolve=AsyncMock(return_value=loc))

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/mynetwork/invitation-manager/")
            .build()
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_network_tools(mcp)

        with (
            patch(
                f"{_NET}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_NET}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_NET}.detect_rate_limit_post_action", new_callable=AsyncMock),
            patch(f"{_NET}.SELECTORS", {"network": {"invitation_rows": chain_mock}}),
        ):
            tool_fn = await get_tool_fn(mcp, "respond_to_invitation")
            result = await tool_fn(
                profile_url="testuser",
                action="accept",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_decline(self, isolate_safety):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.network import register_network_tools

        mcp = FastMCP("test")
        register_network_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "respond_to_invitation")
        # Test via dry_run to verify decline action is accepted
        result = await tool_fn(
            profile_url="testuser",
            action="decline",
            confirm=True,
            dry_run=True,
        )
        assert result["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_invalid_action_error_envelope(self, isolate_safety):
        """Invalid action → ValueError wrapped by run_write_tool as validation_error."""
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.network import register_network_tools

        page = (
            MockPageBuilder()
            .on_goto("https://www.linkedin.com/mynetwork/invitation-manager/")
            .build()
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_network_tools(mcp)

        with (
            patch(
                f"{_NET}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_NET}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
        ):
            tool_fn = await get_tool_fn(mcp, "respond_to_invitation")
            result = await tool_fn(
                profile_url="testuser",
                action="xyz",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "error"
        assert result["error_code"] == "validation_error"


# ---------------------------------------------------------------------------
# UI Fallback Branches
# ---------------------------------------------------------------------------


class TestConnectionRequestFallback:
    @pytest.mark.asyncio
    async def test_more_actions_connect_fallback(self, isolate_safety):
        """When no direct Connect button, falls back to More actions → menuitem Connect."""
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.network import register_network_tools

        # No direct Connect button (count=0)
        connect_btn = MagicMock()
        connect_btn.count = AsyncMock(return_value=0)

        # Menuitem Connect found
        menu_connect = MagicMock()
        menu_connect.count = AsyncMock(return_value=1)
        menu_connect.first = MagicMock(click=AsyncMock())

        page = (
            MockPageBuilder().on_goto("https://www.linkedin.com/in/testuser/").build()
        )
        page.get_by_role = MagicMock(
            side_effect=lambda role, name=None, **kw: (
                connect_btn
                if role == "button" and name == "Connect"
                else menu_connect
                if role == "menuitem" and name == "Connect"
                else MagicMock(count=AsyncMock(return_value=0))
            )
        )
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_network_tools(mcp)

        with (
            patch(
                f"{_NET}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_NET}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_NET}.click_element", new_callable=AsyncMock),
            patch(f"{_NET}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "send_connection_request")
            result = await tool_fn(
                profile_url="testuser",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"
        menu_connect.first.click.assert_awaited_once()


class TestFollowPersonFallback:
    @pytest.mark.asyncio
    async def test_more_actions_follow_fallback(self, isolate_safety):
        """When no direct Follow button, falls back to More actions → menuitem Follow."""
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.network import register_network_tools

        # No direct Follow button (count=0)
        follow_btn = MagicMock()
        follow_btn.count = AsyncMock(return_value=0)

        # Menuitem Follow found
        menu_follow = MagicMock()
        menu_follow.count = AsyncMock(return_value=1)
        menu_follow.first = MagicMock(click=AsyncMock())

        page = (
            MockPageBuilder().on_goto("https://www.linkedin.com/in/testuser/").build()
        )
        page.get_by_role = MagicMock(
            side_effect=lambda role, name=None, **kw: (
                follow_btn
                if role == "button" and name == "Follow"
                else menu_follow
                if role == "menuitem" and name == "Follow"
                else MagicMock(count=AsyncMock(return_value=0))
            )
        )
        page.get_by_text = MagicMock(return_value=menu_follow)
        browser = _make_browser(page)

        mcp = FastMCP("test")
        register_network_tools(mcp)

        with (
            patch(
                f"{_NET}.get_or_create_browser",
                new_callable=AsyncMock,
                return_value=browser,
            ),
            patch(
                f"{_NET}.goto_and_check",
                new_callable=AsyncMock,
                side_effect=_goto_and_check,
            ),
            patch(f"{_NET}.click_element", new_callable=AsyncMock),
            patch(f"{_NET}.detect_rate_limit_post_action", new_callable=AsyncMock),
        ):
            tool_fn = await get_tool_fn(mcp, "follow_person")
            result = await tool_fn(
                profile_url="testuser",
                confirm=True,
                dry_run=False,
            )
        assert result["status"] == "success"
