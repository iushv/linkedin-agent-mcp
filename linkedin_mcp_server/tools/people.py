"""People search tools for referral and warm-intro workflows."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from time import perf_counter
from typing import Any
from urllib.parse import quote_plus

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from linkedin_mcp_server.core.pagination import (
    build_paginated_response,
    decode_cursor,
)
from linkedin_mcp_server.core.resolver import (
    ResolvedCompany,
    ResolvedGeo,
    resolve_company,
    resolve_geo,
)
from linkedin_mcp_server.core.schemas import PersonCard, is_valid_person_card
from linkedin_mcp_server.core.selectors import SELECTORS
from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools._common import goto_and_check, run_read_tool

logger = logging.getLogger(__name__)

_PAGE_BUDGET_SECONDS = 45.0
_RESOLUTION_BUDGET_SECONDS = 16.0
_PAGINATION_DELAY_RANGE_SECONDS = (2.0, 5.0)
_LOCATION_HINT_RE = re.compile(
    r"(remote|hybrid|on-site|onsite|singapore|india|united states|dubai|london|new york|san francisco)",
    re.IGNORECASE,
)
_CONNECTION_DEGREE_RE = re.compile(r"\b(1st|2nd|3rd)\b", re.IGNORECASE)
_SHARED_CONNECTIONS_RE = re.compile(
    r"(\d+)\s+shared connections?",
    re.IGNORECASE,
)
_RESULT_COUNT_RE = re.compile(r"([\d,]+)\+?\s+results", re.IGNORECASE)
_VALID_MATCH_MODES = {"strict", "auto", "broad"}


def _normalize_person_profile_url(href: str | None) -> str | None:
    """Normalize LinkedIn person profile links to absolute URLs."""
    if not href:
        return None

    candidate = href.strip()
    if not candidate:
        return None

    if candidate.startswith("/"):
        candidate = f"https://www.linkedin.com{candidate}"
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

    if "linkedin.com/in/" not in candidate and "linkedin.com/pub/" not in candidate:
        return None
    return candidate


def _extract_connection_degree(text: str) -> str | None:
    match = _CONNECTION_DEGREE_RE.search(text)
    if not match:
        return None
    return match.group(1)


def _extract_shared_connections(text: str) -> int | None:
    match = _SHARED_CONNECTIONS_RE.search(text)
    if not match:
        return None
    return int(match.group(1))


def _extract_current_company(headline: str | None) -> str | None:
    if not headline:
        return None

    match = re.search(r"\bat\s+([^|,·]+)", headline, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()


def _looks_like_location(line: str) -> bool:
    if not line:
        return False
    lowered = line.lower()
    if "shared connection" in lowered or _CONNECTION_DEGREE_RE.search(line):
        return False
    if "," in line or _LOCATION_HINT_RE.search(line):
        return True
    return False


def _extract_total_count(text: str) -> int | None:
    match = _RESULT_COUNT_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _normalize_match_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _extract_prefixed_company(line: str, prefix: str) -> str | None:
    if not line.lower().startswith(prefix.lower()):
        return None
    return line.split(":", 1)[1].strip() or None


def _matches_company_name(candidate: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    normalized_expected = _normalize_match_text(expected)
    normalized_candidate = _normalize_match_text(candidate)
    if not normalized_expected or not normalized_candidate:
        return False
    return normalized_expected in normalized_candidate


def _matches_location(candidate: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    normalized_expected = _normalize_match_text(expected)
    normalized_candidate = _normalize_match_text(candidate)
    if not normalized_expected or not normalized_candidate:
        return False
    return normalized_expected in normalized_candidate


def _matches_title_keyword(card: PersonCard, raw_text: str, title_keyword: str | None) -> bool:
    if not title_keyword:
        return True
    normalized_expected = _normalize_match_text(title_keyword)
    searchable = " ".join(
        part
        for part in [card.headline, raw_text]
        if part
    )
    return normalized_expected in _normalize_match_text(searchable)


def _parse_person_card_text(
    text: str,
    *,
    profile_url: str | None,
    default_current_company: str | None = None,
    default_past_company: str | None = None,
) -> PersonCard | None:
    """Parse a LinkedIn people result card into the shared person schema."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not profile_url:
        return None

    name = re.sub(
        r"\s*[•·]\s*(1st|2nd|3rd\+?)\s*$",
        "",
        lines[0],
        flags=re.IGNORECASE,
    )
    headline = None
    location = None
    connection_degree = None
    shared_connections = None
    explicit_current_company = None
    explicit_past_companies: list[str] = []
    remaining = lines[1:]

    if remaining and not _CONNECTION_DEGREE_RE.search(remaining[0]):
        headline = remaining[0]
        remaining = remaining[1:]

    for line in remaining:
        current_value = _extract_prefixed_company(line, "Current")
        if current_value:
            explicit_current_company = current_value
            continue
        past_value = _extract_prefixed_company(line, "Past")
        if past_value:
            explicit_past_companies.append(past_value)
            continue
        if shared_connections is None:
            shared_connections = _extract_shared_connections(line)
            if shared_connections is not None:
                continue
        if connection_degree is None:
            connection_degree = _extract_connection_degree(line)
            if connection_degree is not None:
                continue
        if location is None and _looks_like_location(line):
            location = line

    current_company = (
        explicit_current_company
        or _extract_current_company(headline)
        or default_current_company
    )
    past_companies = explicit_past_companies or None
    if (
        default_past_company
        and default_past_company.lower() in text.lower()
        and default_past_company not in (past_companies or [])
    ):
        past_companies = [*(past_companies or []), default_past_company]

    card = PersonCard(
        name=name,
        profile_url=profile_url,
        headline=headline,
        location=location,
        connection_degree=connection_degree,
        shared_connections=shared_connections,
        current_company=current_company,
        past_companies=past_companies,
    )
    return card if is_valid_person_card(card) else None


