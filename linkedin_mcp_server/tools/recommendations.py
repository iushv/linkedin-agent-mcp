"""Job recommendation tools."""

from __future__ import annotations

import re
from typing import Any

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


def _clean_recommendation_line(line: str) -> str:
    line = " ".join(line.split()).strip()
    if not line or line in {"•", "·"}:
        return ""
    return line


def _normalize_recommendation_title(title: str) -> str:
    return _VERIFIED_JOB_RE.sub("", title).strip()


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
            try:
                rows = await SELECTORS["jobs"]["recommendation_cards"].resolve(page_obj)
                for idx in range(await rows.count()):
                    row = rows.nth(idx)
                    title = await _first_locator_text(
                        row, _RECOMMENDATION_TITLE_SELECTORS
                    )
                    company = await _first_locator_text(
                        row, _RECOMMENDATION_COMPANY_SELECTORS
                    )
                    location_value = await _first_locator_text(
                        row, _RECOMMENDATION_LOCATION_SELECTORS
                    )
                    href = await _first_locator_href(
                        row, _RECOMMENDATION_LINK_SELECTORS
                    )
                    job_url = _normalize_job_url(href)
                    job = _build_job_result(
                        title=title,
                        company=company,
                        location=location_value,
                        job_id=_extract_job_id(job_url),
                        job_url=job_url,
                    )
                    if job is None:
                        continue
                    jobs.append(job)
                    if len(jobs) >= safe_limit:
                        break
            except Exception:
                jobs = []

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
