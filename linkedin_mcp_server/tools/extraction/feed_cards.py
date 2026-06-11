"""Feed and recent-activity card resolution and per-card DOM extraction."""

from __future__ import annotations

import asyncio
import logging
import re
from time import monotonic
from typing import Any

from linkedin_mcp_server.core import handle_modal_close
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core.navigation import goto_and_check
from linkedin_mcp_server.tools._common import parse_count
from linkedin_mcp_server.tools.extraction.activity_text import (
    _build_activity_post_analytics_item,
    _looks_like_analytics_card_text,
    _post_identity,
)
from linkedin_mcp_server.tools.extraction.post_identity import (
    _extract_post_identifier,
)

logger = logging.getLogger(__name__)

_ANALYTICS_DOM_TIMEOUT_SECONDS = 35.0
_ANALYTICS_DOM_TIMEOUT_EXTENDED_SECONDS = 60.0


_ACTIVITY_CARD_TEXT_TIMEOUT_MS = 800


_ACTIVITY_POST_CARD_SELECTORS = (
    "main article",
    "main [role='article']",
    "main [role='listitem']",
    "main div.feed-shared-update-v2",
    "main div.occludable-update",
    "main [data-urn*='activity']",
    "main [data-id*='urn:li:activity']",
)


_ACTIVITY_POST_URL_CANDIDATES = (
    "https://www.linkedin.com/in/me/recent-activity/shares/",
    "https://www.linkedin.com/in/me/recent-activity/posts/",
    "https://www.linkedin.com/in/me/recent-activity/all/",
)


_ACTIVITY_SCROLL_DELAY_SECONDS = 1.5


_ACTIVITY_STAGNANT_SCROLLS = 5


_ENGAGEMENT_TEXT_RE = re.compile(
    r"([\d,.kKmM]+)\s+(reactions?|likes?|comments?|reposts?|impressions?)",
    re.IGNORECASE,
)


_CONTROL_MENU_RE = re.compile(
    r"(?:Open control menu for post by|Hide post by)\s+(.+)",
    re.IGNORECASE,
)


async def _extract_engagement_from_card_dom(card: Any) -> dict[str, int | None]:
    """Extract engagement metrics from DOM elements.

    LinkedIn's 2025+ DOM renders engagement counts as plain text inside
    generic elements (e.g. ``"21 reactions"``, ``"1 comment"``).  CSS
    class names are obfuscated, so we scan all child text nodes and
    also check aria-labels on buttons.
    """
    reactions: int | None = None
    comments: int | None = None
    reposts: int | None = None
    impressions: int | None = None

    _NUM_RE = re.compile(r"([\d,.kKmM]+)")

    # Strategy 1: scan all text nodes in the card for "N reactions", "N comments", etc.
    try:
        card_text = await card.inner_text(timeout=1500)
        if card_text:
            for m in _ENGAGEMENT_TEXT_RE.finditer(card_text):
                metric = m.group(2).lower().rstrip("s")
                val = parse_count(m.group(1))
                if metric in ("reaction", "like") and reactions is None:
                    reactions = val
                elif metric == "comment" and comments is None:
                    comments = val
                elif metric == "repost" and reposts is None:
                    reposts = val
                elif metric == "impression" and impressions is None:
                    impressions = val
    except Exception:
        pass

    # Strategy 2: check aria-labels on buttons, links, and spans.
    _ARIA_SELECTORS: list[tuple[str, str]] = [
        ("button[aria-label*='reaction' i], a[aria-label*='reaction' i]", "reactions"),
        ("button[aria-label*='like' i], a[aria-label*='like' i]", "reactions"),
        ("button[aria-label*='comment' i], a[aria-label*='comment' i]", "comments"),
        ("button[aria-label*='repost' i], a[aria-label*='repost' i]", "reposts"),
        (
            "span[aria-label*='impression' i], a[aria-label*='impression' i]",
            "impressions",
        ),
    ]
    for selector, metric_name in _ARIA_SELECTORS:
        current = {
            "reactions": reactions,
            "comments": comments,
            "reposts": reposts,
            "impressions": impressions,
        }.get(metric_name)
        if current is not None:
            continue
        try:
            loc = card.locator(selector).first
            if await loc.count() == 0:
                continue
            label = await loc.get_attribute("aria-label", timeout=300)
            if not label:
                label = await loc.inner_text(timeout=300)
            if label:
                m = _NUM_RE.search(label)
                if m:
                    val = parse_count(m.group(1))
                    if metric_name == "reactions" and reactions is None:
                        reactions = val
                    elif metric_name == "comments" and comments is None:
                        comments = val
                    elif metric_name == "reposts" and reposts is None:
                        reposts = val
                    elif metric_name == "impressions" and impressions is None:
                        impressions = val
        except Exception:
            continue

    # Strategy 3: scan descendant buttons/links/spans for richer metric text.
    try:
        descendants = card.locator("button, a, span")
        count = min(await descendants.count(), 30)
        for idx in range(count):
            node = descendants.nth(idx)
            label_parts: list[str] = []
            try:
                aria_label = await node.get_attribute("aria-label", timeout=200)
                if aria_label:
                    label_parts.append(aria_label)
            except Exception:
                pass
            try:
                text = await node.inner_text(timeout=200)
                if text:
                    label_parts.append(text)
            except Exception:
                pass

            if not label_parts:
                continue

            sample = " ".join(label_parts)
            for match in _ENGAGEMENT_TEXT_RE.finditer(sample):
                metric = match.group(2).lower().rstrip("s")
                value = parse_count(match.group(1))
                if metric in ("reaction", "like") and reactions is None:
                    reactions = value
                elif metric == "comment" and comments is None:
                    comments = value
                elif metric == "repost" and reposts is None:
                    reposts = value
                elif metric == "impression" and impressions is None:
                    impressions = value
    except Exception:
        pass

    return {
        "reactions": reactions,
        "comments": comments,
        "reposts": reposts,
        "impressions": impressions,
    }


