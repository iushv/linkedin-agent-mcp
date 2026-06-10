"""Feed browsing and analytics tools."""

from __future__ import annotations

import asyncio
import logging
import re
from time import monotonic
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.pagination import build_paginated_response, decode_cursor
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core import handle_modal_close
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.scraping.extractor import LinkedInExtractor
from linkedin_mcp_server.tools._common import (
    FEED_NAVIGATION_TIMEOUT_MS,
    effective_navigation_timeout_ms,
    ensure_page_healthy,
    goto_and_check,
    normalize_post_reference,
    parse_count,
    run_read_tool,
)

logger = logging.getLogger(__name__)
_ACTIVITY_CARD_TEXT_TIMEOUT_MS = 800
_POST_URL_ATTR_TIMEOUT_MS = 200
_POST_IDENTIFIER_ANCESTOR_DEPTH = 6
_POST_IDENTIFIER_SCRIPT_WINDOW_CHARS = 4000
_ACTIVITY_POST_CARD_SELECTORS = (
    "main article",
    "main [role='article']",
    "main [role='listitem']",
    "main div.feed-shared-update-v2",
    "main div.occludable-update",
    "main [data-urn*='activity']",
    "main [data-id*='urn:li:activity']",
)
_ACTIVITY_URN_RE = re.compile(r"urn:li:activity:\d+")
_CARD_TYPE_SUGGESTED_MARKERS = ("suggested for you",)
_POST_IDENTIFIER_SCRIPT_CACHE_KEY = "__linkedinMcpHydrationScriptsV1"
_NON_ACTIONABLE_CARD_TYPES = {
    "announcement",
    "newsletter",
    "promoted",
    "sponsored",
    "suggested",
}
_BROWSE_FEED_INTERNAL_BUDGET_SECONDS = 60.0
_BROWSE_FEED_STAGNANT_SCROLLS = 5
_BROWSE_FEED_SCROLL_DELAY_SECONDS = 1.2
_BROWSE_FEED_PATIENT_SCROLL_DELAY_SECONDS = 2.0
_ACTIVITY_SCROLL_DELAY_SECONDS = 1.5
_ACTIVITY_STAGNANT_SCROLLS = 5
_ANALYTICS_DOM_TIMEOUT_SECONDS = 35.0
_ANALYTICS_DOM_TIMEOUT_EXTENDED_SECONDS = 60.0
_ACTIVITY_POST_URL_CANDIDATES = (
    "https://www.linkedin.com/in/me/recent-activity/shares/",
    "https://www.linkedin.com/in/me/recent-activity/posts/",
    "https://www.linkedin.com/in/me/recent-activity/all/",
)
_POST_REACTIONS_STAGNANT_SCROLLS = 5
_POST_REACTIONS_SCROLL_DELAY_SECONDS = 1.0
_POST_COMMENTS_LOAD_MORE_CAP = 10
_POST_COMMENTS_LOAD_DELAY_SECONDS = 0.75
_COMMENT_TEXT_SELECTORS = (
    ".comments-comment-item__main-content",
    ".comments-comment-item-content-body",
    ".update-components-text",
    "[data-test-id='main-comment-content']",
)
_COMMENT_HEADLINE_SELECTORS = (
    ".comments-comment-item__header-subtitle",
    ".comments-post-meta__headline",
    ".comments-comment-meta__description",
    "span.t-12.t-normal",
)
_COMMENT_TEXT_SELECTORS_V2 = (
    ".comments-comment-item__main-content",
    "span.comments-comment-item__main-content",
    ".comments-comment-item-content-body",
    "[data-test-id='comment-content']",
    "span.update-components-text span.break-words",
    "span.break-words",
)
_COMMENT_TIME_SELECTORS = (
    "time.comments-comment-meta__timestamp",
    ".comments-comment-meta__timestamp",
    "time[datetime]",
    "a.comments-comment-meta__timestamp-link time",
    "span[aria-label$=' ago']",
)
_HEADLINE_SELECTORS = (
    ".artdeco-entity-lockup__subtitle",
    ".artdeco-entity-lockup__caption",
    ".comments-post-meta__headline",
    "div[class*='subtitle']",
    "span[aria-hidden='true']",
)


