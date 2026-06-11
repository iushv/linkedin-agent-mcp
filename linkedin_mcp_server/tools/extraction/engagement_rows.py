"""Reaction and comment row extraction from LinkedIn post engagement modals."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.tools.extraction.activity_text import (
    _clean_member_name,
    _extract_metric,
    _normalize_reaction_type,
    _parse_named_total,
    _same_linkedin_profile,
)
from linkedin_mcp_server.tools.extraction.post_identity import _absolute_linkedin_url

logger = logging.getLogger(__name__)


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