async def _extract_author_from_card(card: Any) -> str | None:
    """Extract the author name from a feed card's DOM structure.

    LinkedIn's 2025+ DOM obfuscates CSS class names.  The most reliable
    source for the author name is the control-menu button whose
    aria-label reads ``"Open control menu for post by <Author Name>"``.
    """
    # Strategy 1: control menu / hide button aria-labels (most reliable)
    for selector in (
        "button[aria-label*='control menu for post by' i]",
        "button[aria-label*='Hide post by' i]",
    ):
        try:
            loc = card.locator(selector).first
            if await loc.count() > 0:
                label = await loc.get_attribute("aria-label", timeout=500)
                if label:
                    m = _CONTROL_MENU_RE.search(label)
                    if m:
                        return m.group(1).strip()
        except Exception:
            continue

    # Strategy 2: legacy CSS selectors (pre-2025 LinkedIn)
    for selector in (
        ".update-components-actor__name span[dir='ltr']",
        ".update-components-actor__name",
        "[data-tracking-control-name*='actor'] span",
        ".feed-shared-actor__name",
        ".feed-shared-actor__title",
    ):
        try:
            loc = card.locator(selector).first
            if await loc.count() > 0:
                name = await loc.inner_text(timeout=500)
                if name and name.strip():
                    return name.strip()
        except Exception:
            continue
    return None


async def _resolve_post_cards(page: Any) -> Any:
    """Resolve feed post containers for the current LinkedIn feed.

    LinkedIn's 2025+ DOM uses obfuscated CSS class names.  Feed posts are
    ``listitem`` elements that contain a heading reading ``"Feed post"``.
    We prioritise this reliable structural pattern before falling back to
    legacy class-based selectors.
    """
    deadline = monotonic() + 6  # 6s cap — give feed time to hydrate

    while monotonic() < deadline:
        # Priority 1: listitem elements containing "Feed post" heading
        # (reliable on 2025+ obfuscated LinkedIn DOM)
        candidates = [
            page.get_by_role("listitem").filter(
                has=page.get_by_role("heading", name="Feed post")
            ),
            page.locator("[role='listitem']:has(h2:has-text('Feed post'))"),
        ]
        for loc in candidates:
            try:
                if await loc.count() > 0:
                    logger.debug(
                        "Resolved %d feed cards via Feed post heading",
                        await loc.count(),
                    )
                    return loc
            except Exception:
                pass

        # Priority 2: legacy CSS selectors (pre-2025 LinkedIn)
        try:
            result = await SELECTORS["feed"]["post_cards"].resolve(page)
            if await result.count() > 0:
                return result
        except Exception:
            logger.debug("Legacy feed post selector chain failed", exc_info=True)

        # Priority 3: broad semantic fallbacks
        for sel in _ACTIVITY_POST_CARD_SELECTORS:
            fallback = page.locator(sel)
            try:
                if await fallback.count() > 0:
                    return fallback
            except Exception:
                pass

        await asyncio.sleep(1)

    # No cards found after deadline — return None so callers produce empty results
    return None


