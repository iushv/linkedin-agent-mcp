"""
LinkedIn job scraping tools with search and detail extraction.

Uses innerText extraction for resilient job data capture.
"""

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.config import get_config
from linkedin_mcp_server.core import JobCard, is_valid_job_card
from linkedin_mcp_server.core.safety import (
    acquire_browser_lock,
    release_browser_lock,
)
from linkedin_mcp_server.drivers.browser import (
    ensure_authenticated,
    get_or_create_browser,
)
from linkedin_mcp_server.error_handler import handle_tool_error
from linkedin_mcp_server.scraping import LinkedInExtractor
from linkedin_mcp_server.tools._common import goto_and_check

logger = logging.getLogger(__name__)
_JOB_NAVIGATION_TIMEOUT_MS = 30_000
_JOB_CARD_TEXT_TIMEOUT_MS = 300
_JOB_CARD_INNER_TEXT_TIMEOUT_MS = 500

_JOB_CARD_SELECTORS = (
    "li[data-occludable-job-id]",
    "li.jobs-search-results__list-item",
    "div.job-card-container",
    "a[href*='/jobs/view/']",
)
_JOB_TITLE_SELECTORS = (
    ".job-card-list__title",
    ".job-card-container__link",
    ".artdeco-entity-lockup__title a",
    "a[href*='/jobs/view/']",
)
_JOB_COMPANY_SELECTORS = (
    ".artdeco-entity-lockup__subtitle",
    ".job-card-container__company-name",
    ".job-card-container__primary-description",
)
_JOB_LOCATION_SELECTORS = (
    ".job-card-container__metadata-wrapper",
    ".job-card-container__metadata-item",
    ".artdeco-entity-lockup__caption",
)
_JOB_LINK_SELECTORS = (
    "a.job-card-list__title--link",
    "a.job-card-container__link",
    "a[href*='/jobs/view/']",
)
_JOB_NOISE_LINES = {
    "promoted",
    "easy apply",
    "actively hiring",
    "view match",
    "save",
}
_JOB_SEARCH_IGNORE_LINES = _JOB_NOISE_LINES | {
    "jobs",
    "job search",
    "set alert",
    "filters",
    "sort by",
    "all filters",
    "are these results helpful?",
    "jobs you may be interested in",
}
_JOB_POSTING_DATE_RE = re.compile(
    r"^(?:reposted\s+)?(?:just now|\d+\s*(?:s|sec|secs|m|min|mins|h|hr|hrs|d|day|days|w|week|weeks|mo|month|months|yr|year|years))\s+ago$",
    re.IGNORECASE,
)
_JOB_TITLE_NOISE_RE = re.compile(
    r"(are these results helpful|jobs you may be interested in)",
    re.IGNORECASE,
)