def _extract_metric(text: str, phrase: str) -> int | None:
    # Tight patterns: number and phrase on the same or adjacent line
    pattern_before = re.compile(rf"([\d,.kKmM]+)\s+{re.escape(phrase)}", re.IGNORECASE)
    pattern_after = re.compile(
        rf"{re.escape(phrase)}\s*:?\s*([\d,.kKmM]+)", re.IGNORECASE
    )

    match = pattern_before.search(text) or pattern_after.search(text)
    if match:
        return parse_count(match.group(1))

    # Loose pattern: number on one line, phrase within 2 lines below.
    # Handles LinkedIn dashboard widgets where DOM renders them in separate
    # elements, producing ``181\nDiscover who...\nprofile views``.
    loose_before = re.compile(
        rf"([\d,.kKmM]+)\s*\n(?:[^\n]{{0,80}}\n){{0,2}}\s*{re.escape(phrase)}",
        re.IGNORECASE,
    )
    m = loose_before.search(text)
    if m:
        return parse_count(m.group(1))

    return None


def _feed_navigation_timeout_ms() -> int:
    """Use a longer timeout for feed pages, which are among the slowest to hydrate."""
    return effective_navigation_timeout_ms(FEED_NAVIGATION_TIMEOUT_MS)


async def _invalidate_post_identifier_script_cache(page: Any) -> None:
    """Force a fresh hydration-script snapshot after feed content changes."""
    try:
        await page.evaluate(
            """(cacheKey) => {
                try {
                  delete window[cacheKey];
                } catch {
                  window[cacheKey] = undefined;
                }
            }""",
            _POST_IDENTIFIER_SCRIPT_CACHE_KEY,
        )
    except Exception:
        logger.debug("Could not invalidate hydration script cache", exc_info=True)


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
        "profile_views": (
            _extract_metric(text, "profile viewers")
            or _extract_metric(text, "profile views")
        ),
        "search_appearances": _extract_metric(text, "search appearances"),
        "post_impressions": _extract_metric(text, "post impressions"),
    }


_NUM_IN_TEXT_RE = re.compile(r"([\d,.kKmM]+)")


async def _extract_profile_analytics_from_dom(
    page: Any,
) -> dict[str, int | None]:
    """Extract profile analytics from DOM elements (links, aria-labels).

    LinkedIn's 2025 feed page renders analytics in a menu widget:
      <a href="/me/profile-views/"> "Profile viewers 210" </a>
      <a href="/analytics/creator/content/"> "Post impressions 3,475" </a>
    The text format is "Label N" — the number is at the end.
    """
    result: dict[str, int | None] = {
        "profile_views": None,
        "search_appearances": None,
        "post_impressions": None,
    }

    _METRIC_LINK_MAP = [
        # 2025 feed page hrefs (from live DOM inspection)
        ("a[href*='profile-views']", "profile_views"),
        ("a[href*='search-appearances']", "search_appearances"),
        ("a[href*='analytics/creator']", "post_impressions"),
        # Legacy hrefs
        ("a[href*='post-impressions']", "post_impressions"),
        ("a[href*='post_impressions']", "post_impressions"),
        # Data control names
        ("[data-control-name*='profile_views']", "profile_views"),
        ("[data-control-name*='search_appearances']", "search_appearances"),
    ]

    # Also try aria-label-based detection (e.g. div[aria-label="Profile viewers 210"])
    _ARIA_LABEL_MAP = [
        ("div[aria-label*='Profile viewer' i]", "profile_views"),
        ("div[aria-label*='Post impression' i]", "post_impressions"),
        ("div[aria-label*='Search appearance' i]", "search_appearances"),
    ]

    for selector, key in _METRIC_LINK_MAP + _ARIA_LABEL_MAP:
        if result[key] is not None:
            continue
        try:
            loc = page.locator(selector).first
            if await loc.count() == 0:
                continue
            # Try aria-label first
            label = await loc.get_attribute("aria-label", timeout=500)
            if label:
                m = _NUM_IN_TEXT_RE.search(label)
                if m:
                    result[key] = parse_count(m.group(1))
                    continue
            # Fall back to innerText (e.g. "Profile viewers 210")
            text = await loc.inner_text(timeout=500)
            if text:
                m = _NUM_IN_TEXT_RE.search(text)
                if m:
                    result[key] = parse_count(m.group(1))
        except Exception:
            continue

    return result


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


def _classify_card(text: str) -> str:
    """Classify feed cards for actionability diagnostics."""
    normalized = " ".join(text.strip().lower().split())
    head = normalized[:240]

    if head.startswith("announcement"):
        return "announcement"
    if head.startswith("newsletter"):
        return "newsletter"
    if "sponsored" in head:
        return "sponsored"
    if "promoted" in head:
        return "promoted"
    if any(marker in head for marker in _CARD_TYPE_SUGGESTED_MARKERS):
        return "suggested"
    if head.startswith("suggested"):
        return "suggested"
    return "regular"