async def _resolve_activity_post_cards(page: Any) -> Any:
    """Resolve recent-activity post containers with broader fallbacks than the main feed."""
    deadline = (
        monotonic() + 4
    )  # tighter cap for analytics keeps interactive latency down
    last_exc: Exception | None = None

    while monotonic() < deadline:
        try:
            cards = await _resolve_post_cards(page)
            if cards is not None and await cards.count() > 0:
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


async def _measure_page_html_length(page: Any) -> int | None:
    try:
        content = await page.content()
    except Exception:
        return None
    return len(content or "")


async def _open_recent_activity_posts_page(page: Any) -> dict[str, Any]:
    """Open the authored-activity surface, falling back conservatively.

    Never raises on total failure. Returns a result dict including:
        attempted_urls: list[str] — every URL we tried, in order
        html_lengths: list[int | None] — parallel; None where navigation failed
        selected_url: str | None — URL that produced cards, or None on no-cards
        any_nav_succeeded: bool — True if any URL's goto_and_check completed,
            regardless of card count. Distinguishes "all navs failed" from
            "navs OK but DOM yielded zero cards" — the latter is the genuine
            empty-page case for zero-authored-post accounts or DOM variants.
        page_url_after_nav: str | None — final URL for the selected page, or None
        page_title: str | None — title for the selected page, or None
        last_error_message: str | None — str(last error) on total fail, else None
    """
    last_error: Exception | None = None
    attempted_urls: list[str] = []
    html_lengths: list[int | None] = []
    any_nav_succeeded = False

    for url in _ACTIVITY_POST_URL_CANDIDATES:
        attempted_urls.append(url)
        try:
            await goto_and_check(page, url)
        except Exception as exc:
            last_error = exc
            html_lengths.append(None)
            logger.debug("Recent activity navigation failed for %s", url, exc_info=True)
            continue

        any_nav_succeeded = True

        try:
            await page.wait_for_selector("main", timeout=8000)
        except Exception:
            logger.debug("No <main> on recent activity page %s; proceeding anyway", url)

        await handle_modal_close(page)

        page_url = getattr(page, "url", None)
        page_title: str | None = None
        try:
            page_title = await page.title()
        except Exception:
            page_title = None
        html_length = await _measure_page_html_length(page)
        html_lengths.append(html_length)

        try:
            cards = await _resolve_activity_post_cards(page)
            card_count = await cards.count()
            logger.debug(
                "Recent activity candidate url=%s final_url=%s cards=%d title=%r html_length=%s",
                url,
                page_url,
                card_count,
                page_title,
                html_length,
            )
            if card_count > 0:
                return {
                    "attempted_urls": attempted_urls,
                    "html_lengths": html_lengths,
                    "selected_url": url,
                    "any_nav_succeeded": True,
                    "page_url_after_nav": page_url,
                    "page_title": page_title,
                    "last_error_message": None,
                }
        except Exception as exc:
            last_error = exc
            logger.debug(
                "Recent activity card resolution failed for %s",
                url,
                exc_info=True,
            )
            logger.debug(
                "Recent activity candidate url=%s final_url=%s cards=error title=%r html_length=%s",
                url,
                page_url,
                page_title,
                html_length,
            )

    return {
        "attempted_urls": attempted_urls,
        "html_lengths": html_lengths,
        "selected_url": None,
        "any_nav_succeeded": any_nav_succeeded,
        "page_url_after_nav": None,
        "page_title": None,
        "last_error_message": str(last_error) if last_error is not None else None,
    }