def _card_matches_filters(
    card: PersonCard,
    *,
    raw_text: str,
    current_company: str | None = None,
    past_company: str | None = None,
    location: str | None = None,
    title_keyword: str | None = None,
) -> bool:
    if current_company:
        if not _matches_company_name(card.current_company, current_company) and (
            _normalize_match_text(current_company) not in _normalize_match_text(raw_text)
        ):
            return False
    if past_company:
        if not any(
            _matches_company_name(candidate, past_company)
            for candidate in (card.past_companies or [])
        ) and _normalize_match_text(past_company) not in _normalize_match_text(raw_text):
            return False
    if location and not _matches_location(card.location, location):
        return False
    if not _matches_title_keyword(card, raw_text, title_keyword):
        return False
    return True


def _build_fallback_keywords(*parts: str | None) -> str:
    normalized_parts: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        cleaned = " ".join(part.split()).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized_parts.append(cleaned)
    return " ".join(normalized_parts)


def _normalize_match_mode(match_mode: str) -> str:
    normalized = match_mode.strip().lower()
    if normalized not in _VALID_MATCH_MODES:
        raise ValueError(
            f"Invalid match_mode: {match_mode}. Expected one of: auto, broad, strict"
        )
    return normalized


async def _maybe_wait_for_page_gap(page_number: int) -> None:
    if page_number <= 1:
        return
    await asyncio.sleep(random.uniform(*_PAGINATION_DELAY_RANGE_SECONDS))


async def _resolve_people_filters(
    *,
    current_company: str | None = None,
    past_company: str | None = None,
    location: str | None = None,
) -> tuple[dict[str, str | None], list[str], ResolvedCompany | None, ResolvedCompany | None]:
    warnings: list[str] = []
    filters_applied: dict[str, str | None] = {
        "current_company": None,
        "past_company": None,
        "location": None,
    }

    company_tasks: list[asyncio.Task[ResolvedCompany | None]] = []
    company_inputs: list[str] = []
    if current_company:
        company_inputs.append(current_company)
        company_tasks.append(asyncio.create_task(resolve_company(current_company)))
    if past_company:
        company_inputs.append(past_company)
        company_tasks.append(asyncio.create_task(resolve_company(past_company)))

    geo_task: asyncio.Task[ResolvedGeo | None] | None = None
    if location:
        geo_task = asyncio.create_task(resolve_geo(location))

    current_result: ResolvedCompany | None = None
    past_result: ResolvedCompany | None = None
    geo_result: ResolvedGeo | None = None

    if company_tasks:
        try:
            company_results = await asyncio.wait_for(
                asyncio.gather(*company_tasks),
                timeout=_RESOLUTION_BUDGET_SECONDS,
            )
        except Exception:
            company_results = [None] * len(company_tasks)
        for raw_input, result in zip(company_inputs, company_results, strict=False):
            if raw_input == current_company:
                current_result = result
                filters_applied["current_company"] = (
                    result.company_id if result else None
                )
            elif raw_input == past_company:
                past_result = result
                filters_applied["past_company"] = result.company_id if result else None

            if result is None:
                warnings.append(
                    f"Could not resolve {('current_company' if raw_input == current_company else 'past_company')}='{raw_input}'; search ran without that filter"
                )

    if geo_task is not None:
        try:
            geo_result = await asyncio.wait_for(
                geo_task,
                timeout=_RESOLUTION_BUDGET_SECONDS,
            )
        except Exception:
            geo_result = None

        filters_applied["location"] = geo_result.geo_id if geo_result else None
        if geo_result is None:
            warnings.append(
                f"Could not resolve location='{location}'; search ran without that filter"
            )

    return filters_applied, warnings, current_result, past_result


