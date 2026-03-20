"""Feed browsing and analytics tools."""

from __future__ import annotations

import asyncio
import logging
import re
from time import monotonic
from typing import Any
from urllib.parse import urljoin, urlparse

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core import handle_modal_close
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.scraping.extractor import LinkedInExtractor
from linkedin_mcp_server.tools._common import (
    ensure_page_healthy,
    goto_and_check,
    parse_count,
    run_read_tool,
)

logger = logging.getLogger(__name__)
_ACTIVITY_CARD_TEXT_TIMEOUT_MS = 800
_POST_URL_ATTR_TIMEOUT_MS = 200
_ACTIVITY_POST_CARD_SELECTORS = (
    "main article",
    "main [role='article']",
    "main [role='listitem']",
    "main div.feed-shared-update-v2",
    "main div.occludable-update",
    "main [data-urn*='activity']",
    "main [data-id*='urn:li:activity']",
)


def _extract_metric(text: str, phrase: str) -> int | None:
    pattern_before = re.compile(rf"([\d,.kKmM]+)\s+{re.escape(phrase)}", re.IGNORECASE)
    pattern_after = re.compile(
        rf"{re.escape(phrase)}\s*:?\s*([\d,.kKmM]+)", re.IGNORECASE
    )

    match = pattern_before.search(text) or pattern_after.search(text)
    if not match:
        return None
    return parse_count(match.group(1))


def _extract_time_ago(text: str) -> str | None:
    match = re.search(
        r"(\d+\s*(?:m|h|d|w|mo|yr)s?\s*ago|\d+[mhdw])",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1)


def _extract_profile_analytics_from_text(text: str) -> dict[str, int | None]:
    return {
        "profile_views": _extract_metric(text, "profile views"),
        "search_appearances": _extract_metric(text, "search appearances"),
        "post_impressions": _extract_metric(text, "post impressions"),
    }


