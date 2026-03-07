"""Content publishing and post management tools."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.interactions import (
    click_and_confirm,
    click_element,
    dismiss_modal,
    type_text,
    upload_file,
    wait_for_modal,
)
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core.utils import detect_rate_limit_post_action
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools._common import goto_and_check, run_write_tool

logger = logging.getLogger(__name__)


async def _open_composer(page: Any) -> None:
    await goto_and_check(page, "https://www.linkedin.com/feed/")
    await click_element(page, SELECTORS["post_composer"]["trigger"])
    await wait_for_modal(page)


async def _set_visibility_if_needed(page: Any, visibility: str) -> None:
    normalized = visibility.strip().lower()
    if normalized == "anyone":
        return

    await click_element(page, SELECTORS["post_composer"]["visibility"])
    option_locator = page.get_by_role("radio", name="Connections")
    if await option_locator.count() > 0:
        await option_locator.first.click()
        done_button = page.get_by_role("button", name="Done")
        if await done_button.count() > 0:
            await done_button.first.click()


async def _extract_recent_post_url(page: Any) -> str | None:
    post_link = page.locator('a[href*="/feed/update/"]').first
    if await post_link.count() == 0:
        return None
    href = await post_link.get_attribute("href")
    if not href:
        return None
    if href.startswith("http"):
        return href
    return f"https://www.linkedin.com{href}"


def register_post_tools(mcp: FastMCP) -> None:
    """Register post creation/deletion/repost tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Create Post",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def create_post(
        text: str,
        ctx: Context | None = None,
        visibility: str = "anyone",
        image_path: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create a LinkedIn post with optional image attachment."""

        async def _execute() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Opening composer"
                )

            await _open_composer(page)
            await type_text(page, SELECTORS["post_composer"]["text_editor"], text)

            if image_path:
                await click_element(page, SELECTORS["post_composer"]["media_button"])
                await upload_file(page, SELECTORS["common"]["file_input"], image_path)

            await _set_visibility_if_needed(page, visibility)
            await click_element(page, SELECTORS["post_composer"]["submit"])
            await detect_rate_limit_post_action(page)
            await dismiss_modal(page)

            post_url = await _extract_recent_post_url(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Post created"
                )

            return {
                "message": "Post created successfully.",
                "resource_url": post_url,
            }

        return await run_write_tool(
            action="create_post",
            params={
                "text": text,
                "visibility": visibility,
                "image_path": image_path,
            },
            dry_run=dry_run,
            confirm=confirm,
            description="Create a LinkedIn post from the feed composer.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Create Poll",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def create_poll(
        question: str,
        options: list[str],
        ctx: Context | None = None,
        duration: str = "1 week",
        text: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create a LinkedIn poll post with 2-4 options."""

        async def _execute() -> dict[str, Any]:
            if len(options) < 2 or len(options) > 4:
                raise ValueError("Poll options must contain between 2 and 4 entries")

            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Opening poll composer"
                )

            await _open_composer(page)
            await click_element(page, SELECTORS["post_composer"]["poll_button"])

            if text:
                await type_text(page, SELECTORS["post_composer"]["text_editor"], text)

            await type_text(page, SELECTORS["post_composer"]["poll_question"], question)
            await type_text(
                page, SELECTORS["post_composer"]["poll_option_1"], options[0]
            )
            await type_text(
                page, SELECTORS["post_composer"]["poll_option_2"], options[1]
            )

            for idx, option in enumerate(options[2:], start=3):
                option_locator = page.locator(f"input[name='option{idx}']")
                if await option_locator.count() == 0:
                    add_option_button = page.get_by_role("button", name="Add option")
                    if await add_option_button.count() > 0:
                        await add_option_button.first.click()
                if await option_locator.count() > 0:
                    await option_locator.first.fill(option)

            duration_locator = await SELECTORS["post_composer"][
                "duration_dropdown"
            ].find(page)
            try:
                await duration_locator.select_option(label=duration)
            except Exception:
                # Duration labels vary by locale; fallback to leaving default.
                pass

            await click_element(page, SELECTORS["post_composer"]["submit"])
            await detect_rate_limit_post_action(page)
            await dismiss_modal(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Poll created"
                )

            return {
                "message": "Poll created successfully.",
                "resource_url": await _extract_recent_post_url(page),
            }

        return await run_write_tool(
            action="create_poll",
            params={
                "question": question,
                "options": options,
                "duration": duration,
                "text": text,
            },
            dry_run=dry_run,
            confirm=confirm,
            description="Create a poll post from the LinkedIn composer.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Delete Post",
            readOnlyHint=False,
            destructiveHint=True,
            openWorldHint=True,
        )
    )
    async def delete_post(
        post_url: str,
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete a LinkedIn post by URL."""

        async def _execute() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(progress=0, total=100, message="Loading post")

            await goto_and_check(page, post_url)
            await click_element(page, SELECTORS["post_actions"]["menu"])
            await click_and_confirm(
                page,
                SELECTORS["post_actions"]["delete"],
                SELECTORS["post_actions"]["confirm_delete"],
            )
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Post deleted"
                )

            return {"message": "Post deleted successfully.", "resource_url": post_url}

        return await run_write_tool(
            action="delete_post",
            params={"post_url": post_url},
            dry_run=dry_run,
            confirm=confirm,
            description=f"Delete LinkedIn post at {post_url}.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Repost",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def repost(
        post_url: str,
        ctx: Context | None = None,
        comment: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Repost an existing LinkedIn post with optional commentary."""

        async def _execute() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(progress=0, total=100, message="Loading post")

            await goto_and_check(page, post_url)
            await click_element(page, SELECTORS["post_actions"]["repost"])

            if comment:
                await click_element(
                    page, SELECTORS["post_actions"]["repost_with_thoughts"]
                )
                await wait_for_modal(page)
                await type_text(
                    page, SELECTORS["post_composer"]["text_editor"], comment
                )
                await click_element(page, SELECTORS["post_composer"]["submit"])
            else:
                await click_element(page, SELECTORS["post_actions"]["repost_now"])

            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Repost complete"
                )

            return {
                "message": "Repost submitted successfully.",
                "resource_url": post_url,
            }

        return await run_write_tool(
            action="repost",
            params={"post_url": post_url, "comment": comment},
            dry_run=dry_run,
            confirm=confirm,
            description=f"Repost LinkedIn post at {post_url}.",
            execute_fn=_execute,
        )