def _build_people_search_url(
    *,
    keywords: str,
    current_company_id: str | None = None,
    past_company_id: str | None = None,
    geo_id: str | None = None,
    page: int,
) -> str:
    params = [f"keywords={quote_plus(keywords)}"]
    if current_company_id:
        params.append(f"currentCompany=%5B%22{current_company_id}%22%5D")
    if past_company_id:
        params.append(f"pastCompany=%5B%22{past_company_id}%22%5D")
    if geo_id:
        params.append(f"geoUrn=%5B%22{geo_id}%22%5D")
    if page > 1:
        params.append(f"page={page}")
    return f"https://www.linkedin.com/search/results/people/?{'&'.join(params)}"


async def _extract_people_results(
    *,
    page: Any,
    card_group: str,
    card_key: str,
    limit: int,
    started_at: float,
    default_current_company: str | None = None,
    default_past_company: str | None = None,
    match_current_company: str | None = None,
    match_past_company: str | None = None,
    match_location: str | None = None,
    match_title_keyword: str | None = None,
) -> tuple[list[PersonCard], bool]:
    rows = await SELECTORS[card_group][card_key].resolve(page)
    total_rows = await rows.count()

    people: list[PersonCard] = []
    partial = False
    max_scan = min(total_rows, max(limit * 6, limit + 20))

    for idx in range(max_scan):
        if perf_counter() - started_at >= _PAGE_BUDGET_SECONDS:
            partial = True
            break

        row = rows.nth(idx)
        try:
            link = row.locator("a[href*='/in/'], a[href*='/pub/']").first
            if await link.count() == 0:
                continue
            href = await link.get_attribute("href", timeout=300)
            profile_url = _normalize_person_profile_url(href)
            text = await row.inner_text(timeout=800)
        except Exception:
            partial = partial or bool(people)
            continue

        card = _parse_person_card_text(
            text,
            profile_url=profile_url,
            default_current_company=default_current_company,
            default_past_company=default_past_company,
        )
        if card is None:
            continue
        if not _card_matches_filters(
            card,
            raw_text=text,
            current_company=match_current_company,
            past_company=match_past_company,
            location=match_location,
            title_keyword=match_title_keyword,
        ):
            continue
        people.append(card)
        if len(people) >= limit:
            break

    return people, partial