def _extract_post_from_text(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    author = lines[0] if lines else ""

    # Skip common feed metadata lines when building text preview.
    content_lines = [
        line
        for line in lines[1:]
        if line.lower() not in {"follow", "like", "comment", "repost", "send"}
    ]
    text_preview = "\n".join(content_lines[:6])

    reactions = _extract_metric(text, "reactions")
    if reactions is None:
        reactions = _extract_metric(text, "likes")
    comments = _extract_metric(text, "comments")

    return {
        "author": author,
        "text": text_preview,
        "reactions_count": reactions,
        "comments_count": comments,
        "time_ago": _extract_time_ago(text),
    }


def _normalize_post_url(href: str | None) -> str | None:
    """Normalize LinkedIn post URLs to canonical feed/activity paths."""
    if not href:
        return None

    candidate = href.strip()
    if not candidate:
        return None

    if candidate.startswith("/"):
        candidate = urljoin("https://www.linkedin.com", candidate)
    elif candidate.startswith("https://linkedin.com"):
        candidate = candidate.replace(
            "https://linkedin.com",
            "https://www.linkedin.com",
            1,
        )
    elif candidate.startswith("http://linkedin.com"):
        candidate = candidate.replace(
            "http://linkedin.com",
            "https://www.linkedin.com",
            1,
        )
    elif candidate.startswith("http://www.linkedin.com"):
        candidate = candidate.replace(
            "http://www.linkedin.com",
            "https://www.linkedin.com",
            1,
        )

    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc.endswith("linkedin.com"):
        return None
    if not any(
        marker in parsed.path for marker in ("/feed/update/", "/posts/", "/activity-")
    ):
        return None

    return f"https://www.linkedin.com{parsed.path}"


async def _extract_post_url(card: Any) -> str | None:
    for selector in (
        "a[href*='/feed/update/']",
        "a[href*='/posts/']",
        "a[href*='/activity-']",
    ):
        locator = card.locator(selector)
        try:
            if await locator.count() == 0:
                continue
            href = await locator.first.get_attribute(
                "href", timeout=_POST_URL_ATTR_TIMEOUT_MS
            )
        except Exception:
            continue
        normalized = _normalize_post_url(href)
        if normalized:
            return normalized
    return None


def _build_post_analytics_item(text: str, url: str | None = None) -> dict[str, Any]:
    summary = _extract_post_from_text(text)
    reactions = _extract_metric(text, "reactions")
    if reactions is None:
        reactions = _extract_metric(text, "likes")

    return {
        "author": summary["author"] or None,
        "url": url,
        "text_preview": (summary["text"] or text[:240])[:240],
        "time_ago": summary["time_ago"],
        "reactions": reactions,
        "comments": _extract_metric(text, "comments"),
        "reposts": _extract_metric(text, "reposts"),
        "impressions": _extract_metric(text, "impressions"),
    }


def _is_activity_metric_line(line: str) -> bool:
    lowered = line.lower()
    if lowered in _ACTIVITY_TAIL_NOISE:
        return True
    return bool(
        re.match(
            r"^(?:[\d,.kKmM]+\s+(?:impressions?|reactions?|likes?|comments?|reposts?|views?)|(?:impressions?|reactions?|likes?|comments?|reposts?|views?)\s*:?\s*[\d,.kKmM]+)$",
            line,
            re.IGNORECASE,
        )
    )


def _is_activity_metadata_line(line: str) -> bool:
    lowered = line.lower()
    if lowered == "follow":
        return True
    if "visible to anyone" in lowered:
        return True
    if "on or off linkedin" in lowered:
        return True
    if "followers" in lowered or "connections" in lowered:
        return True
    if re.fullmatch(
        r"\d+\s*(?:m|h|d|w|mo|yr)s?\s*ago(?:\s*[•·].*)?",
        lowered,
        re.IGNORECASE,
    ):
        return True
    return False


def _build_activity_post_analytics_item(
    text: str,
    url: str | None = None,
) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    while lines and re.match(r"^feed post number \d+$", lines[0], re.IGNORECASE):
        lines.pop(0)

    time_ago = _extract_time_ago(text)
    time_index: int | None = None
    for idx, line in enumerate(lines):
        if _extract_time_ago(line):
            time_index = idx
            break

    author: str | None = None
    prelude = lines[:time_index] if time_index is not None else lines[:3]
    for line in prelude:
        cleaned = re.sub(
            r"\s+(?:posted|reposted)\s+this$",
            "",
            line,
            flags=re.IGNORECASE,
        ).strip()
        if not cleaned or _is_activity_metric_line(cleaned):
            continue
        author = cleaned
        break

    preview_lines = lines[time_index + 1 :] if time_index is not None else lines[1:]
    while preview_lines and _is_activity_metric_line(preview_lines[-1]):
        preview_lines.pop()
    while preview_lines and preview_lines[-1] == author:
        preview_lines.pop()
    preview_lines = [
        line
        for line in preview_lines
        if not _is_activity_metadata_line(line) and line != author
    ]

    text_preview = "\n".join(preview_lines[:8])[:300]

    return {
        "author": author,
        "url": url,
        "text_preview": text_preview,
        "time_ago": time_ago,
        "reactions": _extract_metric(text, "reactions")
        or _extract_metric(text, "likes"),
        "comments": _extract_metric(text, "comments"),
        "reposts": _extract_metric(text, "reposts"),
        "impressions": _extract_metric(text, "impressions"),
    }


def _post_identity(post: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        post.get("url"),
        post.get("time_ago"),
        post.get("text_preview"),
    )


def _looks_like_analytics_card_text(text: str) -> bool:
    """Heuristic to filter recent-activity containers down to real post cards.

    LinkedIn often renders engagement counts as icon+number combos (no keyword),
    so we accept any card with substantial text (> 200 chars) OR one that does
    contain an explicit engagement keyword.  Short strings (< 50 chars) are
    always excluded as they're almost certainly navigation / UI chrome.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    has_engagement_keyword = any(
        token in lowered
        for token in (
            "impressions",
            "reactions",
            "likes",
            "comments",
            "reposts",
            "views",
        )
    )
    if has_engagement_keyword:
        return True
    if len(stripped) < 50:
        return False
    # Posts themselves are usually long; fall back to length for keyword-less cards.
    return len(stripped) > 200


async def _resolve_post_cards(page: Any) -> Any:
    """Resolve feed post containers with a DOM-fallback for the current LinkedIn feed."""
    deadline = monotonic() + 4  # 4s cap — upstream hydration wait already ran
    last_exc: Exception | None = None

    while monotonic() < deadline:
        try:
            return await SELECTORS["feed"]["post_cards"].resolve(page)
        except Exception as exc:
            last_exc = exc
            logger.debug(
                "Primary feed post selector failed, trying hydrated fallbacks",
                exc_info=True,
            )

        fallback_candidates = [
            page.locator("h2:has-text('Feed post')").locator(
                "xpath=ancestor::*[@role='listitem'][1]"
            ),
            page.get_by_role("listitem").filter(
                has=page.locator("h2:has-text('Feed post')")
            ),
            page.locator("[data-view-name='feed-full-update']").locator(
                "xpath=ancestor::*[@role='listitem'][1]"
            ),
        ]

        for fallback in fallback_candidates:
            try:
                if await fallback.count() > 0:
                    return fallback
            except Exception:
                logger.debug("Feed post fallback locator failed", exc_info=True)

        await asyncio.sleep(1)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Could not resolve feed post cards")


_TIME_AGO_RE = re.compile(r"^\d+\s*(?:m|h|d|w|mo|yr)s?$", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(r"^([\d,.kKmM]+)$")
_SECTION_END_RE = re.compile(
    r"\n(?:Experience|Education|Skills|Licenses|Recommendations|Honors|Languages|Interests|Top Voices|People you may know|People also viewed)\n",
    re.IGNORECASE,
)
_ACTIVITY_TAIL_NOISE = {
    "comment",
    "follow",
    "like",
    "more",
    "react",
    "repost",
    "send",
    "share",
    "view",
}


def _parse_posts_from_activity_text(text: str, limit: int) -> list[dict[str, Any]]:
    """Parse post analytics from raw profile-page innerText.

    LinkedIn's profile Activity section has the shape (per post):
        <Author Name> [split point: "posted/reposted this • "] <time_ago>
        <time_ago repeated>
        <post body text>
        …
        <bare reaction count>   ← just a number, no label
        <N> comments
        <N> reposts             ← sometimes
        <Author Name>           ← start of NEXT post's attribution

    After re.split() on the "posted/reposted this •" pattern:
        parts[0]  = preamble ending with "<Author Name> "
        parts[1]  = "<time_ago>\n<time_ago>\n<content>\n<counts>\n<NextAuthor> "
        parts[2]  = …
    So the author of post i lives at the END of parts[i] (0-indexed).
    """
    match = _SECTION_END_RE.search(text)
    if match:
        text = text[: match.start()]

    _SPLIT_PAT = re.compile(r"(?:posted|reposted)\s+this\s*[•·]?\s*", re.IGNORECASE)
    parts = _SPLIT_PAT.split(text)

    posts: list[dict[str, Any]] = []
    for idx, raw_block in enumerate(parts[1:], start=1):
        if not raw_block.strip():
            continue

        lines = [ln.strip() for ln in raw_block.splitlines() if ln.strip()]
        if not lines:
            continue

        # Author is the LAST non-empty line of the PREVIOUS part
        prev_lines = [ln.strip() for ln in parts[idx - 1].splitlines() if ln.strip()]
        author = prev_lines[-1] if prev_lines else None

        # First line(s) of the block are usually the time-ago stamp ("1d", "5d", …)
        time_ago: str | None = None
        if lines and _TIME_AGO_RE.match(lines[0]):
            time_ago = lines.pop(0)
            # LinkedIn often repeats the stamp on the very next line
            if lines and _TIME_AGO_RE.match(lines[0]):
                lines.pop(0)

        # Strip trailing UI labels and the next post's author name so the
        # backward engagement scanner can reach real metrics.
        while lines:
            last_line = lines[-1]
            last_lower = last_line.lower()
            if last_lower in _ACTIVITY_TAIL_NOISE:
                lines.pop()
                continue
            if last_line == author:
                lines.pop()
                continue
            if len(last_line) < 60 and not re.search(r"[\d#]", last_line):
                lines.pop()
                continue
            break

        # Separate engagement tail from post body.
        # Engagement lines appear AFTER the body (near the end of the block):
        # a bare number (reactions), then "N comments", then "N reposts".
        # Walk backwards from the end to collect them.
        reactions: int | None = None
        comments: int | None = None
        reposts: int | None = None

        while lines:
            last = lines[-1]
            m_comments = re.match(r"^([\d,.kKmM]+)\s+comments?$", last, re.IGNORECASE)
            m_reposts = re.match(r"^([\d,.kKmM]+)\s+reposts?$", last, re.IGNORECASE)
            m_bare = _BARE_NUMBER_RE.match(last)

            if m_comments:
                comments = parse_count(m_comments.group(1))
                lines.pop()
            elif m_reposts:
                reposts = parse_count(m_reposts.group(1))
                lines.pop()
            elif m_bare and reactions is None:
                # A bare trailing number is the reaction count on profile activity.
                reactions = parse_count(m_bare.group(1))
                lines.pop()
            else:
                break

        text_preview = "\n".join(lines[:8])[:300]

        posts.append(
            {
                "author": author,
                "url": None,
                "text_preview": text_preview,
                "time_ago": time_ago or _extract_time_ago(raw_block),
                "reactions": reactions,
                "comments": comments,
                "reposts": reposts,
                "impressions": None,  # not available on profile page
            }
        )

        if len(posts) >= limit:
            break

    return posts


async def _resolve_activity_post_cards(page: Any) -> Any:
    """Resolve recent-activity post containers with broader fallbacks than the main feed."""
    deadline = (
        monotonic() + 4
    )  # tighter cap for analytics keeps interactive latency down
    last_exc: Exception | None = None

    while monotonic() < deadline:
        try:
            cards = await _resolve_post_cards(page)
            if await cards.count() > 0:
                return cards
        except Exception as exc:
            last_exc = exc
            logger.debug(
                "Primary activity post selector failed, trying broader activity fallbacks",
                exc_info=True,
            )

        for selector in _ACTIVITY_POST_CARD_SELECTORS:
            fallback = page.locator(selector)
            try:
                if await fallback.count() > 0:
                    return fallback
            except Exception:
                logger.debug("Activity post fallback locator failed", exc_info=True)

        await asyncio.sleep(1)

    if last_exc is not None:
        raise last_exc
    return page.locator("main article")


async def _extract_activity_posts_from_dom(
    page: Any,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    processed_count = 0
    stagnant_scrolls = 0
    last_total = 0
    batch_size = max(limit * 2, 8)
    first_batch_logged = False

    navigation_start = monotonic()
    await goto_and_check(page, "https://www.linkedin.com/in/me/recent-activity/all/")
    logger.info(
        "Recent activity navigation completed in %.2fs",
        monotonic() - navigation_start,
    )

    try:
        await page.wait_for_selector("main", timeout=8000)
    except Exception:
        logger.debug("No <main> on recent activity page; proceeding anyway")

    await handle_modal_close(page)

    while len(posts) < limit and stagnant_scrolls < 3:
        resolve_start = monotonic()
        cards = await _resolve_activity_post_cards(page)
        resolve_duration = monotonic() - resolve_start
        total_cards = await cards.count()
        scan_end = min(total_cards, processed_count + batch_size)
        batch_start = monotonic()

        for idx in range(processed_count, scan_end):
            card = cards.nth(idx)
            try:
                text = await card.inner_text(timeout=_ACTIVITY_CARD_TEXT_TIMEOUT_MS)
            except Exception:
                continue

            if not text:
                continue

            item = _build_activity_post_analytics_item(
                text,
                url=await _extract_post_url(card),
            )
            if not _looks_like_analytics_card_text(text):
                if not (
                    item["time_ago"]
                    or item["text_preview"]
                    or item["impressions"] is not None
                    or item["comments"] is not None
                    or item["reposts"] is not None
                    or item["reactions"] is not None
                ):
                    continue

            identity = _post_identity(item)
            if identity in seen:
                continue
            seen.add(identity)
            posts.append(item)

            if len(posts) >= limit:
                break

        if not first_batch_logged:
            logger.info(
                "Recent activity first batch resolved in %.2fs and scanned %d cards in %.2fs",
                resolve_duration,
                max(scan_end - processed_count, 0),
                monotonic() - batch_start,
            )
            first_batch_logged = True

        if len(posts) >= limit:
            break

        processed_count = max(processed_count, scan_end)

        await page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.9))")
        await asyncio.sleep(1.2)

        if total_cards <= last_total:
            stagnant_scrolls += 1
        else:
            stagnant_scrolls = 0
        last_total = total_cards

    logger.info(
        "Recent activity DOM extraction completed with %d posts in %.2fs",
        len(posts),
        monotonic() - navigation_start,
    )
    return posts[:limit]


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

            await goto_and_check(page, "https://www.linkedin.com/feed/")
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
            stagnant_scrolls = 0
            last_total = 0

            _feed_deadline = (
                monotonic() + 45
            )  # 45s budget keeps us inside 60s MCP ceiling
            while (
                len(posts) < safe_count
                and stagnant_scrolls < 2
                and monotonic() < _feed_deadline
            ):
                cards = await _resolve_post_cards(page)
                total_cards = await cards.count()

                for idx in range(len(posts), min(total_cards, safe_count)):
                    card = cards.nth(idx)
                    try:
                        text = await card.inner_text(timeout=2500)
                    except Exception:
                        continue

                    if not text or not text.strip():
                        continue

                    # Skip non-post cards (ads, suggestions, chrome) — real
                    # posts have substantial text or engagement keywords.
                    stripped = text.strip()
                    if len(stripped) < 50:
                        continue

                    post = _extract_post_from_text(text)
                    post["url"] = await _extract_post_url(card)
                    posts.append(post)

                if len(posts) >= safe_count:
                    break

                await page.evaluate(
                    "window.scrollBy(0, Math.floor(window.innerHeight * 0.9))"
                )
                await asyncio.sleep(1.2)

                if total_cards <= last_total:
                    stagnant_scrolls += 1
                else:
                    stagnant_scrolls = 0
                last_total = total_cards

            if ctx:
                await ctx.report_progress(
                    progress=100,
                    total=100,
                    message=f"Extracted {len(posts)} posts",
                )

            return {"posts": posts[:safe_count]}

        return await run_read_tool("browse_feed", _fetch)

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
            try:
                posts = await asyncio.wait_for(
                    _extract_activity_posts_from_dom(page, limit=safe_limit),
                    timeout=35.0,
                )
            except (asyncio.TimeoutError, Exception):
                logger.warning(
                    "Recent activity DOM extraction failed; falling back to profile text",
                    exc_info=True,
                )

            if ctx:
                await ctx.report_progress(
                    progress=80, total=100, message="Parsing posts"
                )

            if not posts:
                extractor = LinkedInExtractor(page)
                text = await extractor.extract_page("https://www.linkedin.com/in/me/")
                fallback_posts = _parse_posts_from_activity_text(text, safe_limit)

                seen = {_post_identity(post) for post in posts}
                for post in fallback_posts:
                    identity = _post_identity(post)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    posts.append(post)
                    if len(posts) >= safe_limit:
                        break

            if ctx:
                await ctx.report_progress(progress=100, total=100, message="Complete")

            return {"posts": posts[:safe_limit]}

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

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading dashboard"
                )

            async def _read_dashboard() -> dict[str, int | None]:
                await goto_and_check(page, "https://www.linkedin.com/dashboard/")

                dashboard_text = ""
                try:
                    await page.wait_for_selector("main", timeout=3000)
                except Exception:
                    logger.debug("Dashboard analytics: no <main> selector")

                try:
                    widget = page.locator(
                        "section, div[data-view-name*='dashboard'], main"
                    ).first
                    if await widget.count() > 0:
                        dashboard_text = await widget.inner_text(timeout=2000)
                except Exception:
                    dashboard_text = ""

                if not any(
                    value is not None
                    for value in _extract_profile_analytics_from_text(
                        dashboard_text
                    ).values()
                ):
                    try:
                        await page.evaluate(
                            "window.scrollBy(0, document.body.scrollHeight * 0.5)"
                        )
                    except Exception:
                        logger.debug("Dashboard analytics: scroll fallback unavailable")
                    # Wait for body to stabilise (SPA redirect can transiently remove it)
                    try:
                        await page.wait_for_selector("body", timeout=8000)
                    except Exception:
                        logger.debug("Dashboard analytics: body wait timed out")
                    body_text = await page.locator("body").inner_text(timeout=8000)
                    return _extract_profile_analytics_from_text(body_text)

                return _extract_profile_analytics_from_text(dashboard_text)

            result = await asyncio.wait_for(_read_dashboard(), timeout=25)

            if ctx:
                await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        return await run_read_tool("get_profile_analytics", _fetch)
