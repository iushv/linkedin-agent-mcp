"""Profile analytics extraction (views, search appearances, impressions)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from linkedin_mcp_server.tools._common import parse_count
from linkedin_mcp_server.tools.extraction.activity_text import (
    _NUM_IN_TEXT_RE,
    _extract_metric,
)

logger = logging.getLogger(__name__)


def _extract_profile_analytics_from_text(text: str) -> dict[str, int | None]:
    return {
        "profile_views": (
            _extract_metric(text, "profile viewers")
            or _extract_metric(text, "profile views")
        ),
        "search_appearances": _extract_metric(text, "search appearances"),
        "post_impressions": _extract_metric(text, "post impressions"),
    }


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
