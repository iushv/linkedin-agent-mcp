"""Connection management and network interaction tools."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from linkedin_mcp_server.core.exceptions import ElementNotFoundError
from linkedin_mcp_server.core.interactions import click_element, type_text
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core.utils import (
    detect_rate_limit_post_action,
    handle_modal_close,
)
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools._common import (
    goto_and_check,
    normalize_profile_url,
    parse_count,
    run_read_tool,
    run_write_tool,
)

logger = logging.getLogger(__name__)


async def _click_with_overlay_protection(
    page: Any,
    locator: Any,
    *,
    timeout_ms: int = 5000,
    failure_message: str = "Click intercepted by overlay; JS fallback also failed.",
) -> None:
    """Click a Playwright locator with a JS-evaluate fallback if the click is intercepted.

    Bounds the failure path at ~timeout_ms instead of Playwright's 30s default and raises
    a structured ElementNotFoundError(context={"overlay_suspected": True}) on total failure.
    """
    try:
        await locator.click(timeout=timeout_ms)
        return
    except (PlaywrightTimeoutError, TimeoutError) as exc:
        element_handle_fn = getattr(locator, "element_handle", None)
        if element_handle_fn is None:
            raise ElementNotFoundError(
                failure_message,
                context={"overlay_suspected": True},
            ) from exc
        try:
            handle = await element_handle_fn()
        except Exception as inner:
            raise ElementNotFoundError(
                failure_message,
                context={"overlay_suspected": True},
            ) from inner
        if handle is None:
            raise ElementNotFoundError(
                failure_message,
                context={"overlay_suspected": True},
            ) from exc
        try:
            await page.evaluate("(el) => el.click()", handle)
        except Exception as inner:
            raise ElementNotFoundError(
                failure_message,
                context={"overlay_suspected": True},
            ) from inner


def _extract_mutual_connections(text: str) -> int | None:
    match = re.search(r"([\d,.kKmM]+)\s+mutual", text, re.IGNORECASE)
    if not match:
        return None
    return parse_count(match.group(1))


def _extract_name_headline(text: str) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name = lines[0] if lines else ""
    headline = lines[1] if len(lines) > 1 else ""
    return name, headline


async def _find_invite_button(row: Any, action: str) -> Any | None:
    """Resolve invitation action buttons across LinkedIn DOM variants."""
    if action == "accept":
        candidates = [
            lambda: row.get_by_role("button", name=re.compile(r"^Accept$", re.I)),
            lambda: row.locator("button[aria-label*='Accept' i]"),
            lambda: row.locator("button:has-text('Accept')"),
            lambda: row.locator("button[data-control-name*='accept' i]"),
        ]
    else:
        candidates = [
            lambda: row.get_by_role(
                "button", name=re.compile(r"^(Ignore|Decline)$", re.I)
            ),
            lambda: row.locator(
                "button[aria-label*='Ignore' i], button[aria-label*='Decline' i]"
            ),
            lambda: row.locator(
                "button:has-text('Ignore'), button:has-text('Decline')"
            ),
            lambda: row.locator(
                "button[data-control-name*='ignore' i], "
                "button[data-control-name*='decline' i]"
            ),
        ]

    for factory in candidates:
        locator = factory()
        try:
            if await locator.count() > 0:
                return locator.first
        except Exception:
            continue

    return None


def register_network_tools(mcp: FastMCP) -> None:
    """Register network tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Send Connection Request",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def send_connection_request(
        profile_url: str,
        ctx: Context | None = None,
        note: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Send a LinkedIn connection request to a profile."""

        async def _execute() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page
            normalized_url = normalize_profile_url(profile_url)

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Opening profile"
                )

            await goto_and_check(page, normalized_url)
            await handle_modal_close(page)

            connect_button = page.get_by_role("button", name="Connect")
            if await connect_button.count() > 0:
                await _click_with_overlay_protection(
                    page,
                    connect_button.first,
                    failure_message=(
                        "Connect button click intercepted by overlay; JS fallback also failed."
                    ),
                )
            else:
                try:
                    more_locator = await SELECTORS["network"]["more_actions"].find(
                        page
                    )
                except Exception as exc:
                    raise ElementNotFoundError(
                        "Could not locate a clickable More actions control for the connection request.",
                        context={"overlay_suspected": True},
                    ) from exc
                await _click_with_overlay_protection(
                    page,
                    more_locator,
                    failure_message=(
                        "More actions click intercepted by overlay; JS fallback also failed."
                    ),
                )
                menu_connect = page.get_by_role("menuitem", name="Connect")
                if await menu_connect.count() == 0:
                    menu_connect = page.get_by_text("Connect")
                if await menu_connect.count() == 0:
                    raise ElementNotFoundError(
                        "Could not locate Connect action after opening the More actions menu.",
                        context={"overlay_suspected": True},
                    )
                await _click_with_overlay_protection(
                    page,
                    menu_connect.first,
                    failure_message=(
                        "Connect menu item click intercepted by overlay; JS fallback also failed."
                    ),
                )

            if note:
                await click_element(page, SELECTORS["network"]["add_note"])
                await type_text(page, SELECTORS["network"]["note_input"], note)

            await click_element(page, SELECTORS["network"]["send_invite"])
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100,
                    total=100,
                    message="Connection request sent",
                )

            return {
                "message": "Connection request sent successfully.",
                "resource_url": normalized_url,
            }

        return await run_write_tool(
            action="send_connection_request",
            params={"profile_url": profile_url, "note": note},
            dry_run=dry_run,
            confirm=confirm,
            description=f"Send a LinkedIn connection request to {profile_url}.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Pending Invitations",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_pending_invitations(
        limit: int = 20, ctx: Context | None = None
    ) -> dict[str, Any]:
        """List pending incoming LinkedIn invitations."""

        async def _fetch() -> dict[str, Any]:
            safe_limit = max(1, min(limit, 100))
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading invitation manager"
                )

            await goto_and_check(
                page, "https://www.linkedin.com/mynetwork/invitation-manager/"
            )

            invitations: list[dict[str, Any]] = []

            # Detect empty state — LinkedIn shows this when there are no invitations
            empty_markers = page.locator(
                "text='No pending invitations', "
                "text='No new invitations', "
                "h2:has-text('No pending invitations')"
            )
            try:
                if await empty_markers.count() > 0:
                    logger.debug("Invitation manager shows empty state")
                    return {"invitations": []}
            except Exception:
                pass

            # Use invitation-specific selectors before falling back to generic listitem.
            # The chain's Role("listitem") is too broad — filter to rows that contain
            # Accept/Ignore buttons, which only appear on invitation cards.
            rows = None
            for sel in (
                "li.invitation-card",
                "li.mn-invitation-manager__invitation-card",
                "[data-view-name='invitation-card']",
            ):
                loc = page.locator(sel)
                try:
                    if await loc.count() > 0:
                        rows = loc
                        break
                except Exception:
                    continue

            if rows is None:
                # Fallback: listitems that contain an Accept or Ignore button
                rows = page.get_by_role("listitem").filter(
                    has=page.get_by_role(
                        "button", name=re.compile(r"Accept|Ignore", re.IGNORECASE)
                    )
                )
                try:
                    if await rows.count() == 0:
                        logger.debug("No invitation rows found; returning empty list")
                        return {"invitations": []}
                except Exception:
                    return {"invitations": []}

            total_rows = min(await rows.count(), safe_limit)

            for idx in range(total_rows):
                row = rows.nth(idx)
                try:
                    anchor = row.locator('a[href*="/in/"]').first
                    if await anchor.count() == 0:
                        continue

                    text = await row.inner_text(timeout=2000)
                    name, headline = _extract_name_headline(text)
                    href = await anchor.get_attribute("href")
                    if href and href.startswith("/"):
                        href = f"https://www.linkedin.com{href}"

                    invitations.append(
                        {
                            "name": name,
                            "profile_url": href,
                            "headline": headline,
                            "mutual_connections": _extract_mutual_connections(text),
                            "invitation_index": idx,
                        }
                    )
                except Exception:
                    continue
                if len(invitations) >= safe_limit:
                    break

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Invitations loaded"
                )

            return {"invitations": invitations}

        return await run_read_tool("get_pending_invitations", _fetch)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Respond To Invitation",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def respond_to_invitation(
        profile_url: str,
        action: str,
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Accept or decline a pending invitation for a profile URL."""

        async def _execute() -> dict[str, Any]:
            normalized_url = normalize_profile_url(profile_url)
            normalized_action = action.strip().lower()
            if normalized_action not in {"accept", "decline"}:
                raise ValueError("action must be either 'accept' or 'decline'")

            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading invitation manager"
                )

            await goto_and_check(
                page, "https://www.linkedin.com/mynetwork/invitation-manager/"
            )

            rows = await SELECTORS["network"]["invitation_rows"].resolve(page)
            target_row = None

            for idx in range(await rows.count()):
                row = rows.nth(idx)
                anchor = row.locator('a[href*="/in/"]').first
                href = (
                    await anchor.get_attribute("href")
                    if await anchor.count() > 0
                    else None
                )
                if href and href.startswith("/"):
                    href = f"https://www.linkedin.com{href}"
                if href and normalize_profile_url(href) == normalized_url:
                    target_row = row
                    break

            if target_row is None:
                raise ValueError("Invitation not found for the provided profile URL")

            button = await _find_invite_button(target_row, normalized_action)
            if button is None:
                raise ElementNotFoundError(
                    f"Could not locate {normalized_action} button for invitation. "
                    "LinkedIn may have changed the DOM - fall back to Chrome MCP."
                )

            await button.click()
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Invitation updated"
                )

            return {
                "message": f"Invitation {normalized_action}ed successfully.",
                "resource_url": normalized_url,
            }

        return await run_write_tool(
            action="respond_to_invitation",
            params={"profile_url": profile_url, "action": action},
            dry_run=dry_run,
            confirm=confirm,
            description=f"{action.title()} invitation from {profile_url}.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Follow Person",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def follow_person(
        profile_url: str,
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Follow a LinkedIn person profile."""

        async def _execute() -> dict[str, Any]:
            normalized_url = normalize_profile_url(profile_url)
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Opening profile"
                )

            await goto_and_check(page, normalized_url)

            follow_button = page.get_by_role("button", name="Follow")
            if await follow_button.count() > 0:
                await follow_button.first.click()
            else:
                await click_element(page, SELECTORS["network"]["more_actions"])
                menu_follow = page.get_by_role("menuitem", name="Follow")
                if await menu_follow.count() == 0:
                    menu_follow = page.get_by_text("Follow")
                await menu_follow.first.click()

            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Profile followed"
                )

            return {
                "message": "Followed profile successfully.",
                "resource_url": normalized_url,
            }

        return await run_write_tool(
            action="follow_person",
            params={"profile_url": profile_url},
            dry_run=dry_run,
            confirm=confirm,
            description=f"Follow LinkedIn profile {profile_url}.",
            execute_fn=_execute,
        )
