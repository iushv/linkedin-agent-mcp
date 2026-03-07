"""Saved jobs queue tools."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.interactions import click_element
from linkedin_mcp_server.core.pagination import build_paginated_response, decode_cursor
from linkedin_mcp_server.core.schemas import JobCard, is_valid_job_card
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core.utils import detect_rate_limit_post_action
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools._common import goto_and_check, run_read_tool, run_write_tool

logger = logging.getLogger(__name__)

_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")


def _normalize_job_url(job_url: str) -> str:
    candidate = job_url.strip()
    if not candidate:
        raise ValueError("job_url must not be empty")
    if candidate.startswith("/"):
        candidate = f"https://www.linkedin.com{candidate}"
    if not candidate.startswith("http"):
        raise ValueError("job_url must be an absolute LinkedIn job URL")
    return candidate.rstrip("/")


def _extract_job_id(job_url: str | None) -> str | None:
    if not job_url:
        return None
    match = _JOB_ID_RE.search(job_url)
    if not match:
        return None
    return match.group(1)


def _parse_saved_job_card_text(
    text: str,
    *,
    job_url: str | None,
) -> JobCard | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    title = lines[0]
    company = lines[1]
    location = lines[2] if len(lines) > 2 else None
    posting_date = None
    for line in lines[2:]:
        if "ago" in line.lower() or re.match(r"\d{4}-\d{2}-\d{2}$", line):
            posting_date = line
            break

    card = JobCard(
        title=title,
        company=company,
        location=location,
        posting_date=posting_date,
        job_id=_extract_job_id(job_url),
        job_url=job_url,
    )
    return card if is_valid_job_card(card) else None


async def _extract_job_page_summary(page: Any, normalized_url: str) -> dict[str, str | None]:
    try:
        body_text = await page.locator("body").inner_text(timeout=1500)
    except Exception:
        body_text = ""

    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    title = lines[0] if lines else None
    company = lines[1] if len(lines) > 1 else None
    location = lines[2] if len(lines) > 2 else None
    return {
        "job_id": _extract_job_id(normalized_url),
        "job_url": normalized_url,
        "title": title,
        "company": company,
        "location": location,
    }


def register_saved_job_tools(mcp: FastMCP) -> None:
    """Register saved-job queue tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Save Job",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def save_job(
        job_url: str,
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Save a LinkedIn job posting for later review."""

        async def _execute() -> dict[str, Any]:
            normalized_url = _normalize_job_url(job_url)
            browser = await get_or_create_browser()
            page = browser.page

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Opening job posting"
                )

            await goto_and_check(page, normalized_url)
            already_saved = False
            try:
                saved_button = await SELECTORS["jobs"]["unsave_button"].find(page)
                already_saved = await saved_button.count() > 0
            except Exception:
                already_saved = False

            if not already_saved:
                await click_element(page, SELECTORS["jobs"]["save_button"])
                await detect_rate_limit_post_action(page)

            summary = await _extract_job_page_summary(page, normalized_url)
            summary["status"] = "saved"
            summary["message"] = (
                "Job already saved." if already_saved else "Job saved successfully."
            )

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Job saved"
                )

            return summary

        return await run_write_tool(
            action="save_job",
            params={"job_url": job_url},
            dry_run=dry_run,
            confirm=confirm,
            description=f"Save LinkedIn job {job_url}.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Saved Jobs",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_saved_jobs(
        limit: int = 10,
        page: int | None = None,
        next_cursor: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return the current user's saved jobs list."""

        async def _fetch() -> dict[str, Any]:
            safe_limit = max(1, min(limit, 25))
            current_page = decode_cursor(next_cursor, page)
            browser = await get_or_create_browser()
            page_obj = browser.page
            url = "https://www.linkedin.com/my-items/saved-jobs/"
            if current_page > 1:
                url = f"{url}?page={current_page}"

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Loading saved jobs"
                )

            await goto_and_check(page_obj, url)
            rows = await SELECTORS["jobs"]["saved_job_cards"].resolve(page_obj)
            jobs: list[JobCard] = []

            for idx in range(await rows.count()):
                row = rows.nth(idx)
                anchor = row.locator("a[href*='/jobs/view/']").first
                href = await anchor.get_attribute("href") if await anchor.count() > 0 else None
                if href and href.startswith("/"):
                    href = f"https://www.linkedin.com{href}"
                text = await row.inner_text(timeout=1000)
                card = _parse_saved_job_card_text(text, job_url=href)
                if card is None:
                    continue
                jobs.append(card)
                if len(jobs) >= safe_limit:
                    break

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
                    progress=100, total=100, message="Saved jobs loaded"
                )

            return payload

        return await run_read_tool("get_saved_jobs", _fetch)
