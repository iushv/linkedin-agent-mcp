"""Messaging tools for conversation browsing, reading, and sending."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.interactions import click_element, type_text
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core.utils import detect_rate_limit_post_action
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools._common import (
    extract_thread_id_from_url,
    goto_and_check,
    normalize_profile_url,
    run_read_tool,
    run_write_tool,
)

logger = logging.getLogger(__name__)

_CONV_OPTIONS_RE = re.compile(
    r"Open the options list in your conversation with (.+)",
    re.IGNORECASE,
)


def _parse_conversation_item(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name = lines[0] if lines else ""
    preview = lines[1] if len(lines) > 1 else ""
    timestamp = lines[-1] if len(lines) > 2 else ""
    unread = any("unread" in line.lower() for line in lines)
    return {
        "name": name,
        "preview": preview,
        "timestamp": timestamp,
        "unread": unread,
    }


def _parse_message_item(text: str) -> dict[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    sender = lines[0] if lines else ""
    timestamp = lines[1] if len(lines) > 1 else ""
    message = (
        "\n".join(lines[2:])
        if len(lines) > 2
        else (lines[1] if len(lines) == 2 else "")
    )
    return {
        "sender": sender,
        "text": message,
        "timestamp": timestamp,
    }


def register_messaging_tools(mcp: FastMCP) -> None:
    """Register messaging tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Conversations",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_conversations(
        limit: int = 10, ctx: Context | None = None
    ) -> dict[str, Any]:
        """Get recent LinkedIn messaging conversations."""

        async def _fetch() -> dict[str, Any]:
            safe_limit = max(1, min(limit, 50))
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading messaging inbox"
                )

            await goto_and_check(page, "https://www.linkedin.com/messaging/")

            # Wait for conversation list to load
            try:
                await page.wait_for_selector(
                    "[aria-label*='Conversation' i], [role='list']",
                    timeout=8000,
                )
            except Exception:
                logger.debug("Messaging: no conversation list selector found")

            # 2025+ DOM: conversations in list[aria-label="Conversation List"]
            rows = None
            for conv_sel in (
                "list:has-text('Conversation List') > listitem",
                "[aria-label*='Conversation' i] > [role='listitem']",
                "[aria-label*='Conversation' i] > li",
            ):
                try:
                    loc = page.locator(conv_sel)
                    if await loc.count() > 0:
                        rows = loc
                        break
                except Exception:
                    continue

            # Legacy fallback
            if rows is None:
                try:
                    rows = await SELECTORS["messaging"]["conversation_items"].resolve(
                        page
                    )
                except Exception:
                    logger.debug("Legacy messaging selectors also failed")
                    rows = page.get_by_role("listitem").filter(
                        has=page.locator(
                            "button[aria-label*='options list in your conversation' i]"
                        )
                    )

            conversations: list[dict[str, Any]] = []
            total_rows = await rows.count()

            for idx in range(total_rows):
                if len(conversations) >= safe_limit:
                    break
                row = rows.nth(idx)

                try:
                    text = await row.inner_text(timeout=2000)
                except Exception:
                    continue

                if not text or not text.strip():
                    continue

                parsed = _parse_conversation_item(text)

                # Extract participant names from options button aria-label
                try:
                    opts_btn = row.locator(
                        "button[aria-label*='options list in your conversation' i]"
                    ).first
                    if await opts_btn.count() > 0:
                        opts_label = await opts_btn.get_attribute(
                            "aria-label", timeout=300
                        )
                        if opts_label:
                            m = _CONV_OPTIONS_RE.search(opts_label)
                            if m:
                                parsed["name"] = m.group(1).strip()
                except Exception:
                    pass

                # Try to find thread link
                thread_id_val = None
                href = None
                link = row.locator("a[href*='/messaging/thread/']").first
                try:
                    if await link.count() > 0:
                        href = await link.get_attribute("href")
                        thread_id_val = (
                            extract_thread_id_from_url(href or "") if href else None
                        )
                        if href and href.startswith("/"):
                            href = f"https://www.linkedin.com{href}"
                except Exception:
                    pass

                conversations.append(
                    {
                        **parsed,
                        "profile_url": href,
                        "thread_id": thread_id_val,
                    }
                )

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Conversations loaded"
                )

            return {"conversations": conversations}

        return await run_read_tool("get_conversations", _fetch)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Read Conversation",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def read_conversation(
        thread_id: str | None = None,
        profile_url: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read messages from a conversation by thread id or profile URL."""

        async def _fetch() -> dict[str, Any]:
            if not thread_id and not profile_url:
                raise ValueError("Either thread_id or profile_url must be provided")

            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Opening conversation"
                )

            resolved_thread_id = thread_id
            if thread_id:
                await goto_and_check(
                    page, f"https://www.linkedin.com/messaging/thread/{thread_id}/"
                )
            else:
                profile = normalize_profile_url(profile_url or "")
                await goto_and_check(page, profile)
                await click_element(page, SELECTORS["common"]["message_button"])
                await goto_and_check(page, "https://www.linkedin.com/messaging/")
                resolved_thread_id = extract_thread_id_from_url(page.url)

            # Wait for thread messages to load
            await asyncio.sleep(2)

            # 2025+ DOM: thread messages are listitem elements in a list
            # within the conversation detail pane
            items = None
            for msg_sel in (
                # Thread message list items (contain "{Name} sent the following")
                "[role='listitem']:has(a[href*='/in/'])",
            ):
                try:
                    loc = page.locator(msg_sel)
                    if await loc.count() > 0:
                        items = loc
                        break
                except Exception:
                    continue

            # Legacy fallback
            if items is None:
                try:
                    items = await SELECTORS["messaging"]["thread_messages"].resolve(
                        page
                    )
                except Exception:
                    # Broadest fallback: all listitem in the message area
                    items = page.get_by_role("listitem")

            messages: list[dict[str, str]] = []

            for idx in range(await items.count()):
                item = items.nth(idx)
                try:
                    text = await item.inner_text(timeout=2000)
                except Exception:
                    continue
                if text and text.strip():
                    messages.append(_parse_message_item(text))

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Conversation loaded"
                )

            return {
                "thread_id": resolved_thread_id or extract_thread_id_from_url(page.url),
                "messages": messages,
            }

        return await run_read_tool("read_conversation", _fetch)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Send Message",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def send_message(
        profile_url: str,
        text: str,
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Send a message to a LinkedIn profile."""

        async def _execute() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page
            normalized_url = normalize_profile_url(profile_url)

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Opening profile"
                )

            await goto_and_check(page, normalized_url)
            await click_element(page, SELECTORS["common"]["message_button"])
            await type_text(page, SELECTORS["messaging"]["message_input"], text)
            await click_element(page, SELECTORS["messaging"]["send_button"])
            await detect_rate_limit_post_action(page)

            thread_url = page.url if "/messaging/thread/" in page.url else None

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Message sent"
                )

            return {
                "message": "Message sent successfully.",
                "resource_url": thread_url,
            }

        return await run_write_tool(
            action="send_message",
            params={"profile_url": profile_url, "text": text},
            dry_run=dry_run,
            confirm=confirm,
            description=f"Send a LinkedIn message to {profile_url}.",
            execute_fn=_execute,
        )