async def _extract_activity_posts_from_dom(
    page: Any,
    *,
    limit: int,
) -> dict[str, Any]:
    posts: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    processed_count = 0
    stagnant_scrolls = 0
    last_total = 0
    batch_size = max(limit * 2, 8)
    first_batch_logged = False
    scroll_iterations = 0
    stopped_reason = "empty_page"
    timeout_seconds = (
        _ANALYTICS_DOM_TIMEOUT_EXTENDED_SECONDS
        if limit > 10
        else _ANALYTICS_DOM_TIMEOUT_SECONDS
    )
    attempted_urls: list[str] = []
    html_lengths: list[int | None] = []
    selected_url: str | None = None
    any_nav_succeeded = False
    last_error_message: str | None = None

    navigation_start = monotonic()
    activity_page = await _open_recent_activity_posts_page(page)
    attempted_urls = list(activity_page.get("attempted_urls", []))
    html_lengths = list(activity_page.get("html_lengths", []))
    selected_url = activity_page.get("selected_url")
    any_nav_succeeded = bool(activity_page.get("any_nav_succeeded", False))
    last_error_message = activity_page.get("last_error_message")
    logger.info(
        "Recent activity navigation completed in %.2fs via %s",
        monotonic() - navigation_start,
        selected_url or (attempted_urls[-1] if attempted_urls else None),
    )

    if selected_url is None:
        # No URL produced cards. Could be (a) all navigations raised, or (b)
        # navigations succeeded but every URL yielded zero cards (zero-posts
        # account or DOM variant). Short-circuit; let get_my_post_analytics
        # decide stopped_reason based on any_nav_succeeded.
        return {
            "posts": [],
            "posts_requested": limit,
            "posts_returned": 0,
            "scroll_iterations": 0,
            "stopped_reason": "empty_page",
            "activity_url_tried": attempted_urls,
            "activity_url_selected": None,
            "activity_any_nav_succeeded": any_nav_succeeded,
            "activity_page_html_lengths": html_lengths,
            "activity_last_error": last_error_message,
        }

    while len(posts) < limit:
        if monotonic() - navigation_start >= timeout_seconds:
            stopped_reason = "timeout"
            break

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

            identifier = await _extract_post_identifier(card)
            item = _build_activity_post_analytics_item(
                text,
                url=identifier["url"],
                post_urn=identifier["post_urn"],
            )

            # Supplement text-based metrics with DOM-based extraction
            dom_metrics = await _extract_engagement_from_card_dom(card)
            for key in ("reactions", "comments", "reposts", "impressions"):
                if item.get(key) is None and dom_metrics.get(key) is not None:
                    item[key] = dom_metrics[key]

            if item.get("url") is None and identifier["url"] is not None:
                item["url"] = identifier["url"]
            if item.get("post_urn") is None and identifier["post_urn"] is not None:
                item["post_urn"] = identifier["post_urn"]

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
            stopped_reason = "limit_reached"
            break

        processed_count = max(processed_count, scan_end)

        await page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.9))")
        scroll_iterations += 1
        await asyncio.sleep(_ACTIVITY_SCROLL_DELAY_SECONDS)

        if total_cards <= last_total:
            stagnant_scrolls += 1
        else:
            stagnant_scrolls = 0
        last_total = total_cards

        if stagnant_scrolls >= _ACTIVITY_STAGNANT_SCROLLS:
            stopped_reason = (
                "empty_page" if total_cards == 0 and not posts else "stagnant"
            )
            break

    if not posts and stopped_reason == "empty_page":
        stopped_reason = "empty_page"

    logger.info(
        "Recent activity DOM extraction completed with %d posts in %.2fs (reason=%s, scrolls=%d)",
        len(posts),
        monotonic() - navigation_start,
        stopped_reason,
        scroll_iterations,
    )
    return {
        "posts": posts[:limit],
        "posts_requested": limit,
        "posts_returned": len(posts[:limit]),
        "scroll_iterations": scroll_iterations,
        "stopped_reason": stopped_reason,
        "activity_url_tried": attempted_urls,
        "activity_url_selected": selected_url,
        "activity_any_nav_succeeded": any_nav_succeeded,
        "activity_page_html_lengths": html_lengths,
        "activity_last_error": last_error_message,
    }