def register_people_tools(mcp: FastMCP) -> None:
    """Register people-search tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Search People",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def search_people(
        keywords: str,
        ctx: Context | None = None,
        current_company: str | None = None,
        past_company: str | None = None,
        location: str | None = None,
        match_mode: str = "auto",
        limit: int = 10,
        page: int | None = None,
        next_cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search LinkedIn people using keywords and optional company/location filters."""

        async def _fetch() -> dict[str, Any]:
            safe_limit = max(1, min(limit, 25))
            normalized_match_mode = _normalize_match_mode(match_mode)
            current_page = decode_cursor(next_cursor, page)
            await _maybe_wait_for_page_gap(current_page)

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Resolving people search filters"
                )

            filters_applied, warnings, current_result, past_result = (
                await _resolve_people_filters(
                    current_company=current_company,
                    past_company=past_company,
                    location=location,
                )
            )
            browser = await get_or_create_browser()
            page_obj = browser.page
            if normalized_match_mode == "broad":
                search_keywords = _build_fallback_keywords(
                    keywords,
                    current_company,
                    past_company,
                    location,
                )
                search_url = _build_people_search_url(
                    keywords=search_keywords or keywords,
                    geo_id=filters_applied["location"],
                    page=current_page,
                )
            else:
                search_url = _build_people_search_url(
                    keywords=keywords,
                    current_company_id=filters_applied["current_company"],
                    past_company_id=filters_applied["past_company"],
                    geo_id=filters_applied["location"],
                    page=current_page,
                )

            if ctx:
                await ctx.report_progress(
                    progress=30, total=100, message="Loading people search results"
                )

            await goto_and_check(page_obj, search_url)
            try:
                await page_obj.wait_for_selector("main", timeout=3000)
            except Exception:
                logger.debug("People search page missing <main> for %s", search_url)
            await page_obj.evaluate("window.scrollBy(0, document.body.scrollHeight * 0.5)")

            started_at = perf_counter()
            people, partial = await _extract_people_results(
                page=page_obj,
                card_group="people",
                card_key="search_result_cards",
                limit=safe_limit,
                started_at=started_at,
                default_current_company=current_result.display_name
                if current_result
                else current_company,
                default_past_company=past_result.display_name if past_result else past_company,
                match_current_company=current_company if normalized_match_mode == "broad" else None,
                match_past_company=past_company if normalized_match_mode == "broad" else None,
                match_location=location if normalized_match_mode == "broad" else None,
                match_title_keyword=None if normalized_match_mode == "broad" else None,
            )

            if (
                normalized_match_mode == "auto"
                and not people
                and (current_company or past_company)
            ):
                fallback_keywords = _build_fallback_keywords(
                    keywords,
                    current_company,
                    past_company,
                )
                fallback_url = _build_people_search_url(
                    keywords=fallback_keywords,
                    geo_id=filters_applied["location"],
                    page=current_page,
                )
                await goto_and_check(page_obj, fallback_url)
                try:
                    await page_obj.wait_for_selector("main", timeout=3000)
                except Exception:
                    logger.debug(
                        "Fallback people search page missing <main> for %s",
                        fallback_url,
                    )
                await page_obj.evaluate(
                    "window.scrollBy(0, document.body.scrollHeight * 0.5)"
                )
                started_at = perf_counter()
                people, partial = await _extract_people_results(
                    page=page_obj,
                    card_group="people",
                    card_key="search_result_cards",
                    limit=safe_limit,
                    started_at=started_at,
                    default_current_company=current_company,
                    default_past_company=past_company,
                    match_current_company=current_company,
                    match_past_company=past_company,
                    match_location=location,
                    match_title_keyword=keywords,
                )
                if not people and keywords:
                    broadened_keywords = _build_fallback_keywords(
                        current_company,
                        past_company,
                        location,
                    )
                    if broadened_keywords:
                        warnings.append(
                            f"No exact matches for keywords='{keywords}'; broadened search to company/background filters only"
                        )
                        broadened_url = _build_people_search_url(
                            keywords=broadened_keywords,
                            geo_id=filters_applied["location"],
                            page=current_page,
                        )
                        await goto_and_check(page_obj, broadened_url)
                        try:
                            await page_obj.wait_for_selector("main", timeout=3000)
                        except Exception:
                            logger.debug(
                                "Broadened people search page missing <main> for %s",
                                broadened_url,
                            )
                        await page_obj.evaluate(
                            "window.scrollBy(0, document.body.scrollHeight * 0.5)"
                        )
                        started_at = perf_counter()
                        people, partial = await _extract_people_results(
                            page=page_obj,
                            card_group="people",
                            card_key="search_result_cards",
                            limit=safe_limit,
                            started_at=started_at,
                            default_current_company=current_company,
                            default_past_company=past_company,
                            match_current_company=current_company,
                            match_past_company=past_company,
                            match_location=location,
                            match_title_keyword=None,
                        )

            page_text = ""
            try:
                page_text = await page_obj.locator("body").inner_text(timeout=1000)
            except Exception:
                pass
            total_results = _extract_total_count(page_text)

            response = build_paginated_response(
                results=people,
                page=current_page,
                limit=safe_limit,
                total=total_results,
                partial=partial,
                warnings=warnings or None,
            )
            payload = response.to_dict()
            payload["filters_applied"] = filters_applied
            payload["match_mode"] = normalized_match_mode

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="People search complete"
                )

            return payload

        return await run_read_tool("search_people", _fetch)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Get Company People",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def get_company_people(
        company_name: str,
        ctx: Context | None = None,
        past_company: str | None = None,
        title_keyword: str | None = None,
        limit: int = 10,
        page: int | None = None,
        next_cursor: str | None = None,
    ) -> dict[str, Any]:
        """Get people at a company with optional past-company and title filters."""

        async def _fetch() -> dict[str, Any]:
            safe_limit = max(1, min(limit, 25))
            current_page = decode_cursor(next_cursor, page)
            await _maybe_wait_for_page_gap(current_page)

            if ctx:
                await ctx.report_progress(
                    progress=0, total=100, message="Resolving company filters"
                )

            async def _resolve_primary_company() -> ResolvedCompany | None:
                return await resolve_company(company_name)

            primary_task = asyncio.create_task(_resolve_primary_company())
            past_task: asyncio.Task[ResolvedCompany | None] | None = None
            if past_company:
                past_task = asyncio.create_task(resolve_company(past_company))

            try:
                primary_result = await asyncio.wait_for(
                    primary_task,
                    timeout=_RESOLUTION_BUDGET_SECONDS,
                )
            except Exception:
                primary_result = None

            try:
                past_result = (
                    await asyncio.wait_for(past_task, timeout=_RESOLUTION_BUDGET_SECONDS)
                    if past_task is not None
                    else None
                )
            except Exception:
                past_result = None

            warnings: list[str] = []
            slug = (
                primary_result.company_slug
                if primary_result
                else quote_plus(company_name.strip().lower().replace(" ", "-"))
            )
            if primary_result is None:
                warnings.append(
                    f"Could not resolve company_name='{company_name}'; using best-effort slug '{slug}'"
                )
            if past_company and past_result is None:
                warnings.append(
                    f"Could not resolve past_company='{past_company}'; search ran without that filter"
                )

            browser = await get_or_create_browser()
            page_obj = browser.page

            use_people_search = primary_result is not None
            resolved_primary = primary_result if use_people_search else None
            if resolved_primary is not None:
                url = _build_people_search_url(
                    keywords=title_keyword or "",
                    current_company_id=resolved_primary.company_id,
                    past_company_id=past_result.company_id if past_result else None,
                    page=current_page,
                )
            else:
                params: list[str] = []
                if title_keyword:
                    params.append(f"keywords={quote_plus(title_keyword)}")
                if current_page > 1:
                    params.append(f"page={current_page}")
                suffix = f"?{'&'.join(params)}" if params else ""
                url = f"https://www.linkedin.com/company/{slug}/people/{suffix}"

            if ctx:
                await ctx.report_progress(
                    progress=30, total=100, message="Loading company people page"
                )

            await goto_and_check(page_obj, url)
            try:
                await page_obj.wait_for_selector("main", timeout=3000)
            except Exception:
                logger.debug("Company people page missing <main> for %s", url)

            started_at = perf_counter()
            people, partial = await _extract_people_results(
                page=page_obj,
                card_group="people" if use_people_search else "company_people",
                card_key="search_result_cards" if use_people_search else "people_cards",
                limit=safe_limit,
                started_at=started_at,
                default_current_company=primary_result.display_name
                if primary_result
                else company_name,
                default_past_company=past_result.display_name if past_result else past_company,
            )

            if not people:
                fallback_url = _build_people_search_url(
                    keywords=_build_fallback_keywords(
                        company_name,
                        past_company,
                        title_keyword,
                    ),
                    page=current_page,
                )
                await goto_and_check(page_obj, fallback_url)
                try:
                    await page_obj.wait_for_selector("main", timeout=3000)
                except Exception:
                    logger.debug(
                        "Fallback company people page missing <main> for %s",
                        fallback_url,
                    )
                await page_obj.evaluate(
                    "window.scrollBy(0, document.body.scrollHeight * 0.5)"
                )
                started_at = perf_counter()
                people, partial = await _extract_people_results(
                    page=page_obj,
                    card_group="people",
                    card_key="search_result_cards",
                    limit=safe_limit,
                    started_at=started_at,
                    default_current_company=company_name,
                    default_past_company=past_company,
                    match_current_company=company_name,
                    match_past_company=past_company,
                    match_title_keyword=title_keyword,
                )

            page_text = ""
            try:
                page_text = await page_obj.locator("body").inner_text(timeout=1000)
            except Exception:
                pass
            total_results = _extract_total_count(page_text)

            response = build_paginated_response(
                results=people,
                page=current_page,
                limit=safe_limit,
                total=total_results,
                partial=partial,
                warnings=warnings or None,
            )
            payload = response.to_dict()
            payload["filters_applied"] = {
                "company_name": primary_result.company_id if primary_result else None,
                "past_company": past_result.company_id if past_result else None,
                "title_keyword": title_keyword,
            }

            if ctx:
                await ctx.report_progress(
                    progress=100, total=100, message="Company people search complete"
                )

            return payload

        return await run_read_tool("get_company_people", _fetch)