def _is_actionable_card_type(card_type: str) -> bool:
    return card_type not in _NON_ACTIONABLE_CARD_TYPES


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


def _extract_activity_urn(value: str | None) -> str | None:
    if not value:
        return None
    match = _ACTIVITY_URN_RE.search(unquote(value))
    if not match:
        return None
    return match.group(0)


def _activity_url_from_urn(post_urn: str | None) -> str | None:
    if not post_urn:
        return None
    return f"https://www.linkedin.com/feed/update/{post_urn}"


def _build_post_identifier(
    *,
    url: str | None = None,
    post_urn: str | None = None,
    strategy: str | None = None,
) -> dict[str, str | None]:
    if url is None and post_urn is not None:
        url = _activity_url_from_urn(post_urn)
    result: dict[str, str | None] = {"url": url, "post_urn": post_urn}
    if strategy is not None:
        result["strategy"] = strategy
    return result


def _post_identifier_from_href(href: str | None) -> dict[str, str | None] | None:
    normalized_url = _normalize_post_url(href)
    post_urn = _extract_activity_urn(href)
    if normalized_url is None and post_urn is None:
        return None
    return _build_post_identifier(url=normalized_url, post_urn=post_urn)


def _absolute_linkedin_url(href: str | None) -> str | None:
    if not href:
        return None
    return urljoin("https://www.linkedin.com", href)


