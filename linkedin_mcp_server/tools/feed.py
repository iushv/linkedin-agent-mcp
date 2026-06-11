"""Feed browsing and analytics tools."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.pagination import build_paginated_response, decode_cursor
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core import handle_modal_close
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools.extraction.engagement_rows import (
    _extract_comment_row,
    _extract_post_author_profile_url,
    _extract_reactor_row,
    _find_visible_css_locator,
    _load_comment_rows,
    _load_reaction_modal,
)
from linkedin_mcp_server.tools.extraction.feed_cards import (
    _extract_activity_posts_from_dom,
    _extract_author_from_card,
    _resolve_post_cards,
)
from linkedin_mcp_server.tools.extraction.post_identity import (
    _extract_activity_urn,
    _extract_post_identifier,
    _invalidate_post_identifier_script_cache,
    _is_engagement_actionable_identifier,
    _log_post_identifier_miss,
)
from linkedin_mcp_server.tools.extraction.profile_analytics import (
    _discover_profile_analytics_links,
    _extract_profile_analytics_from_dom,
    _extract_profile_analytics_from_text,
    _merge_profile_analytics,
    _profile_analytics_complete,
)
from linkedin_mcp_server.tools.extraction.activity_text import (
    _classify_card,
    _extract_post_from_text,
    _is_actionable_card_type,
    _parse_posts_from_activity_text,
    _post_identity,
)
from linkedin_mcp_server.scraping.extractor import LinkedInExtractor
from linkedin_mcp_server.tools._common import (
    FEED_NAVIGATION_TIMEOUT_MS,
    effective_navigation_timeout_ms,
    ensure_page_healthy,
    goto_and_check,
    normalize_post_reference,
    run_read_tool,
)

logger = logging.getLogger(__name__)
_BROWSE_FEED_INTERNAL_BUDGET_SECONDS = 60.0
_BROWSE_FEED_STAGNANT_SCROLLS = 5
_BROWSE_FEED_SCROLL_DELAY_SECONDS = 1.2
_BROWSE_FEED_PATIENT_SCROLL_DELAY_SECONDS = 2.0


def _feed_navigation_timeout_ms() -> int:
    """Use a longer timeout for feed pages, which are among the slowest to hydrate."""
    return effective_navigation_timeout_ms(FEED_NAVIGATION_TIMEOUT_MS)


def register_feed_tools(mcp: FastMCP) -> None:
    """Register feed browsing and analytics tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Browse Feed",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def browse_feed(
        count: int = 10, ctx: Context | None = None
    ) -> dict[str, Any]:
        """Browse LinkedIn feed and return structured post summaries."""

        async def _fetch() -> dict[str, Any]:
            safe_count = max(1, min(count, 50))
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(progress=0, total=100, message="Loading feed")

            await goto_and_check(
                page,
                "https://www.linkedin.com/feed/",
                timeout_ms=_feed_navigation_timeout_ms(),
            )
            await ensure_page_healthy(page)

            # Wait for SPA to render, dismiss any modal or consent overlay
            try:
                await page.wait_for_selector("main", timeout=8000)
            except Exception:
                logger.debug("No <main> on feed page; proceeding anyway")
            await handle_modal_close(page)
            # Dismiss cookie/GDPR consent banners that block feed rendering
            for consent_sel in (
                "button[action-type='ACCEPT']",
                "button[data-tracking-control-name='cookie-policy-banner-accept']",
                "button:has-text('Accept cookies')",
                "button:has-text('Accept all')",
                "button:has-text('Accept & continue')",
            ):
                try:
                    btn = page.locator(consent_sel).first
                    if await btn.count() > 0:
                        await btn.click(timeout=2000)
                        logger.debug("Dismissed consent banner: %s", consent_sel)
                        await asyncio.sleep(0.8)
                        break
                except Exception:
                    pass

            # Wait for actual feed posts to hydrate (not just <main>).
            # Use a single combined selector — sequential 12s waits blew the 60s budget.
            try:
                await page.wait_for_selector(
                    "article, "
                    "div.feed-shared-update-v2, "
                    "div.occludable-update, "
                    "[data-view-name='feed-full-update']",
                    timeout=8000,
                )
            except Exception:
                logger.warning("Feed posts did not hydrate within 8s")

            posts: list[dict[str, Any]] = []
            strategy_counts: dict[str, int] = {}
            seen_fingerprints: set[str] = set()
            stagnant_scrolls = 0
            last_total = 0
            processed_idx = 0
            scroll_iterations = 0
            stopped_reason = "empty_page"
            patient_scroll_floor = 0

            _feed_deadline = monotonic() + _BROWSE_FEED_INTERNAL_BUDGET_SECONDS
            while len(posts) < safe_count:
                if monotonic() >= _feed_deadline:
                    stopped_reason = "timeout"
                    break

                cards = await _resolve_post_cards(page)
                if cards is None:
                    stopped_reason = "empty_page"
                    break
                total_cards = await cards.count()

                for idx in range(processed_idx, total_cards):
                    if len(posts) >= safe_count:
                        break
                    card = cards.nth(idx)
                    try:
                        text = await card.inner_text(timeout=2500)
                    except Exception:
                        continue

                    if not text or not text.strip():
                        continue

                    # Skip non-post cards (ads, suggestions, chrome)
                    stripped = text.strip()
                    if len(stripped) < 50:
                        continue

                    # Dedup by text fingerprint (first 200 chars)
                    fingerprint = stripped[:200]
                    if fingerprint in seen_fingerprints:
                        continue
                    seen_fingerprints.add(fingerprint)

                    # Extract author from DOM (not text) and post URL
                    post = _extract_post_from_text(text)
                    card_type = _classify_card(stripped)
                    dom_author = await _extract_author_from_card(card)
                    if dom_author:
                        post["author"] = dom_author
                    identifier = await _extract_post_identifier(card)
                    strategy_name = identifier.get("strategy")
                    if not strategy_name:
                        strategy_name = (
                            "resolved_unspecified"
                            if identifier.get("url") is not None
                            or identifier.get("post_urn") is not None
                            else "missing"
                        )
                    strategy_counts[str(strategy_name)] = (
                        strategy_counts.get(str(strategy_name), 0) + 1
                    )
                    if strategy_name == "missing":
                        await _log_post_identifier_miss(
                            card,
                            card_type=card_type,
                            stripped_text=stripped,
                        )
                    post["url"] = identifier["url"]
                    post["post_urn"] = identifier["post_urn"]
                    post["card_type"] = card_type
                    posts.append(post)

                processed_idx = total_cards

                if len(posts) >= safe_count:
                    stopped_reason = "limit_reached"
                    break

                await page.evaluate(
                    "window.scrollBy(0, Math.floor(window.innerHeight * 0.9))"
                )
                scroll_iterations += 1
                low_card_threshold = max(1, safe_count // 2)
                needs_patient_scrolls = (
                    scroll_iterations >= 3 and len(posts) < low_card_threshold
                )
                if needs_patient_scrolls:
                    patient_scroll_floor = max(patient_scroll_floor, 5)
                await asyncio.sleep(
                    _BROWSE_FEED_PATIENT_SCROLL_DELAY_SECONDS
                    if needs_patient_scrolls
                    else _BROWSE_FEED_SCROLL_DELAY_SECONDS
                )
                await _invalidate_post_identifier_script_cache(page)

                if total_cards <= last_total:
                    stagnant_scrolls += 1
                else:
                    stagnant_scrolls = 0
                last_total = total_cards

                if stagnant_scrolls >= _BROWSE_FEED_STAGNANT_SCROLLS:
                    if scroll_iterations < patient_scroll_floor:
                        continue
                    stopped_reason = (
                        "empty_page" if total_cards == 0 and not posts else "stagnant"
                    )
                    break

            if ctx:
                await ctx.report_progress(
                    progress=100,
                    total=100,
                    message=f"Extracted {len(posts)} posts",
                )

            final_posts = posts[:safe_count]
            all_resolved = sum(
                count for name, count in strategy_counts.items() if name != "missing"
            )
            all_total = len(final_posts)
            actionable_total = sum(
                1
                for post in final_posts
                if _is_actionable_card_type(str(post.get("card_type", "regular")))
            )
            actionable_resolved = sum(
                1
                for post in final_posts
                if _is_actionable_card_type(str(post.get("card_type", "regular")))
                and (post.get("url") is not None or post.get("post_urn") is not None)
            )
            logger.info(
                "browse_feed URL extraction: actionable=%d/%d all=%d/%d strategies=%s",
                actionable_resolved,
                actionable_total,
                all_resolved,
                all_total,
                strategy_counts,
            )
            with_url = sum(1 for post in final_posts if post.get("url"))
            with_post_urn = sum(1 for post in final_posts if post.get("post_urn"))
            actionable = sum(
                1
                for post in final_posts
                if post.get("url") is not None or post.get("post_urn") is not None
            )
            engagement_actionable = sum(
                1
                for post in final_posts
                if _is_engagement_actionable_identifier(
                    url=post.get("url"),
                    post_urn=post.get("post_urn"),
                )
            )
            card_type_counts = {
                "regular": 0,
                "promoted": 0,
                "sponsored": 0,
                "suggested": 0,
            }
            for post in final_posts:
                card_type = str(post.get("card_type", "regular"))
                card_type_counts[card_type] = card_type_counts.get(card_type, 0) + 1
            regular_total = card_type_counts.get("regular", 0)
            regular_actionable = sum(
                1
                for post in final_posts
                if post.get("card_type") == "regular"
                and (post.get("url") is not None or post.get("post_urn") is not None)
            )
            regular_engagement_actionable = sum(
                1
                for post in final_posts
                if post.get("card_type") == "regular"
                and _is_engagement_actionable_identifier(
                    url=post.get("url"),
                    post_urn=post.get("post_urn"),
                )
            )

            return {
                "posts": final_posts,
                "extraction_stats": {
                    "total": len(final_posts),
                    "with_url": with_url,
                    "with_post_urn": with_post_urn,
                    "actionable": actionable,
                    "engagement_actionable": engagement_actionable,
                    "regular_total": regular_total,
                    "regular_actionable": regular_actionable,
                    "regular_engagement_actionable": regular_engagement_actionable,
                    "card_types": card_type_counts,
                },
                "scroll_iterations": scroll_iterations,
                "stopped_reason": stopped_reason,
            }

        return await run_read_tool("browse_feed", _fetch)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Post Reactions",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_post_reactions(
        post_url: str,
        limit: int = 50,
        next_cursor: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List people who reacted to a LinkedIn post."""

        async def _fetch() -> dict[str, Any]:
            safe_limit = max(1, min(limit, 100))
            current_page = decode_cursor(next_cursor)
            start_idx = (current_page - 1) * safe_limit
            end_idx = start_idx + safe_limit
            normalized_post_url = normalize_post_reference(post_url)
            post_urn = _extract_activity_urn(normalized_post_url)

            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading post reactions"
                )

            await goto_and_check(page, normalized_post_url)
            await ensure_page_healthy(page)

            count_button = await _find_visible_css_locator(
                page,
                (
                    "button.social-details-social-counts__count-value-hover",
                    "button[aria-label*='reaction' i]",
                    ".social-details-social-counts__reactions-count button",
                    "button:has-text('reactions')",
                ),
            )
            if count_button is None:
                count_button = await SELECTORS["post_reactions"]["count_button"].find(
                    page
                )
            await count_button.click()
            modal = await SELECTORS["post_reactions"]["modal"].find(page)
            modal_data = await _load_reaction_modal(page, modal, target_count=end_idx)
            row_locator = modal_data["rows"]

            results: list[dict[str, Any]] = []
            if row_locator is not None:
                loaded_count = int(modal_data["loaded_count"])
                for idx in range(start_idx, min(loaded_count, end_idx)):
                    item = await _extract_reactor_row(row_locator.nth(idx))
                    if item is not None:
                        results.append(item)

            payload = build_paginated_response(
                results,
                page=current_page,
                limit=safe_limit,
                total=modal_data["total"],
                partial=bool(modal_data["partial"]),
                warnings=modal_data["warnings"] or None,
            ).to_dict()
            payload["post_url"] = normalized_post_url
            payload["post_urn"] = post_urn

            if ctx:
                await ctx.report_progress(
                    progress=100,
                    total=100,
                    message=f"Loaded {len(results)} reactions",
                )

            return payload

        return await run_read_tool("get_post_reactions", _fetch)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Post Commenters",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_post_commenters(
        post_url: str,
        limit: int = 50,
        next_cursor: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List top-level commenters on a LinkedIn post."""

        async def _fetch() -> dict[str, Any]:
            safe_limit = max(1, min(limit, 100))
            current_page = decode_cursor(next_cursor)
            start_idx = (current_page - 1) * safe_limit
            end_idx = start_idx + safe_limit
            normalized_post_url = normalize_post_reference(post_url)
            post_urn = _extract_activity_urn(normalized_post_url)

            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading post comments"
                )

            await goto_and_check(page, normalized_post_url)
            await ensure_page_healthy(page)

            post_author_profile_url = await _extract_post_author_profile_url(page)
            comment_data = await _load_comment_rows(page, target_count=end_idx)
            row_locator = comment_data["rows"]

            extracted_rows: list[dict[str, Any]] = []
            if row_locator is not None:
                loaded_count = int(comment_data["loaded_count"])
                for idx in range(loaded_count):
                    item = await _extract_comment_row(
                        row_locator.nth(idx),
                        post_author_profile_url=post_author_profile_url,
                    )
                    if item is not None:
                        extracted_rows.append(item)

            results = extracted_rows[start_idx:end_idx]
            payload = build_paginated_response(
                results,
                page=current_page,
                limit=safe_limit,
                total=comment_data["total"],
                partial=bool(comment_data["partial"]),
                warnings=comment_data["warnings"] or None,
            ).to_dict()
            payload["post_url"] = normalized_post_url
            payload["post_urn"] = post_urn

            if ctx:
                await ctx.report_progress(
                    progress=100,
                    total=100,
                    message=f"Loaded {len(results)} commenters",
                )

            return payload

        return await run_read_tool("get_post_commenters", _fetch)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get My Post Analytics",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_my_post_analytics(
        limit: int = 5, ctx: Context | None = None
    ) -> dict[str, Any]:
        """Extract analytics-style engagement metrics from recent activity posts."""

        async def _fetch() -> dict[str, Any]:
            safe_limit = max(1, min(limit, 20))
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0,
                    total=100,
                    message="Loading recent activity",
                )

            posts: list[dict[str, Any]] = []
            diagnostics: dict[str, Any] = {
                "posts_requested": safe_limit,
                "posts_returned": 0,
                "scroll_iterations": 0,
                "stopped_reason": "empty_page",
                "activity_url_tried": [],
                "activity_url_selected": None,
                "activity_any_nav_succeeded": False,
                "activity_page_html_lengths": [],
                "activity_last_error": None,
            }
            warnings: list[str] = []
            fallback_invoked = False
            try:
                dom_result = await _extract_activity_posts_from_dom(
                    page, limit=safe_limit
                )
                if isinstance(dom_result, list):
                    posts = dom_result
                else:
                    posts = list(dom_result.get("posts", []))
                    diagnostics.update(
                        {
                            "posts_requested": int(
                                dom_result.get("posts_requested", safe_limit)
                            ),
                            "posts_returned": int(
                                dom_result.get("posts_returned", len(posts))
                            ),
                            "scroll_iterations": int(
                                dom_result.get("scroll_iterations", 0)
                            ),
                            "stopped_reason": str(
                                dom_result.get("stopped_reason", "empty_page")
                            ),
                            "activity_url_tried": list(
                                dom_result.get("activity_url_tried", [])
                            ),
                            "activity_url_selected": dom_result.get(
                                "activity_url_selected"
                            ),
                            "activity_any_nav_succeeded": bool(
                                dom_result.get("activity_any_nav_succeeded", False)
                            ),
                            "activity_page_html_lengths": list(
                                dom_result.get("activity_page_html_lengths", [])
                            ),
                            "activity_last_error": dom_result.get(
                                "activity_last_error"
                            ),
                        }
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    "Recent activity DOM extraction failed; falling back to profile text",
                    exc_info=True,
                )
                diagnostics["stopped_reason"] = "timeout"
            except Exception as exc:
                logger.warning(
                    "Recent activity DOM extraction failed; falling back to profile text",
                    exc_info=True,
                )
                diagnostics["activity_last_error"] = str(exc)

            if ctx:
                await ctx.report_progress(
                    progress=80, total=100, message="Parsing posts"
                )

            if not posts:
                fallback_invoked = True
                extractor = LinkedInExtractor(page)
                text = await extractor.extract_page("https://www.linkedin.com/in/me/")
                fallback_posts = _parse_posts_from_activity_text(text, safe_limit)
                logger.debug(
                    "Recent activity text fallback invoked count=%d",
                    len(fallback_posts),
                )

                seen = {_post_identity(post) for post in posts}
                for post in fallback_posts:
                    identity = _post_identity(post)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    posts.append(post)
                    if len(posts) >= safe_limit:
                        break

            # Distinguish "DOM nav failed entirely and we ran the text fallback"
            # from "navigations succeeded but DOM yielded zero cards" (zero-posts
            # account or DOM variant — keep "empty_page"). Only flip to the new
            # stopped_reason when no URL completed goto_and_check successfully.
            if fallback_invoked and not diagnostics["activity_any_nav_succeeded"]:
                diagnostics["stopped_reason"] = (
                    "activity_navigation_failed_fallback_used"
                )

            diagnostics["posts_returned"] = len(posts[:safe_limit])
            if diagnostics["stopped_reason"] in {"stagnant", "timeout"} and diagnostics[
                "posts_returned"
            ] < max(diagnostics["posts_requested"] // 2, 3):
                warnings.append(
                    "Recent activity returned fewer posts than requested; retry if you need a fuller history."
                )

            if ctx:
                await ctx.report_progress(progress=100, total=100, message="Complete")

            return {
                "posts": posts[:safe_limit],
                "posts_requested": diagnostics["posts_requested"],
                "posts_returned": diagnostics["posts_returned"],
                "scroll_iterations": diagnostics["scroll_iterations"],
                "stopped_reason": diagnostics["stopped_reason"],
                "activity_url_tried": diagnostics["activity_url_tried"],
                "activity_url_selected": diagnostics["activity_url_selected"],
                "activity_any_nav_succeeded": diagnostics["activity_any_nav_succeeded"],
                "activity_page_html_lengths": diagnostics["activity_page_html_lengths"],
                "activity_last_error": diagnostics["activity_last_error"],
                "_warnings": warnings,
            }

        return await run_read_tool("get_my_post_analytics", _fetch)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Profile Analytics",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_profile_analytics(ctx: Context | None = None) -> dict[str, Any]:
        """Get profile-level LinkedIn analytics summary values."""

        async def _fetch() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page
            page_budget = 3

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading dashboard"
                )

            async def _try_page_analytics(
                url: str,
            ) -> tuple[dict[str, int | None], dict[str, str]]:
                """Extract analytics and metric links from a single page."""
                await goto_and_check(page, url)

                try:
                    await page.wait_for_selector("main", timeout=5000)
                except Exception:
                    logger.debug("Profile analytics: no <main> on %s", url)

                # DOM extraction: aria-labels / link text in dashboard widget
                result = await _extract_profile_analytics_from_dom(page)

                # Text extraction from dashboard widget selectors
                _DASHBOARD_SELECTORS = (
                    "section:has(a[href*='profile-views'])",
                    "section:has(a[href*='search-appearances'])",
                    "div[data-view-name*='dashboard']",
                    ".pv-dashboard-section",
                    ".pv-deferred-area",
                    "section.artdeco-card",
                    "section, div[data-view-name*='dashboard'], main",
                    "main",
                )
                for sel in _DASHBOARD_SELECTORS:
                    try:
                        widget = page.locator(sel).first
                        if await widget.count() > 0:
                            dashboard_text = await widget.inner_text(timeout=3000)
                            parsed = _extract_profile_analytics_from_text(
                                dashboard_text
                            )
                            result = _merge_profile_analytics(result, parsed)
                    except Exception:
                        continue

                # Scroll and read full body
                try:
                    await page.evaluate(
                        "window.scrollBy(0, document.body.scrollHeight * 0.5)"
                    )
                except Exception:
                    pass
                try:
                    await page.wait_for_selector("body", timeout=5000)
                except Exception:
                    pass
                try:
                    body_text = await page.locator("body").inner_text(timeout=8000)
                    parsed = _extract_profile_analytics_from_text(body_text)
                    result = _merge_profile_analytics(result, parsed)
                except Exception:
                    pass

                return result, await _discover_profile_analytics_links(page)

            async def _read_dashboard() -> dict[str, int | None]:
                result: dict[str, int | None] = {
                    "profile_views": None,
                    "search_appearances": None,
                    "post_impressions": None,
                }
                visited: set[str] = set()
                queue: list[str] = [
                    "https://www.linkedin.com/feed/",
                    "https://www.linkedin.com/dashboard/",
                ]
                canonical_metric_urls = {
                    "profile_views": "https://www.linkedin.com/me/profile-views/",
                    "search_appearances": "https://www.linkedin.com/me/search-appearances/",
                    "post_impressions": "https://www.linkedin.com/analytics/creator/content/",
                }

                while queue and len(visited) < page_budget:
                    url = queue.pop(0)
                    if url in visited:
                        continue
                    visited.add(url)

                    page_result, discovered_links = await _try_page_analytics(url)
                    result = _merge_profile_analytics(result, page_result)
                    if _profile_analytics_complete(result):
                        break

                    missing_keys = [
                        key for key, value in result.items() if value is None
                    ]
                    for key in missing_keys:
                        discovered_url = discovered_links.get(key)
                        if discovered_url and discovered_url not in visited:
                            queue.append(discovered_url)
                    for key in missing_keys:
                        canonical_url = canonical_metric_urls[key]
                        if canonical_url not in visited and canonical_url not in queue:
                            queue.append(canonical_url)
                    fallback_profile_url = "https://www.linkedin.com/in/me/"
                    if (
                        fallback_profile_url not in visited
                        and fallback_profile_url not in queue
                    ):
                        queue.append(fallback_profile_url)

                return result

            result = await asyncio.wait_for(_read_dashboard(), timeout=25)

            if ctx:
                await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        return await run_read_tool("get_profile_analytics", _fetch)
