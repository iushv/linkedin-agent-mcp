"""
LinkedIn company profile scraping tools.

Uses innerText extraction for resilient company data capture
with configurable section selection.
"""

import asyncio
import logging
import re
from time import monotonic
from typing import Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core import handle_modal_close
from linkedin_mcp_server.drivers.browser import (
    ensure_authenticated,
    get_or_create_browser,
)
from linkedin_mcp_server.error_handler import handle_tool_error
from linkedin_mcp_server.scraping import LinkedInExtractor, parse_company_sections
from linkedin_mcp_server.tools._common import goto_and_check, parse_count

logger = logging.getLogger(__name__)


async def _resolve_company_name(page: Any) -> str | None:
    """Extract the actual company name from the loaded company page.

    Checks the page heading/title to detect what company was actually resolved,
    handling LinkedIn redirects for ambiguous slugs.
    """
    for selector in (
        "h1.org-top-card-summary__title",
        "h1[class*='org-top-card']",
        "h1.top-card-layout__title",
        "h1",
    ):
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                text = await loc.inner_text(timeout=1000)
                if text and text.strip():
                    return " ".join(text.strip().split())
        except Exception:
            continue
    return None


async def _resolve_company_url(page: Any) -> str | None:
    """Get the final URL after any LinkedIn redirects."""
    try:
        return page.url
    except Exception:
        return None