def _dedupe_repeated_text(value: str | None) -> str | None:
    """Collapse DOM text that repeats the same token sequence twice."""
    if not value:
        return value

    normalized = " ".join(value.split())
    tokens = normalized.split()

    for split in range(len(tokens) // 2, 0, -1):
        prefix = tokens[:split]
        repeated = tokens[split : split * 2]
        if prefix != repeated:
            continue
        # Exact duplication: keep one copy.
        if split * 2 == len(tokens):
            return " ".join(prefix)
        # Prefix duplication with a suffix-bearing second copy:
        # "Django Developer Django Developer with verification"
        return " ".join(tokens[split:])

    return normalized


def _normalize_job_url(href: str | None) -> str | None:
    """Normalize LinkedIn job links to canonical /jobs/view/<id>/ form."""
    if not href:
        return None

    candidate = href.strip()
    if not candidate:
        return None

    if candidate.startswith("/"):
        candidate = urljoin("https://www.linkedin.com", candidate)
    elif candidate.startswith("https://linkedin.com"):
        candidate = candidate.replace(
            "https://linkedin.com", "https://www.linkedin.com", 1
        )
    elif candidate.startswith("http://linkedin.com"):
        candidate = candidate.replace(
            "http://linkedin.com", "https://www.linkedin.com", 1
        )
    elif candidate.startswith("http://www.linkedin.com"):
        candidate = candidate.replace(
            "http://www.linkedin.com", "https://www.linkedin.com", 1
        )

    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc.endswith("linkedin.com"):
        return None
    if "/jobs/view/" not in parsed.path:
        return None

    return f"https://www.linkedin.com{parsed.path.rstrip('/')}/"


def _extract_job_id(value: str | None) -> str | None:
    """Extract a numeric LinkedIn job id from a URL or DOM attribute."""
    if not value:
        return None

    match = re.search(r"/jobs/view/(\d+)", value)
    if match:
        return match.group(1)

    digits = re.search(r"(\d{6,})", value)
    if digits:
        return digits.group(1)

    return None


def _parse_job_card_text(text: str) -> dict[str, str | None]:
    """Best-effort parser for a job search result card text blob."""
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [
        line
        for line in raw_lines
        if line.lower() not in _JOB_NOISE_LINES
        and "applicant" not in line.lower()
        and not line.lower().endswith("ago")
    ]

    title = lines[0] if lines else None
    company = lines[1] if len(lines) > 1 else None
    location = None
    for line in lines[2:]:
        lowered = line.lower()
        if lowered in _JOB_NOISE_LINES or "applicant" in lowered:
            continue
        location = line
        break

    return {
        "title": title,
        "company": company,
        "location": location,
    }


def _extract_posting_date(line: str) -> str | None:
    candidate = " ".join(line.split())
    if _JOB_POSTING_DATE_RE.match(candidate):
        return candidate
    return None


def _is_verification_title_line(line: str, title: str | None) -> bool:
    if not title:
        return False
    lowered = line.lower()
    return lowered.endswith("with verification") and title.lower() in lowered


def _parse_job_search_results_text(
    text: str,
    *,
    limit: int = 10,
) -> list[dict[str, str | None]]:
    """Parse structured job summaries from raw LinkedIn jobs search innerText."""
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [
        line
        for line in raw_lines
        if line.lower() not in _JOB_SEARCH_IGNORE_LINES
        and "applicant" not in line.lower()
        and "alert" not in line.lower()
    ]

    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        current.append(line)
        if _extract_posting_date(line):
            blocks.append(current)
            current = []

    if current:
        blocks.append(current)

    jobs: list[dict[str, str | None]] = []
    seen: set[str] = set()

    for block in blocks:
        informative = [
            line
            for line in block
            if line.lower() not in _JOB_NOISE_LINES and "applicant" not in line.lower()
        ]
        if len(informative) < 2:
            continue

        posting_date = None
        if informative and _extract_posting_date(informative[-1]):
            posting_date = informative.pop()

        title = _dedupe_repeated_text(informative[0])
        if len(informative) > 1 and _is_verification_title_line(informative[1], title):
            informative.pop(1)
        company = informative[1] if len(informative) > 1 else None
        location = None
        for line in informative[2:]:
            lowered = line.lower()
            if lowered in _JOB_NOISE_LINES or _extract_posting_date(line):
                continue
            location = line
            break

        dedupe_key = title or f"{company}:{location}:{posting_date}"
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        job = _build_job_result(
            title=title,
            company=company,
            location=location,
            posting_date=posting_date,
            job_id=None,
            job_url=None,
        )
        if job is None:
            continue
        jobs.append(job)
        if len(jobs) >= limit:
            break

    return jobs


async def _first_locator_text(scope: Any, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        locator = scope.locator(selector)
        try:
            if await locator.count() == 0:
                continue
            text = await locator.first.inner_text(timeout=_JOB_CARD_TEXT_TIMEOUT_MS)
        except Exception:
            continue
        if text and text.strip():
            return " ".join(text.split())
    return None


async def _first_locator_href(scope: Any, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        locator = scope.locator(selector)
        try:
            if await locator.count() == 0:
                continue
            href = await locator.first.get_attribute("href")
        except Exception:
            continue
        if href:
            return href.strip()
    return None


async def _resolve_job_cards(page: Any) -> Any:
    for selector in _JOB_CARD_SELECTORS:
        locator = page.locator(selector)
        try:
            if await locator.count() > 0:
                return locator
        except Exception:
            continue
    return page.locator("a[href*='/jobs/view/']")


async def _extract_structured_job_results(
    page: Any,
    *,
    limit: int = 10,
) -> list[dict[str, str | None]]:
    """Extract a lightweight structured job list from the current search results page."""
    cards = await _resolve_job_cards(page)
    total = min(await cards.count(), limit)
    jobs: list[dict[str, str | None]] = []
    seen: set[str] = set()

    for idx in range(total):
        card = cards.nth(idx)

        href = await _first_locator_href(card, _JOB_LINK_SELECTORS)
        url = _normalize_job_url(href)

        job_id = None
        try:
            job_id = _extract_job_id(await card.get_attribute("data-occludable-job-id"))
        except Exception:
            pass
        if not job_id:
            job_id = _extract_job_id(url)

        title = await _first_locator_text(card, _JOB_TITLE_SELECTORS)
        company = await _first_locator_text(card, _JOB_COMPANY_SELECTORS)
        location = await _first_locator_text(card, _JOB_LOCATION_SELECTORS)

        if not (title and company and location):
            try:
                parsed = _parse_job_card_text(
                    await card.inner_text(timeout=_JOB_CARD_INNER_TEXT_TIMEOUT_MS)
                )
            except Exception:
                parsed = {"title": None, "company": None, "location": None}
            title = title or parsed["title"]
            company = company or parsed["company"]
            location = location or parsed["location"]

        dedupe_key = job_id or url or title
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        job = _build_job_result(
            title=_dedupe_repeated_text(title),
            company=company,
            location=location,
            job_id=job_id,
            job_url=url,
        )
        if job is None:
            continue
        jobs.append(job)

    return jobs


def _looks_like_noise_job(title: str | None, company: str | None) -> bool:
    if title and _JOB_TITLE_NOISE_RE.search(title):
        return True
    if company and re.fullmatch(r"\d+\+?\s+results", company.strip(), re.IGNORECASE):
        return True
    return False


def _build_job_result(
    *,
    title: str | None,
    company: str | None,
    location: str | None = None,
    posting_date: str | None = None,
    job_id: str | None = None,
    job_url: str | None = None,
) -> dict[str, str | None] | None:
    if not title or not company:
        return None
    if _looks_like_noise_job(title, company):
        return None

    card = JobCard(
        title=title,
        company=company,
        location=location,
        posting_date=posting_date,
        job_id=job_id,
        job_url=job_url,
    )
    if not is_valid_job_card(card):
        return None

    result = {
        "title": card.title,
        "company": card.company,
        "location": card.location,
        "posting_date": card.posting_date,
        "job_id": card.job_id,
        "job_url": card.job_url,
    }
    if card.job_url is not None:
        result["url"] = card.job_url
    else:
        result["url"] = None
    return result


def _finalize_job_results(
    jobs: list[dict[str, str | None]],
    *,
    limit: int = 10,
) -> list[dict[str, str | None]]:
    filtered: list[dict[str, str | None]] = []
    seen: set[str] = set()

    for job in jobs:
        normalized = _build_job_result(
            title=job.get("title"),
            company=job.get("company"),
            location=job.get("location"),
            posting_date=job.get("posting_date"),
            job_id=job.get("job_id"),
            job_url=job.get("job_url") or job.get("url"),
        )
        if normalized is None:
            continue
        dedupe_key = (
            normalized.get("job_id") or normalized.get("job_url") or normalized["title"]
        )
        if not dedupe_key:
            continue
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        filtered.append(normalized)
        if len(filtered) >= limit:
            break

    return filtered


async def _run_job_read(
    action: str,
    fetch_fn: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run a legacy job read tool with shared browser locking."""
    browser_lock_acquired = False
    try:
        await acquire_browser_lock(action)
        browser_lock_acquired = True
        await ensure_authenticated()
        return await fetch_fn()
    except Exception as exc:
        return handle_tool_error(exc, action)
    finally:
        if browser_lock_acquired:
            release_browser_lock()


def _job_navigation_timeout_ms() -> int:
    """Use a longer timeout for LinkedIn jobs pages, which hydrate more slowly."""
    return max(get_config().browser.default_timeout, _JOB_NAVIGATION_TIMEOUT_MS)


def register_job_tools(mcp: FastMCP) -> None:
    """Register all job-related tools with the MCP server."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Job Details",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_job_details(job_id: str, ctx: Context) -> dict[str, Any]:
        """
        Get job details for a specific job posting on LinkedIn.

        Args:
            job_id: LinkedIn job ID (e.g., "4252026496", "3856789012")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, sections (name -> raw text), pages_visited, and sections_requested.
            The LLM should parse the raw text to extract job details.
        """

        async def _fetch() -> dict[str, Any]:
            logger.info("Scraping job: %s", job_id)
            browser = await get_or_create_browser()
            page = browser.page

            async def _navigate(url: str) -> None:
                await goto_and_check(
                    page,
                    url,
                    timeout_ms=_job_navigation_timeout_ms(),
                )

            extractor = LinkedInExtractor(page, navigate_fn=_navigate)

            await ctx.report_progress(
                progress=0, total=100, message="Starting job scrape"
            )

            result = await extractor.scrape_job(job_id)

            await ctx.report_progress(progress=100, total=100, message="Complete")
            return result

        return await _run_job_read("get_job_details", _fetch)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Search Jobs",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def search_jobs(
        keywords: str,
        ctx: Context,
        location: str | None = None,
    ) -> dict[str, Any]:
        """
        Search for jobs on LinkedIn.

        Args:
            keywords: Search keywords (e.g., "software engineer", "data scientist")
            ctx: FastMCP context for progress reporting
            location: Optional location filter (e.g., "San Francisco", "Remote")

        Returns:
            Dict with url, sections (name -> raw text), pages_visited, and sections_requested.
            The LLM should parse the raw text to extract job listings.
        """

        async def _fetch() -> dict[str, Any]:
            logger.info(
                "Searching jobs: keywords='%s', location='%s'",
                keywords,
                location,
            )

            browser = await get_or_create_browser()
            page = browser.page

            async def _navigate(url: str) -> None:
                await goto_and_check(
                    page,
                    url,
                    timeout_ms=_job_navigation_timeout_ms(),
                )

            extractor = LinkedInExtractor(page, navigate_fn=_navigate)

            await ctx.report_progress(
                progress=0, total=100, message="Starting job search"
            )

            result = await extractor.search_jobs(keywords, location)
            raw_search_text = result.get("sections", {}).get("search_results", "")
            try:
                # Hard cap: structured extraction must finish in 15s so the total
                # call stays under Cowork's 60s interactive ceiling.
                structured_jobs = await asyncio.wait_for(
                    _extract_structured_job_results(page),
                    timeout=15.0,
                )
                logger.info(
                    "Structured job DOM extraction returned %d jobs",
                    len(structured_jobs),
                )
            except Exception:
                logger.warning(
                    "Structured job result extraction failed (timeout or error)",
                    exc_info=True,
                )
                structured_jobs = []

            if not structured_jobs and raw_search_text:
                logger.info(
                    "Falling back to raw job text parser (%d chars)",
                    len(raw_search_text),
                )
                structured_jobs = _parse_job_search_results_text(raw_search_text)
                logger.info(
                    "Raw job text parser returned %d jobs",
                    len(structured_jobs),
                )

            result["jobs"] = _finalize_job_results(structured_jobs)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        return await _run_job_read("search_jobs", _fetch)
