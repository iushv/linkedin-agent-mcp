"""Profile editing tools for job-search profile optimization."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.exceptions import InteractionError
from linkedin_mcp_server.core.interactions import (
    click_element,
    type_text,
    wait_for_modal,
)
from linkedin_mcp_server.core.responses import write_dry_run
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.core.utils import detect_rate_limit_post_action
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools._common import goto_and_check, run_write_tool

logger = logging.getLogger(__name__)


async def _read_current_headline(page: Any) -> str | None:
    try:
        locator = await SELECTORS["profile"]["headline_text"].find(page)
        text = await locator.inner_text(timeout=1000)
        return " ".join(text.split()) if text else None
    except Exception:
        return None


async def _read_open_to_work_enabled(page: Any) -> bool:
    # Check specific DOM selectors for the Open To Work badge/section
    _OTW_SELECTORS = (
        "#open-to-work-modal-header",
        "section[class*='open-to-work']",
        ".pv-open-to-work-section",
        "div[class*='open-to-work']",
        "button[aria-label*='Open to work' i]",
        "[data-view-name*='open-to-work']",
        # Profile photo overlay badge
        "img[alt*='Open to work' i]",
        "span.pv-member-badge--open-to-work",
        ".pv-top-card--open-to-work",
    )
    for sel in _OTW_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                return True
        except Exception:
            continue

    # Fallback: check body text for the phrase in context
    try:
        # Read only the top card / profile header to avoid false positives
        for scope_sel in (
            "section.pv-top-card",
            ".pv-top-card",
            "main section:first-of-type",
        ):
            try:
                scope = page.locator(scope_sel).first
                if await scope.count() > 0:
                    scope_text = await scope.inner_text(timeout=1500)
                    lowered = scope_text.lower()
                    if "open to work" in lowered or "show recruiters" in lowered:
                        return True
            except Exception:
                continue
    except Exception:
        pass

    return False


async def _read_featured_skills(page: Any) -> list[str]:
    # Try multiple selectors — LinkedIn changes class names frequently
    _SKILL_SELECTORS = (
        "span.pv-skill-category-entity__name-text",
        # Current LinkedIn skills detail page selectors
        "li.pvs-list__paged-list-item .t-bold span",
        "li.pvs-list__paged-list-item span[aria-hidden='true']",
        "li.pvs-list__paged-list-item .mr1.t-bold span",
        "div.pvs-list__outer-container li span.t-bold span",
        # Broader fallbacks
        "[class*='skill-category-entity'] span",
        ".pvs-entity--padded .t-bold span",
    )
    for selector in _SKILL_SELECTORS:
        try:
            rows = page.locator(selector)
            count = min(await rows.count(), 10)
            if count == 0:
                continue
        except Exception:
            continue

        skills: list[str] = []
        for idx in range(count):
            try:
                text = await rows.nth(idx).inner_text(timeout=500)
            except Exception:
                continue
            value = " ".join(text.split())
            if value and len(value) > 1:
                skills.append(value)

        if skills:
            return skills[:10]

    return []


async def _open_intro_editor(page: Any) -> None:
    await goto_and_check(page, "https://www.linkedin.com/in/me/")
    await click_element(page, SELECTORS["profile"]["intro_edit"])
    await wait_for_modal(page)


async def _preview_headline_change(page: Any, headline: str) -> dict[str, Any]:
    await goto_and_check(page, "https://www.linkedin.com/in/me/")
    current = await _read_current_headline(page)
    return {
        "previous_headline": current,
        "new_headline": headline,
    }


async def _preview_open_to_work(page: Any, payload: dict[str, Any]) -> dict[str, Any]:
    await goto_and_check(page, "https://www.linkedin.com/in/me/")
    return {
        "currently_enabled": await _read_open_to_work_enabled(page),
        **payload,
    }


def _dry_run_with_data(
    action: str, description: str, data: dict[str, Any]
) -> dict[str, Any]:
    return write_dry_run(action, description, data=data)


def register_profile_tools(mcp: FastMCP) -> None:
    """Register profile editing tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Update Profile Headline",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def update_profile_headline(
        headline: str,
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Update the logged-in user's LinkedIn headline."""
        if not headline.strip():
            raise ValueError("headline must not be empty")

        browser = await get_or_create_browser()
        page = browser.page

        if dry_run or not confirm:
            preview = await _preview_headline_change(page, headline)
            return _dry_run_with_data(
                "update_profile_headline",
                "Preview profile headline update.",
                preview,
            )

        async def _execute() -> dict[str, Any]:
            previous = await _preview_headline_change(page, headline)
            await _open_intro_editor(page)

            headline_input = await SELECTORS["profile"]["headline_input"].find(page)
            await headline_input.fill("")
            await type_text(page, SELECTORS["profile"]["headline_input"], headline)
            await click_element(page, SELECTORS["profile"]["modal_save"])
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Headline updated"
                )

            return {
                "message": "Profile headline updated successfully.",
                "previous_headline": previous.get("previous_headline"),
                "new_headline": headline,
                "resource_url": "https://www.linkedin.com/in/me/",
            }

        return await run_write_tool(
            action="update_profile_headline",
            params={"headline": headline},
            dry_run=False,
            confirm=confirm,
            description="Update the LinkedIn profile headline.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Set Open To Work",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def set_open_to_work(
        enabled: bool,
        visibility: str,
        job_titles: list[str],
        job_types: list[str],
        locations: list[str],
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Enable or disable the Open To Work profile signal."""
        browser = await get_or_create_browser()
        page = browser.page
        preview_payload = {
            "enabled": enabled,
            "visibility": visibility,
            "job_titles": job_titles,
            "job_types": job_types,
            "locations": locations,
        }

        if dry_run or not confirm:
            preview = await _preview_open_to_work(page, preview_payload)
            return _dry_run_with_data(
                "set_open_to_work",
                "Preview Open To Work changes.",
                preview,
            )

        async def _execute() -> dict[str, Any]:
            await goto_and_check(page, "https://www.linkedin.com/in/me/")
            await click_element(page, SELECTORS["profile"]["open_to_work_button"])
            await wait_for_modal(page)

            if enabled:
                if job_titles:
                    title_input = await SELECTORS["profile"][
                        "open_to_work_job_title"
                    ].find(page)
                    await title_input.fill(job_titles[0])
                if locations:
                    location_input = await SELECTORS["profile"][
                        "open_to_work_location"
                    ].find(page)
                    await location_input.fill(locations[0])

                if visibility.strip().lower() == "recruiters_only":
                    await click_element(
                        page, SELECTORS["profile"]["open_to_work_recruiters_only"]
                    )
                else:
                    await click_element(
                        page, SELECTORS["profile"]["open_to_work_public"]
                    )
            else:
                await click_element(page, SELECTORS["profile"]["open_to_work_remove"])

            await click_element(page, SELECTORS["profile"]["modal_save"])
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Open To Work updated"
                )

            return {
                "message": "Open To Work settings updated successfully.",
                "resource_url": "https://www.linkedin.com/in/me/",
                "enabled": enabled,
                "visibility": visibility,
                "titles_set": len(job_titles),
            }

        return await run_write_tool(
            action="set_open_to_work",
            params=preview_payload,
            dry_run=False,
            confirm=confirm,
            description="Update LinkedIn Open To Work settings.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Add Profile Skills",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def add_profile_skills(
        skills: list[str],
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Add new skills to the logged-in user's profile."""
        requested_skills = [skill.strip() for skill in skills if skill.strip()]
        if not requested_skills:
            raise ValueError("skills must contain at least one non-empty value")

        if dry_run or not confirm:
            return _dry_run_with_data(
                "add_profile_skills",
                "Preview adding profile skills.",
                {"skills_to_add": requested_skills},
            )

        async def _execute() -> dict[str, Any]:
            browser = await get_or_create_browser()
            page = browser.page
            await goto_and_check(page, "https://www.linkedin.com/in/me/details/skills/")
            await click_element(page, SELECTORS["profile"]["skills_add_button"])
            await wait_for_modal(page)

            skill_input = await SELECTORS["profile"]["skill_input"].find(page)
            for skill in requested_skills:
                await skill_input.fill("")
                await skill_input.fill(skill)

            await click_element(page, SELECTORS["profile"]["modal_save"])
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Skills added"
                )

            return {
                "message": "Profile skills updated successfully.",
                "resource_url": "https://www.linkedin.com/in/me/details/skills/",
                "added_skills": requested_skills,
            }

        return await run_write_tool(
            action="add_profile_skills",
            params={"skills": requested_skills},
            dry_run=False,
            confirm=confirm,
            description="Add skills to the LinkedIn profile.",
            execute_fn=_execute,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Set Featured Skills",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def set_featured_skills(
        featured_skills: list[str],
        ctx: Context | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Best-effort featured-skills ordering tool."""
        requested = [skill.strip() for skill in featured_skills if skill.strip()]
        if not requested:
            raise ValueError(
                "featured_skills must contain at least one non-empty value"
            )

        browser = await get_or_create_browser()
        page = browser.page
        await goto_and_check(page, "https://www.linkedin.com/in/me/details/skills/")
        current_order = await _read_featured_skills(page)

        if dry_run or not confirm:
            return _dry_run_with_data(
                "set_featured_skills",
                "Preview featured skills reorder.",
                {
                    "current_order": current_order,
                    "requested_order": requested,
                    "experimental": True,
                },
            )

        async def _execute() -> dict[str, Any]:
            await click_element(page, SELECTORS["profile"]["featured_skills_button"])
            await wait_for_modal(page)

            missing = [
                skill
                for skill in requested
                if await page.get_by_text(skill).count() == 0
            ]
            if missing:
                raise InteractionError(
                    "Featured skills reorder could not be completed.",
                    action="set_featured_skills",
                    context={
                        "current_order": current_order,
                        "missing_skills": missing,
                    },
                )

            await click_element(page, SELECTORS["profile"]["modal_save"])
            await detect_rate_limit_post_action(page)

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Featured skills updated"
                )

            return {
                "message": "Featured skills updated successfully.",
                "resource_url": "https://www.linkedin.com/in/me/details/skills/",
                "current_order": current_order,
                "requested_order": requested,
            }

        return await run_write_tool(
            action="set_featured_skills",
            params={"featured_skills": requested, "experimental": True},
            dry_run=False,
            confirm=confirm,
            description="Reorder featured skills on the LinkedIn profile.",
            execute_fn=_execute,
        )
