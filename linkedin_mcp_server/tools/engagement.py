"""Post engagement tools: reactions, comments, and comment interactions."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.interactions import click_element, type_text
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core.utils import detect_rate_limit_post_action
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools._common import goto_and_check, run_write_tool

logger = logging.getLogger(__name__)

ALLOWED_REACTIONS = {
    "like": "Like",
    "celebrate": "Celebrate",
    "support": "Support",
    "funny": "Funny",
    "love": "Love",
    "insightful": "Insightful",
}


def _comment_locator(page: Any, comment_index: int):
    if comment_index < 0:
        raise ValueError("comment_index must be >= 0")
    return page.locator("article.comments-comment-item, li.comments-comment-item").nth(
        comment_index
    )


def register_engagement_tools(mcp: FastMCP) -> None:
    """Register engagement tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="React To Post",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def react_to_post(
        post_url: str,
        ctx: Context | None = None,
        reaction: str = "like",
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """React to a LinkedIn post with a specific reaction."""

        async def _execute() -> dict[str, Any]:
            normalized = reaction.strip().lower()
            if normalized not in ALLOWED_REACTIONS:
                raise ValueError(
                    f"Unsupported reaction '{reaction}'. Allowed: {', '.join(sorted(ALLOWED_REACTIONS))}"
                )

            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(progress=0, total=100, message="Loading post")

            await goto_and_check(page, post_url)
            like_button = await SELECTORS["engagement"]["like"].find(page)
            await like_button.hover()

            reaction_name = ALLOWED_REACTIONS[normalized]
            picker = page.get_by_role("button", name=reaction_name)
            if await picker.count() > 0:
                await picker.first.click()
            else:
                # Fallback to simple like click if picker isn't available.
                await click_element(page, SELECTORS["engagement"]["like"])

            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Reaction added"
                )

            return {
                "message": f"Applied '{normalized}' reaction.",
                "resource_url": post_url,
            }

        return await run_write_tool(
            action="react_to_post",
            params={"post_url": post_url, "reaction": reaction},
            dry_run=dry_run,
            confirm=confirm,
            description=f"React to LinkedIn post at {post_url}.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Comment On Post",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def comment_on_post(
        post_url: str,
        text: str,
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Add a comment to a LinkedIn post."""

        async def _execute() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(progress=0, total=100, message="Loading post")

            await goto_and_check(page, post_url)
            await click_element(page, SELECTORS["engagement"]["comment_input"])
            await type_text(page, SELECTORS["engagement"]["comment_input"], text)
            await click_element(page, SELECTORS["engagement"]["comment_post"])
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Comment posted"
                )

            return {"message": "Comment posted.", "resource_url": post_url}

        return await run_write_tool(
            action="comment_on_post",
            params={"post_url": post_url, "text": text},
            dry_run=dry_run,
            confirm=confirm,
            description=f"Comment on LinkedIn post at {post_url}.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Reply To Comment",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def reply_to_comment(
        post_url: str,
        comment_index: int,
        text: str,
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Reply to the Nth comment on a LinkedIn post."""

        async def _execute() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading comments"
                )

            await goto_and_check(page, post_url)
            comment = _comment_locator(page, comment_index)
            await comment.scroll_into_view_if_needed()
            await comment.locator("button:has-text('Reply')").first.click()

            reply_box = comment.locator(
                "div.comments-comment-box__editor, textarea[placeholder*='Reply' i]"
            ).first
            if await reply_box.count() == 0:
                raise ValueError(
                    f"Reply box not found for comment index {comment_index}"
                )

            await reply_box.click()
            try:
                await reply_box.fill("")
            except Exception:
                pass
            await reply_box.type(text, delay=50)
            await comment.locator("button:has-text('Post')").first.click()
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Reply posted"
                )

            return {
                "message": f"Reply posted to comment index {comment_index}.",
                "resource_url": post_url,
            }

        return await run_write_tool(
            action="reply_to_comment",
            params={
                "post_url": post_url,
                "comment_index": comment_index,
                "text": text,
            },
            dry_run=dry_run,
            confirm=confirm,
            description=f"Reply to comment {comment_index} on post {post_url}.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Like Comment",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def like_comment(
        post_url: str,
        comment_index: int,
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Like the Nth comment on a LinkedIn post."""

        async def _execute() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading comments"
                )

            await goto_and_check(page, post_url)
            comment = _comment_locator(page, comment_index)
            await comment.scroll_into_view_if_needed()
            like_button = comment.locator(
                "button.comments-comment-social-bar__reaction-action, button:has-text('Like')"
            ).first
            if await like_button.count() == 0:
                raise ValueError(
                    f"Like button not found for comment index {comment_index}"
                )
            await like_button.click()
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Comment liked"
                )

            return {
                "message": f"Liked comment index {comment_index}.",
                "resource_url": post_url,
            }

        return await run_write_tool(
            action="like_comment",
            params={"post_url": post_url, "comment_index": comment_index},
            dry_run=dry_run,
            confirm=confirm,
            description=f"Like comment {comment_index} on post {post_url}.",
            execute_fn=_execute,
        )