def _parse_named_total(text: str | None, noun_pattern: str) -> int | None:
    if not text:
        return None
    match = re.search(
        rf"([\d,.kKmM]+)\s+(?:total\s+)?{noun_pattern}",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return parse_count(match.group(1))


def _normalize_reaction_type(label: str | None) -> str | None:
    if not label:
        return None
    lowered = label.strip().lower()
    for reaction in (
        "celebrate",
        "support",
        "insightful",
        "love",
        "funny",
        "like",
    ):
        if reaction in lowered:
            return reaction
    return None


def _clean_member_name(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = " ".join(raw.split())
    for marker in (" View ", " • "):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()

    tokens = cleaned.split()
    max_repeat = min(4, len(tokens) // 2)
    for size in range(max_repeat, 0, -1):
        if tokens[:size] == tokens[size : size * 2]:
            cleaned = " ".join(tokens[:size])
            break

    return cleaned or None


async def _first_text_from_selectors(
    scope: Any,
    selectors: tuple[str, ...],
    *,
    timeout_ms: int = 500,
) -> str | None:
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if await locator.count() == 0:
                continue
            text = await locator.inner_text(timeout=timeout_ms)
        except Exception:
            continue
        if text and text.strip():
            return " ".join(text.split())
    return None


async def _find_profile_link(scope: Any) -> tuple[str | None, str | None]:
    try:
        links = scope.locator("a[href*='/in/']")
        count = min(await links.count(), 5)
    except Exception:
        return None, None

    for idx in range(count):
        link = links.nth(idx)
        try:
            name = await link.inner_text(timeout=500)
            href = await link.get_attribute("href", timeout=500)
        except Exception:
            continue
        normalized_name = _clean_member_name(name)
        if normalized_name:
            return normalized_name, _absolute_linkedin_url(href)
    return None, None


async def _scroll_locator_to_bottom(locator: Any) -> None:
    try:
        await locator.evaluate(
            """(el) => {
                if (el && typeof el.scrollTop === "number") {
                    el.scrollTop = el.scrollHeight;
                } else if (el?.scrollIntoView) {
                    el.scrollIntoView({ block: "end" });
                }
            }"""
        )
    except Exception:
        logger.debug("Could not scroll locator", exc_info=True)


async def _resolve_optional_locator(group: str, key: str, page: Any) -> Any | None:
    try:
        return await SELECTORS[group][key].resolve(page)
    except Exception:
        return None


async def _find_visible_css_locator(
    page: Any,
    selectors: tuple[str, ...],
) -> Any | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            if await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def _extract_reaction_type_from_row(row: Any) -> str | None:
    icon_chain = SELECTORS["post_reactions"]["reaction_icon"]
    for strategy in icon_chain.strategies:
        try:
            locator = strategy.locator(row)
            count = min(await locator.count(), 5)
        except Exception:
            continue

        for idx in range(count):
            node = locator.nth(idx)
            for attr in ("alt", "aria-label", "title"):
                try:
                    value = await node.get_attribute(attr, timeout=500)
                except Exception:
                    continue
                reaction_type = _normalize_reaction_type(value)
                if reaction_type:
                    return reaction_type
    return None


async def _extract_reactor_row(row: Any) -> dict[str, Any] | None:
    name, profile_url = await _find_profile_link(row)
    if not name and not profile_url:
        return None

    headline = await _first_text_from_selectors(row, _HEADLINE_SELECTORS)
    reaction_type = await _extract_reaction_type_from_row(row)
    return {
        "name": name,
        "profile_url": profile_url,
        "headline": headline,
        "reaction_type": reaction_type,
    }


def _same_linkedin_profile(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    try:
        return urlparse(left).path.rstrip("/") == urlparse(right).path.rstrip("/")
    except Exception:
        return left == right


async def _is_nested_comment_row(row: Any) -> bool:
    for attr_name in ("aria-level", "class", "data-test-id"):
        try:
            value = await row.get_attribute(attr_name, timeout=300)
        except Exception:
            continue
        if not value:
            continue
        lowered = str(value).lower()
        if attr_name == "aria-level":
            try:
                if int(lowered) > 1:
                    return True
            except ValueError:
                pass
        if "reply" in lowered:
            return True
    return False


async def _debug_comment_row_result(
    row: Any,
    *,
    name: str | None,
    profile_url: str | None,
    headline: str | None,
    comment_text: str | None,
    time_ago: str | None,
    reason: str,
) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return

    try:
        row_text = await row.inner_text(timeout=500)
    except Exception:
        row_text = ""
    try:
        row_class = await row.get_attribute("class", timeout=300)
    except Exception:
        row_class = None

    logger.debug(
        "Comment row parse: reason=%s class=%s name=%r profile_url=%r headline=%r time_ago=%r comment_text=%r row=%r",
        reason,
        row_class,
        name,
        profile_url,
        headline,
        time_ago,
        comment_text,
        " ".join(row_text.split())[:200] if row_text else "",
    )


async def _extract_comment_row(
    row: Any,
    *,
    post_author_profile_url: str | None = None,
) -> dict[str, Any] | None:
    if await _is_nested_comment_row(row):
        await _debug_comment_row_result(
            row,
            name=None,
            profile_url=None,
            headline=None,
            comment_text=None,
            time_ago=None,
            reason="nested_reply",
        )
        return None

    name, profile_url = await _find_profile_link(row)
    if not name and not profile_url:
        await _debug_comment_row_result(
            row,
            name=name,
            profile_url=profile_url,
            headline=None,
            comment_text=None,
            time_ago=None,
            reason="missing_profile_link",
        )
        return None

    if _same_linkedin_profile(profile_url, post_author_profile_url):
        await _debug_comment_row_result(
            row,
            name=name,
            profile_url=profile_url,
            headline=None,
            comment_text=None,
            time_ago=None,
            reason="matches_post_author",
        )
        return None

    headline = await _first_text_from_selectors(row, _COMMENT_HEADLINE_SELECTORS)
    comment_text = await _first_text_from_selectors(row, _COMMENT_TEXT_SELECTORS_V2)
    time_ago = await _first_text_from_selectors(row, _COMMENT_TIME_SELECTORS)
    try:
        row_text = await row.inner_text(timeout=500)
    except Exception:
        row_text = ""

    reactions_count = _extract_metric(row_text, "reactions")
    if reactions_count is None:
        reactions_count = _extract_metric(row_text, "likes")

    if not comment_text and row_text:
        lines = [line.strip() for line in row_text.splitlines() if line.strip()]
        filtered_lines = [
            line
            for line in lines
            if line != name
            and line != headline
            and line != time_ago
            and line.lower() not in {"like", "reply"}
        ]
        comment_text = filtered_lines[0] if filtered_lines else None

    if not comment_text:
        await _debug_comment_row_result(
            row,
            name=name,
            profile_url=profile_url,
            headline=headline,
            comment_text=comment_text,
            time_ago=time_ago,
            reason="missing_comment_text",
        )

    return {
        "name": name,
        "profile_url": profile_url,
        "headline": headline,
        "comment_text": comment_text,
        "time_ago": time_ago,
        "reactions_count": reactions_count,
    }


async def _extract_post_author_profile_url(page: Any) -> str | None:
    for selector in (
        ".feed-shared-actor__container a[href*='/in/']",
        ".update-components-actor a[href*='/in/']",
        "a[data-tracking-control-name*='actor'][href*='/in/']",
    ):
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            href = await locator.get_attribute("href", timeout=500)
        except Exception:
            continue
        absolute = _absolute_linkedin_url(href)
        if absolute:
            return absolute
    return None


async def _resolve_reaction_row_locator(modal: Any) -> Any | None:
    for selector in (
        ".social-details-reactors-tab-body li",
        "li.artdeco-list__item",
        "[data-test-id*='reactor']",
        "[role='listitem']",
    ):
        try:
            locator = modal.locator(selector)
            if await locator.count() > 0:
                return locator
        except Exception:
            continue
    return None


async def _load_reaction_modal(
    page: Any,
    modal: Any,
    *,
    target_count: int,
) -> dict[str, Any]:
    last_count = -1
    stagnant = 0
    warnings: list[str] = []
    current_modal = modal
    row_locator = None
    total: int | None = None

    # LinkedIn often renders a shell dialog before swapping in the hydrated
    # reactions dialog. Re-resolve the modal during the initial load window so
    # we do not bind row lookups to a stale detached node.
    for _ in range(6):
        try:
            current_modal = await SELECTORS["post_reactions"]["modal"].find(page)
        except Exception:
            current_modal = modal
        row_locator = await _resolve_reaction_row_locator(current_modal)
        try:
            loaded_count = await row_locator.count() if row_locator is not None else 0
        except Exception:
            loaded_count = 0
        if total is None:
            try:
                total_text = await current_modal.inner_text(timeout=1000)
            except Exception:
                total_text = None
            total = _parse_named_total(total_text, r"reactions?")
        if loaded_count > 0:
            break
        await asyncio.sleep(0.5)

    if row_locator is None:
        return {
            "rows": None,
            "loaded_count": 0,
            "total": total,
            "partial": False,
            "warnings": [],
        }

    while True:
        try:
            loaded_count = await row_locator.count()
        except Exception:
            loaded_count = 0

        if loaded_count >= target_count:
            break
        if loaded_count <= last_count:
            stagnant += 1
        else:
            stagnant = 0
        if stagnant >= _POST_REACTIONS_STAGNANT_SCROLLS:
            break
        last_count = loaded_count

        await _scroll_locator_to_bottom(current_modal)
        await asyncio.sleep(_POST_REACTIONS_SCROLL_DELAY_SECONDS)

    try:
        loaded_count = await row_locator.count()
    except Exception:
        loaded_count = 0

    partial = total is not None and loaded_count < min(target_count, total)
    if partial:
        warnings.append(
            "Reaction modal did not load enough rows to satisfy the requested page."
        )

    return {
        "rows": row_locator,
        "loaded_count": loaded_count,
        "total": total,
        "partial": partial,
        "warnings": warnings,
    }


async def _load_comment_rows(
    page: Any,
    *,
    target_count: int,
) -> dict[str, Any]:
    container = await _resolve_optional_locator("post_commenters", "container", page)
    if container is None:
        return {
            "rows": None,
            "loaded_count": 0,
            "total": None,
            "partial": False,
            "warnings": [],
            "load_more_clicks": 0,
        }

    row_locator = container.locator(
        "article.comments-comment-item, "
        "article.comments-comment-entity, "
        "li.comments-comment-item, "
        "[data-test-id='comment-item'], "
        "[data-id^='urn:li:comment:']"
    )

    try:
        total_text = await container.inner_text(timeout=1000)
    except Exception:
        total_text = None
    total = _parse_named_total(total_text, r"comments?")

    warnings: list[str] = []
    load_more_clicks = 0

    while load_more_clicks < _POST_COMMENTS_LOAD_MORE_CAP:
        try:
            loaded_count = await row_locator.count()
        except Exception:
            loaded_count = 0

        if loaded_count >= target_count:
            break

        buttons = await _resolve_optional_locator("post_commenters", "load_more", page)
        if buttons is None:
            break
        try:
            button_count = await buttons.count()
        except Exception:
            button_count = 0
        if button_count == 0:
            break

        clicked = False
        for idx in range(min(button_count, 3)):
            button = buttons.nth(idx)
            try:
                await button.click()
                clicked = True
                load_more_clicks += 1
                break
            except Exception:
                continue

        if not clicked:
            break

        await asyncio.sleep(_POST_COMMENTS_LOAD_DELAY_SECONDS)

    try:
        loaded_count = await row_locator.count()
    except Exception:
        loaded_count = 0

    remaining_buttons = await _resolve_optional_locator(
        "post_commenters", "load_more", page
    )
    remaining_button_count = 0
    if remaining_buttons is not None:
        try:
            remaining_button_count = await remaining_buttons.count()
        except Exception:
            remaining_button_count = 0

    partial = loaded_count < target_count and (
        load_more_clicks >= _POST_COMMENTS_LOAD_MORE_CAP or remaining_button_count > 0
    )
    if load_more_clicks >= _POST_COMMENTS_LOAD_MORE_CAP and partial:
        warnings.append("Stopped after 10 comment pagination clicks.")
    elif partial:
        warnings.append(
            "Comment list did not load enough rows to satisfy the requested page."
        )

    return {
        "rows": row_locator,
        "loaded_count": loaded_count,
        "total": total,
        "partial": partial,
        "warnings": warnings,
        "load_more_clicks": load_more_clicks,
    }


def _is_engagement_actionable_identifier(
    *,
    url: str | None,
    post_urn: str | None,
) -> bool:
    """True when the identifier is safe for engagement tools."""
    if post_urn:
        return True
    if not url:
        return False
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    return "/feed/update/" in path or "/activity-" in path


_ENGAGEMENT_TEXT_RE = re.compile(
    r"([\d,.kKmM]+)\s+(reactions?|likes?|comments?|reposts?|impressions?)",
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


async def _extract_post_identifier(card: Any) -> dict[str, str | None]:
    """Extract actionable post identifiers from a feed/activity card.

    LinkedIn often omits a direct permalink but still exposes an activity URN
    on the card, its ancestors, or descendant controls.
    """
    # Strategy 1: explicit post-permalink selectors.
    for selector in (
        "a[href*='/feed/update/']",
        "a[href*='/posts/']",
        "a[href*='/activity-']",
        "a[href*='urn%3Ali%3Aactivity']",
        "a[data-tracking-control-name*='update']",
    ):
        try:
            locator = card.locator(selector)
            if await locator.count() == 0:
                continue
            href = await locator.first.get_attribute(
                "href", timeout=_POST_URL_ATTR_TIMEOUT_MS
            )
            identifier = _post_identifier_from_href(href)
            if identifier:
                logger.debug(
                    "Post identifier via strategy=permalink selector=%s href=%s",
                    selector,
                    href,
                )
                return _build_post_identifier(
                    url=identifier["url"],
                    post_urn=identifier["post_urn"],
                    strategy="permalink",
                )
        except Exception:
            continue

    # Strategy 2: scan the card and a bounded ancestor chain for activity URNs.
    try:
        urn = await card.evaluate(
            """(el, maxDepth) => {
                const attrNames = [
                  "data-urn",
                  "data-id",
                  "data-activity-urn",
                  "data-update-id",
                  "data-test-id",
                ];
                const extract = (value) => {
                  if (!value) return null;
                  const match = String(value).match(/urn:li:activity:\\d+/);
                  return match ? match[0] : null;
                };
                let current = el;
                for (let depth = 0; current && depth <= maxDepth; depth += 1) {
                  for (const attrName of attrNames) {
                    const urn = extract(current.getAttribute?.(attrName));
                    if (urn) return urn;
                  }
                  const href = extract(current.getAttribute?.("href"));
                  if (href) return href;
                  current = current.parentElement;
                }
                return null;
            }""",
            _POST_IDENTIFIER_ANCESTOR_DEPTH,
        )
        post_urn = _extract_activity_urn(urn)
        if post_urn:
            logger.debug(
                "Post identifier via strategy=ancestor_urn urn=%s",
                post_urn,
            )
            return _build_post_identifier(
                post_urn=post_urn,
                strategy="ancestor_urn",
            )
    except Exception:
        pass

    # Strategy 3: scan descendant controls/anchors for embedded post URNs.
    try:
        urn = await card.evaluate(
            """(el) => {
                const attrNames = [
                  "href",
                  "data-urn",
                  "data-id",
                  "data-activity-urn",
                  "data-update-id",
                  "data-test-id",
                  "aria-label",
                ];
                const extract = (value) => {
                  if (!value) return null;
                  const match = String(value).match(/urn:li:activity:\\d+/);
                  return match ? match[0] : null;
                };
                const nodes = [el, ...el.querySelectorAll("*")];
                for (const node of nodes.slice(0, 60)) {
                  for (const attrName of attrNames) {
                    const urn = extract(node.getAttribute?.(attrName));
                    if (urn) return urn;
                  }
                }
                return null;
            }"""
        )
        post_urn = _extract_activity_urn(urn)
        if post_urn:
            logger.debug(
                "Post identifier via strategy=descendant_urn urn=%s",
                post_urn,
            )
            return _build_post_identifier(
                post_urn=post_urn,
                strategy="descendant_urn",
            )
    except Exception:
        pass

    # Strategy 2.5: overflow/menu button attributes often carry the parent post URN.
    try:
        menu_buttons = card.locator(
            "button[aria-controls*='urn:li:activity'], button[id*='urn:li:activity']"
        )
        count = min(await menu_buttons.count(), 5)
        for idx in range(count):
            button = menu_buttons.nth(idx)
            for attr_name in ("aria-controls", "id"):
                try:
                    value = await button.get_attribute(
                        attr_name, timeout=_POST_URL_ATTR_TIMEOUT_MS
                    )
                except Exception:
                    continue
                post_urn = _extract_activity_urn(value)
                if not post_urn:
                    continue
                logger.debug(
                    "Post identifier via strategy=menu_button urn=%s",
                    post_urn,
                )
                return _build_post_identifier(
                    post_urn=post_urn,
                    strategy="menu_button",
                )
    except Exception:
        pass

    # Strategy 3.5: full-subtree outerHTML scan — accept only when one unique URN.
    try:
        outer = await card.evaluate("(el) => el.outerHTML || ''")
        unique_urns = set(_ACTIVITY_URN_RE.findall(outer))
        if len(unique_urns) == 1:
            post_urn = next(iter(unique_urns))
            logger.debug("Post identifier via strategy=outer_html urn=%s", post_urn)
            return _build_post_identifier(
                post_urn=post_urn,
                strategy="outer_html",
            )
    except Exception:
        pass

    # Strategy 4: broad anchor scan for any post-like URL.
    try:
        all_links = card.locator("a[href]")
        count = min(await all_links.count(), 15)
        for i in range(count):
            try:
                href = await all_links.nth(i).get_attribute(
                    "href", timeout=_POST_URL_ATTR_TIMEOUT_MS
                )
            except Exception:
                continue
            identifier = _post_identifier_from_href(href)
            if identifier:
                logger.debug(
                    "Post identifier via strategy=broad_anchor href=%s",
                    href,
                )
                return _build_post_identifier(
                    url=identifier["url"],
                    post_urn=identifier["post_urn"],
                    strategy="broad_anchor",
                )
    except Exception:
        pass

    # Strategy 5: scan page hydration scripts and map by card text tokens.
    # LinkedIn's 2025 DOM can omit post URNs from card attributes while
    # still embedding activity identifiers inside inline app state scripts.
    try:
        urn = await card.evaluate(
            """(el, windowChars, cacheKey) => {
                const urnRe = /urn:li:activity:\\d+/g;
                const ignoreLine = /^(?:\\d+[smhdw]|\\d+\\s*(?:m|h|d|w|mo|yr)s?\\s*ago|follow|like|comment|repost|send|promoted|sponsored)$/i;
                const lines = (el.innerText || "")
                  .split(/\\n+/)
                  .map((line) => line.replace(/\\s+/g, " ").trim().toLowerCase())
                  .filter((line) => line.length >= 24 && line.length <= 160 && !ignoreLine.test(line))
                  .slice(0, 3);
                if (lines.length === 0) return null;
                const globalObj = window;
                if (!Array.isArray(globalObj[cacheKey])) {
                  const scripts = Array.from(document.querySelectorAll("script:not([src])"))
                    .map((node) => node.textContent || "")
                    .filter((text) => text.includes("urn:li:activity:"))
                    .slice(0, 40)
                    .map((text) => (text.length > 800000 ? text.slice(0, 800000) : text));
                  globalObj[cacheKey] = scripts;
                }

                const scriptBlobs = globalObj[cacheKey];
                if (!Array.isArray(scriptBlobs) || scriptBlobs.length === 0) return null;

                const hits = new Set();
                const counts = new Map();
                for (const blob of scriptBlobs) {
                  if (!blob || typeof blob !== "string") continue;
                  const lower = blob.toLowerCase();
                  for (const token of lines) {
                    const idx = lower.indexOf(token);
                    if (idx === -1) continue;
                    const start = Math.max(0, idx - windowChars);
                    const end = Math.min(blob.length, idx + token.length + windowChars);
                    const slice = blob.slice(start, end);
                    const urns = slice.match(urnRe);
                    if (!urns) continue;
                    for (const value of urns) {
                      hits.add(value);
                      counts.set(value, (counts.get(value) || 0) + 1);
                    }
                  }
                }

                if (hits.size === 0) return null;
                if (hits.size === 1) return Array.from(hits)[0];
                let best = null, bestCount = 0, tied = false;
                for (const [urn, count] of counts.entries()) {
                  if (count > bestCount) {
                    best = urn;
                    bestCount = count;
                    tied = false;
                  } else if (count === bestCount) {
                    tied = true;
                  }
                }
                return tied ? null : best;
            }""",
            _POST_IDENTIFIER_SCRIPT_WINDOW_CHARS,
            _POST_IDENTIFIER_SCRIPT_CACHE_KEY,
        )
        post_urn = _extract_activity_urn(urn)
        if post_urn:
            logger.debug(
                "Post identifier via strategy=hydration urn=%s",
                post_urn,
            )
            return _build_post_identifier(
                post_urn=post_urn,
                strategy="hydration",
            )
    except Exception:
        pass

    logger.debug("Post identifier: no strategy matched for card")
    return _build_post_identifier()


async def _log_post_identifier_miss(
    card: Any,
    *,
    card_type: str,
    stripped_text: str,
) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return

    try:
        diagnostics = await card.evaluate(
            """(el) => {
                const outer = el.outerHTML || "";
                const urnMatches = outer.match(/urn:li:activity:\\d+/g) || [];
                const distinctUrns = [...new Set(urnMatches)];
                const buttons = Array.from(el.querySelectorAll("button"));
                const menuButtonCount = buttons.filter((button) => {
                  const ariaControls = button.getAttribute("aria-controls") || "";
                  const label = button.getAttribute("aria-label") || "";
                  const buttonId = button.id || "";
                  return (
                    ariaControls.length > 0 ||
                    buttonId.includes("urn:li:activity") ||
                    /control menu|more/i.test(label)
                  );
                }).length;
                const nodes = [el, ...Array.from(el.querySelectorAll("*")).slice(0, 10)];
                const dataAttrNames = new Set();
                for (const node of nodes) {
                  for (const attrName of node.getAttributeNames ? node.getAttributeNames() : []) {
                    if (attrName.startsWith("data-")) dataAttrNames.add(attrName);
                  }
                }
                return {
                  outerhtml_urn_total: urnMatches.length,
                  outerhtml_urn_distinct: distinctUrns.length,
                  anchor_count: el.querySelectorAll("a[href]").length,
                  menu_button_count: menuButtonCount,
                  data_attr_names: [...dataAttrNames].sort(),
                };
            }"""
        )
    except Exception:
        logger.debug(
            "Post identifier miss: card_type=%s snippet=%r diagnostics=unavailable",
            card_type,
            " ".join(stripped_text.split())[:80],
            exc_info=True,
        )
        return

    logger.debug(
        "Post identifier miss: card_type=%s snippet=%r outerhtml_urn_total=%s "
        "outerhtml_urn_distinct=%s anchor_count=%s menu_button_count=%s data_attrs=%s",
        card_type,
        " ".join(stripped_text.split())[:80],
        diagnostics.get("outerhtml_urn_total"),
        diagnostics.get("outerhtml_urn_distinct"),
        diagnostics.get("anchor_count"),
        diagnostics.get("menu_button_count"),
        diagnostics.get("data_attr_names"),
    )


async def _extract_post_url(card: Any) -> str | None:
    """Backward-compatible wrapper returning only the canonical post URL."""
    return (await _extract_post_identifier(card)).get("url")


_CONTROL_MENU_RE = re.compile(
    r"(?:Open control menu for post by|Hide post by)\s+(.+)",
    re.IGNORECASE,
)


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


def _build_post_analytics_item(
    text: str,
    url: str | None = None,
    post_urn: str | None = None,
) -> dict[str, Any]:
    summary = _extract_post_from_text(text)
    reactions = _extract_metric(text, "reactions")
    if reactions is None:
        reactions = _extract_metric(text, "likes")

    return {
        "author": summary["author"] or None,
        "url": url,
        "post_urn": post_urn,
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
    post_urn: str | None = None,
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
        "post_urn": post_urn,
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
        post.get("url") or post.get("post_urn"),
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
                "post_urn": None,
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


def _merge_profile_analytics(
    base: dict[str, int | None],
    update: dict[str, int | None],
) -> dict[str, int | None]:
    merged = dict(base)
    for key, value in update.items():
        if merged.get(key) is None and value is not None:
            merged[key] = value
    return merged


def _profile_analytics_complete(result: dict[str, int | None]) -> bool:
    return all(result.get(key) is not None for key in result)


async def _discover_profile_analytics_links(page: Any) -> dict[str, str]:
    discovered: dict[str, str] = {}
    metric_selectors = {
        "profile_views": "a[href*='profile-views']",
        "search_appearances": "a[href*='search-appearances']",
        "post_impressions": "a[href*='analytics/creator'], a[href*='post-impressions'], a[href*='post_impressions']",
    }

    for key, selector in metric_selectors.items():
        try:
            loc = page.locator(selector).first
            if await loc.count() == 0:
                continue
            href = await loc.get_attribute("href", timeout=500)
        except Exception:
            continue

        if href:
            discovered[key] = urljoin("https://www.linkedin.com", href)

    return discovered


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
