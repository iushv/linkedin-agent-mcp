"""Post identifier (URL / activity URN) resolution from feed and activity cards.

LinkedIn frequently omits visible permalinks; these helpers recover an
actionable identifier through layered strategies, from explicit permalink
anchors down to hydration-script scanning.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

logger = logging.getLogger(__name__)


_POST_URL_ATTR_TIMEOUT_MS = 200


_POST_IDENTIFIER_ANCESTOR_DEPTH = 6


_POST_IDENTIFIER_SCRIPT_WINDOW_CHARS = 4000


_POST_IDENTIFIER_SCRIPT_CACHE_KEY = "__linkedinMcpHydrationScriptsV1"


_ACTIVITY_URN_RE = re.compile(r"urn:li:activity:\d+")


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
