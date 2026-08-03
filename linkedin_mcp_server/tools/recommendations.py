"""Job recommendation tools."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.pagination import build_paginated_response, decode_cursor
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools._common import goto_and_check, run_read_tool
from linkedin_mcp_server.tools.job import (
    _build_job_result,
    _extract_job_id,
    _first_locator_href,
    _first_locator_text,
    _normalize_job_url,
)

logger = logging.getLogger(__name__)

_RECOMMENDATION_TITLE_SELECTORS = (
    ".job-card-list__title",
    ".job-card-container__link",
    "a[href*='/jobs/view/']",
)
_RECOMMENDATION_COMPANY_SELECTORS = (
    ".artdeco-entity-lockup__subtitle",
    ".job-card-container__company-name",
)
_RECOMMENDATION_LOCATION_SELECTORS = (
    ".job-card-container__metadata-item",
    ".job-card-container__metadata-wrapper",
)
_RECOMMENDATION_LINK_SELECTORS = ("a[href*='/jobs/view/']",)
_RECOMMENDATION_NOISE = {
    "top job picks for you",
    "based on your profile, preferences, and activity like applies, searches, and saves",
    "promoted",
    "easy apply",
    "show all",
    "load more",
    "top job picks",
}
_LOCATION_HINT_RE = re.compile(
    r"(remote|hybrid|on-site|onsite|india|singapore|delhi|gurugram|bangalore|mumbai|new york|london|\()",
    re.IGNORECASE,
)
_VERIFIED_JOB_RE = re.compile(r"\s+\(verified job\)\s*$", re.IGNORECASE)
_ANCILLARY_JOB_LINE_RE = re.compile(
    r"(actively reviewing applicants|company alumni works here|promoted|easy apply|applied|resume matched)",
    re.IGNORECASE,
)
# Patterns that indicate a non-job card (profile / people-you-may-know / company spotlight)
_NON_JOB_CARD_RE = re.compile(
    r"(\bconnections?\b|\bfollowers?\b|\bpeople\s+you\s+may\s+know\b"
    r"|\bpeople\s+also\s+viewed\b|\bwho\s+viewed\b|\bmutual\b"
    r"|\bcompany\s+spotlight\b|\bschool\b.*\balumni\b)",
    re.IGNORECASE,
)


def _clean_recommendation_line(line: str) -> str:
    line = " ".join(line.split()).strip()
    if not line or line in {"•", "·"}:
        return ""
    return line


def _normalize_recommendation_title(title: str) -> str:
    return _VERIFIED_JOB_RE.sub("", title).strip()


def _is_non_job_line(line: str) -> bool:
    """Return True if a line looks like it belongs to a profile/people card."""
    return bool(_NON_JOB_CARD_RE.search(line))


def _parse_job_recommendations_text(
    text: str,
    *,
    limit: int = 10,
) -> list[dict[str, str | None]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    try:
        start = next(
            idx
            for idx, line in enumerate(lines)
            if line.lower().startswith("top job picks")
            or line.lower().startswith("recommended for you")
        )
        lines = lines[start + 1 :]
    except StopIteration:
        pass

    filtered: list[str] = []
    for raw_line in lines:
        line = _clean_recommendation_line(raw_line)
        if not line:
            continue
        if line.lower() in _RECOMMENDATION_NOISE:
            continue
        # Skip lines that look like profile/people cards
        if _is_non_job_line(line):
            continue
        filtered.append(line)

    jobs: list[dict[str, str | None]] = []
    seen: set[str] = set()
    idx = 0
    while idx < len(filtered) and len(jobs) < limit:
        while idx < len(filtered) and (
            filtered[idx].lower() in _RECOMMENDATION_NOISE
            or _ANCILLARY_JOB_LINE_RE.search(filtered[idx])
        ):
            idx += 1
        if idx >= len(filtered):
            break

        title = filtered[idx]
        if len(title) < 3:
            idx += 1
            continue

        next_idx = idx + 1
        normalized_title = _normalize_recommendation_title(title)
        if next_idx < len(filtered):
            duplicate_title = _normalize_recommendation_title(filtered[next_idx])
            if duplicate_title and duplicate_title == normalized_title:
                title = normalized_title or title
                next_idx += 1
        else:
            title = normalized_title or title

        title = normalized_title or title
        if not title:
            idx += 1
            continue

        while next_idx < len(filtered) and _ANCILLARY_JOB_LINE_RE.search(
            filtered[next_idx]
        ):
            next_idx += 1

        if next_idx >= len(filtered):
            break

        company = filtered[next_idx]
        next_idx += 1
        location = None
        while next_idx < len(filtered):
            candidate = filtered[next_idx]
            if _LOCATION_HINT_RE.search(candidate):
                location = candidate
                next_idx += 1
                break
            if (
                candidate.lower() in _RECOMMENDATION_NOISE
                or _ANCILLARY_JOB_LINE_RE.search(candidate)
            ):
                next_idx += 1
                continue
            break

        while next_idx < len(filtered) and (
            filtered[next_idx].lower() in _RECOMMENDATION_NOISE
            or _ANCILLARY_JOB_LINE_RE.search(filtered[next_idx])
        ):
            next_idx += 1

        job = _build_job_result(
            title=title,
            company=company,
            location=location,
            job_id=None,
            job_url=None,
        )
        idx = next_idx if next_idx > idx else idx + 1
        if job is None:
            continue
        dedupe_key = f"{job['title']}::{job['company']}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        jobs.append(job)

    return jobs


def register_recommendation_tools(mcp: FastMCP) -> None:
    """Register personalized job recommendation tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Job Recommendations",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_job_recommendations(
        limit: int = 10,
        page: int | None = None,
        next_cursor: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return LinkedIn's personalized job recommendations feed."""

        async def _fetch() -> dict[str, Any]:
            safe_limit = max(1, min(limit, 25))
            current_page = decode_cursor(next_cursor, page)
            browser = await get_or_create_browser()
            page_obj = browser.page
            url = "https://www.linkedin.com/jobs/"
            if current_page > 1:
                url = f"{url}?page={current_page}"

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading job recommendations"
                )

            await goto_and_check(page_obj, url)
            jobs: list[dict[str, str | None]] = []

            # --- 2025+ DOM: job cards are <a> links with currentJobId param ---
            try:
                job_links = page_obj.locator("a[href*='currentJobId']")
                link_count = await job_links.count()
                logger.debug(
                    "Found %d job recommendation links via currentJobId",
                    link_count,
                )
                for idx in range(link_count):
                    if len(jobs) >= safe_limit:
                        break
                    link = job_links.nth(idx)
                    try:
                        href = await link.get_attribute("href", timeout=500)
                    except Exception:
                        continue
                    if not href:
                        continue

                    # Extract job ID from URL param currentJobId
                    parsed = urlparse(href)
                    params = parse_qs(parsed.query)
                    job_id_list = params.get("currentJobId", [])
                    job_id = job_id_list[0] if job_id_list else None
                    if not job_id:
                        continue

                    job_url = f"https://www.linkedin.com/jobs/view/{job_id}/"

                    # Extract title, company, location from child elements
                    title = None
                    company = None
                    location_value = None
                    try:
                        text = await link.inner_text(timeout=800)
                        if text:
                            lines = [
                                ln.strip() for ln in text.splitlines() if ln.strip()
                            ]
                            # Filter noise lines
                            clean = [
                                ln
                                for ln in lines
                                if ln.lower() not in _RECOMMENDATION_NOISE
                                and not _ANCILLARY_JOB_LINE_RE.search(ln)
                            ]
                            # Title is first clean line, company second
                            if clean:
                                title = _normalize_recommendation_title(clean[0])
                                # Skip duplicate title line
                                ci = 1
                                if (
                                    ci < len(clean)
                                    and _normalize_recommendation_title(clean[ci])
                                    == title
                                ):
                                    ci += 1
                                if ci < len(clean):
                                    company = clean[ci]
                                    ci += 1
                                if ci < len(clean):
                                    location_value = clean[ci]
                    except Exception:
                        pass

                    # Also try dismiss button for title confirmation
                    if not title:
                        try:
                            dismiss = link.locator(
                                "button[aria-label*='Dismiss' i]"
                            ).first
                            if await dismiss.count() > 0:
                                dismiss_label = await dismiss.get_attribute(
                                    "aria-label", timeout=300
                                )
                                if dismiss_label:
                                    # "Dismiss AI Engineer - 3 job"
                                    m = re.match(
                                        r"Dismiss\s+(.+?)\s+job",
                                        dismiss_label,
                                        re.IGNORECASE,
                                    )
                                    if m:
                                        title = m.group(1).strip()
                        except Exception:
                            pass

                    job = _build_job_result(
                        title=title,
                        company=company,
                        location=location_value,
                        job_id=job_id,
                        job_url=job_url,
                    )
                    if job is None:
                        continue
                    jobs.append(job)
            except Exception:
                logger.debug(
                    "2025+ job recommendation extraction failed",
                    exc_info=True,
                )

            # --- Legacy DOM fallback: CSS class-based selectors ---
            if not jobs:
                try:
                    rows = await SELECTORS["jobs"]["recommendation_cards"].resolve(
                        page_obj
                    )
                    for idx in range(await rows.count()):
                        row = rows.nth(idx)
                        href = await _first_locator_href(
                            row, _RECOMMENDATION_LINK_SELECTORS
                        )
                        job_url = _normalize_job_url(href)
                        if not job_url:
                            continue
                        title = await _first_locator_text(
                            row, _RECOMMENDATION_TITLE_SELECTORS
                        )
                        company = await _first_locator_text(
                            row, _RECOMMENDATION_COMPANY_SELECTORS
                        )
                        location_value = await _first_locator_text(
                            row, _RECOMMENDATION_LOCATION_SELECTORS
                        )
                        job = _build_job_result(
                            title=title,
                            company=company,
                            location=location_value,
                            job_id=_extract_job_id(job_url),
                            job_url=job_url,
                        )
                        if job is not None:
                            jobs.append(job)
                        if len(jobs) >= safe_limit:
                            break
                except Exception:
                    pass

            # --- Text fallback ---
            if not jobs:
                body_text = await page_obj.locator("body").inner_text(timeout=2000)
                jobs = _parse_job_recommendations_text(body_text, limit=safe_limit)

            response = build_paginated_response(
                results=jobs,
                page=current_page,
                limit=safe_limit,
                total=None,
            )
            payload = response.to_dict()
            payload["jobs"] = payload.pop("results")

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Recommendations loaded"
                )

            return payload

        return await run_read_tool("get_job_recommendations", _fetch)