def _parse_company_post_text(text: str) -> dict[str, Any]:
    """Parse a single company post's innerText into structured fields."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    _NOISE = {"like", "comment", "repost", "send", "follow", "share"}
    _TIME_RE = re.compile(r"(\d+\s*(?:m|h|d|w|mo|yr)s?\s*ago|\d+[mhdw])", re.IGNORECASE)
    _COUNT_RE = re.compile(
        r"([\d,.kKmM]+)\s+(reactions?|likes?|comments?|reposts?)", re.IGNORECASE
    )

    time_ago = None
    for line in lines:
        m = _TIME_RE.search(line)
        if m:
            time_ago = m.group(1)
            break

    # Filter out noise/metadata lines for the preview
    content_lines = [
        line
        for line in lines
        if line.lower() not in _NOISE and not _TIME_RE.fullmatch(line)
    ]
    text_preview = "\n".join(content_lines[:8])[:400]

    # Extract engagement counts
    reactions = None
    comments = None
    reposts = None
    for m in _COUNT_RE.finditer(text):
        metric = m.group(2).lower().rstrip("s")
        val = parse_count(m.group(1))
        if metric in ("reaction", "like") and reactions is None:
            reactions = val
        elif metric == "comment" and comments is None:
            comments = val
        elif metric == "repost" and reposts is None:
            reposts = val

    return {
        "text_preview": text_preview,
        "time_ago": time_ago,
        "reactions": reactions,
        "comments": comments,
        "reposts": reposts,
    }


async def _extract_company_post_url(card: Any) -> str | None:
    """Extract the post URL from a company feed card."""
    for selector in (
        "a[href*='/feed/update/']",
        "a[href*='/posts/']",
        "a[href*='urn%3Ali%3Aactivity']",
        "a[data-tracking-control-name*='update']",
    ):
        try:
            loc = card.locator(selector).first
            if await loc.count() > 0:
                href = await loc.get_attribute("href", timeout=300)
                if href:
                    href = href.strip()
                    if href.startswith("/"):
                        href = f"https://www.linkedin.com{href}"
                    return href
        except Exception:
            continue
    return None


def register_company_tools(mcp: FastMCP) -> None:
    """Register all company-related tools with the MCP server."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Company Profile",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_company_profile(
        company_name: str,
        ctx: Context,
        sections: str | None = None,
    ) -> dict[str, Any]:
        """
        Get a specific company's LinkedIn profile.

        Args:
            company_name: LinkedIn company name (e.g., "docker", "anthropic", "microsoft")
            ctx: FastMCP context for progress reporting
            sections: Comma-separated list of extra sections to scrape.
                The about page is always included.
                Available sections: posts, jobs
                Examples: "posts", "posts,jobs"
                Default (None) scrapes only the about page.

        Returns:
            Dict with url, sections (name -> raw text), pages_visited, and sections_requested.
            The LLM should parse the raw text in each section.
        """
        try:
            await ensure_authenticated()

            fields, unknown = parse_company_sections(sections)

            logger.info(
                "Scraping company: %s (sections=%s)",
                company_name,
                sections,
            )

            browser = await get_or_create_browser()
            extractor = LinkedInExtractor(browser.page)

            await ctx.report_progress(
                progress=0, total=100, message="Starting company profile scrape"
            )

            result = await extractor.scrape_company(company_name, fields)

            # B13 fix: detect the actual company that was resolved
            resolved_name = await _resolve_company_name(browser.page)
            if resolved_name:
                result["resolved_name"] = resolved_name
                # Check if the resolved name looks like a match for the slug.
                # Slugs are lowercase, dashes replaced with spaces.
                slug_words = set(company_name.lower().replace("-", " ").split())
                name_words = set(resolved_name.lower().split())
                # Warn if there's very little overlap between slug words
                # and the resolved company name.
                if slug_words and not slug_words & name_words:
                    result["warning"] = (
                        f"LinkedIn resolved slug '{company_name}' to "
                        f"'{resolved_name}'. Verify this is the intended company."
                    )

            if unknown:
                result["unknown_sections"] = unknown

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except Exception as e:
            return handle_tool_error(e, "get_company_profile")

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Company Posts",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_company_posts(
        company_name: str,
        ctx: Context,
        limit: int = 10,
    ) -> dict[str, Any]:
        """
        Get recent posts from a company's LinkedIn feed.

        Args:
            company_name: LinkedIn company name (e.g., "docker", "anthropic", "microsoft")
            ctx: FastMCP context for progress reporting
            limit: Maximum number of posts to return (default 10, max 20).

        Returns:
            Dict with url, posts (structured list), pages_visited, and sections_requested.
        """
        try:
            await ensure_authenticated()

            logger.info("Scraping company posts: %s", company_name)

            browser = await get_or_create_browser()
            page = browser.page
            safe_limit = max(1, min(limit, 20))

            await ctx.report_progress(
                progress=0, total=100, message="Starting company posts scrape"
            )

            url = f"https://www.linkedin.com/company/{company_name}/posts/"
            await goto_and_check(page, url)

            await handle_modal_close(page)

            # Wait for post cards to hydrate
            try:
                await page.wait_for_selector(
                    "article, "
                    "div.feed-shared-update-v2, "
                    "div.occludable-update, "
                    "[data-view-name='feed-full-update']",
                    timeout=8000,
                )
            except Exception:
                logger.warning("Company posts did not hydrate within 8s")

            posts: list[dict[str, Any]] = []
            seen_fingerprints: set[str] = set()
            stagnant_scrolls = 0
            last_total = 0
            processed_idx = 0
            deadline = monotonic() + 40

            # Post card selectors (company feed)
            _CARD_SELECTORS = (
                "main div.feed-shared-update-v2",
                "main div.occludable-update",
                "main article",
                "main [data-urn*='activity']",
            )

            while (
                len(posts) < safe_limit
                and stagnant_scrolls < 3
                and monotonic() < deadline
            ):
                cards = None
                for sel in _CARD_SELECTORS:
                    loc = page.locator(sel)
                    try:
                        if await loc.count() > 0:
                            cards = loc
                            break
                    except Exception:
                        continue
                if cards is None:
                    break

                total_cards = await cards.count()

                for idx in range(processed_idx, total_cards):
                    if len(posts) >= safe_limit:
                        break
                    card = cards.nth(idx)
                    try:
                        text = await card.inner_text(timeout=2500)
                    except Exception:
                        continue

                    if not text or len(text.strip()) < 50:
                        continue

                    stripped = text.strip()
                    fingerprint = stripped[:200]
                    if fingerprint in seen_fingerprints:
                        continue
                    seen_fingerprints.add(fingerprint)

                    post = _parse_company_post_text(stripped)

                    # Try to extract post URL from card
                    post["url"] = await _extract_company_post_url(card)
                    posts.append(post)

                processed_idx = total_cards

                if len(posts) >= safe_limit:
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

            await ctx.report_progress(progress=100, total=100, message="Complete")

            # Also include raw text as fallback
            sections: dict[str, str] = {}
            if not posts:
                extractor = LinkedInExtractor(page)
                raw_text = await extractor.extract_page(url)
                if raw_text:
                    sections["posts"] = raw_text

            return {
                "url": url,
                "posts": posts[:safe_limit],
                "sections": sections,
                "pages_visited": [url],
                "sections_requested": ["posts"],
            }

        except Exception as e:
            return handle_tool_error(e, "get_company_posts")
