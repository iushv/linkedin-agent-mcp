"""Entity resolution helpers for company and geography filters."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, unquote, urlparse

from linkedin_mcp_server.drivers.browser import get_or_create_browser
from linkedin_mcp_server.tools._common import goto_and_check

logger = logging.getLogger(__name__)

STATE_DIR = Path.home() / ".linkedin-mcp"
ENTITY_CACHE_FILE = STATE_DIR / "entity_cache.json"
CACHE_TTL = timedelta(days=30)
LIVE_RESOLUTION_TIMEOUT_SECONDS = 12.0
COMPANY_CACHE_VERSION = 4


@dataclass
class ResolvedCompany:
    company_id: str
    company_slug: str
    company_url: str
    display_name: str


@dataclass
class ResolvedGeo:
    geo_id: str
    geo_label: str


_company_cache: dict[str, ResolvedCompany] = {}
_geo_cache: dict[str, ResolvedGeo] = {}
_resolution_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _normalize_key(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _default_cache_payload() -> dict[str, Any]:
    return {"companies": {}, "geos": {}}


def _get_resolution_lock(kind: str, key: str) -> asyncio.Lock:
    lock_key = (kind, key)
    lock = _resolution_locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _resolution_locks[lock_key] = lock
    return lock


async def _read_cache_payload() -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        if not ENTITY_CACHE_FILE.exists():
            return _default_cache_payload()
        try:
            payload = json.loads(ENTITY_CACHE_FILE.read_text())
        except json.JSONDecodeError:
            return _default_cache_payload()
        if not isinstance(payload, dict):
            return _default_cache_payload()
        payload.setdefault("companies", {})
        payload.setdefault("geos", {})
        return payload

    return await asyncio.to_thread(_read)


async def _write_cache_payload(payload: dict[str, Any]) -> None:
    def _write() -> None:
        _ensure_state_dir()
        ENTITY_CACHE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))

    await asyncio.to_thread(_write)


def _is_fresh(entry: dict[str, Any]) -> bool:
    raw_timestamp = entry.get("cached_at")
    if not isinstance(raw_timestamp, str):
        return False
    try:
        cached_at = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    return _now_utc() - cached_at <= CACHE_TTL


def _company_from_payload(entry: dict[str, Any]) -> ResolvedCompany | None:
    if entry.get("cache_version") != COMPANY_CACHE_VERSION:
        return None
    try:
        return ResolvedCompany(
            company_id=str(entry["company_id"]),
            company_slug=str(entry["company_slug"]),
            company_url=str(entry["company_url"]),
            display_name=str(entry["display_name"]),
        )
    except KeyError:
        return None


def _geo_from_payload(entry: dict[str, Any]) -> ResolvedGeo | None:
    try:
        return ResolvedGeo(
            geo_id=str(entry["geo_id"]),
            geo_label=str(entry["geo_label"]),
        )
    except KeyError:
        return None


async def _store_company(key: str, company: ResolvedCompany) -> None:
    _company_cache[key] = company
    payload = await _read_cache_payload()
    payload["companies"][key] = {
        **asdict(company),
        "cache_version": COMPANY_CACHE_VERSION,
        "cached_at": _now_utc().isoformat().replace("+00:00", "Z"),
    }
    await _write_cache_payload(payload)


async def _store_geo(key: str, geo: ResolvedGeo) -> None:
    _geo_cache[key] = geo
    payload = await _read_cache_payload()
    payload["geos"][key] = {
        **asdict(geo),
        "cached_at": _now_utc().isoformat().replace("+00:00", "Z"),
    }
    await _write_cache_payload(payload)


def _extract_company_slug(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    match = re.search(r"/company/([^/?#]+)/?", parsed.path)
    return match.group(1) if match else None


def _extract_company_id(value: str | None) -> str | None:
    if not value:
        return None
    urn_match = re.search(r"(?:company|fs_miniCompany):(\d+)", value)
    if urn_match:
        return urn_match.group(1)
    digits_match = re.search(r"\b(\d{3,})\b", value)
    return digits_match.group(1) if digits_match else None


def _extract_company_display_name(raw_text: str | None, slug: str) -> str:
    if raw_text:
        for line in raw_text.splitlines():
            candidate = " ".join(line.split()).strip()
            if candidate:
                return candidate
    return slug.replace("-", " ").title()


def _company_candidate_matches(name: str, slug: str, display_name: str) -> bool:
    requested = _normalize_key(name)
    slug_text = _normalize_key(slug.replace("-", " "))
    display_text = _normalize_key(display_name)
    if not requested:
        return False
    return (
        requested in slug_text
        or requested in display_text
        or slug_text in requested
        or display_text in requested
    )


def _extract_current_company_ids(value: str | None) -> list[str]:
    if not value:
        return []
    decoded = unquote(value)
    match = re.search(r"currentCompany=\[([^\]]+)\]", decoded)
    if not match:
        return []
    raw_values = match.group(1)
    return re.findall(r"\d+", raw_values)


def _pick_company_filter_id(candidates: list[tuple[str | None, str | None]]) -> str | None:
    best_single: str | None = None
    best_multi: str | None = None

    for text, href in candidates:
        ids = _extract_current_company_ids(href)
        if not ids:
            continue

        lowered = (text or "").lower()
        if len(ids) == 1 and "work here" in lowered:
            return ids[0]
        if len(ids) == 1 and "employee" in lowered:
            best_single = best_single or ids[0]
            continue
        if len(ids) == 1:
            best_single = best_single or ids[0]
            continue
        best_multi = best_multi or ids[0]

    return best_single or best_multi


async def _extract_company_filter_id_from_page(page: Any) -> str | None:
    locator = page.locator("a[href*='search/results/people']")
    try:
        total = min(await locator.count(), 10)
    except Exception:
        return None

    candidates: list[tuple[str | None, str | None]] = []
    for idx in range(total):
        item = locator.nth(idx)
        try:
            href = await item.get_attribute("href", timeout=400)
        except Exception:
            href = None
        try:
            text = await item.inner_text(timeout=400)
        except Exception:
            text = None
        candidates.append((text, href))
    return _pick_company_filter_id(candidates)


def _extract_company_id_from_html(html: str) -> str | None:
    for pattern in (
        r"urn:li:company:(\d+)",
        r"urn:li:fs_miniCompany:(\d+)",
        r'"companyId"\s*:\s*"?(\d+)"?',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


def _extract_geo_id_from_html(html: str) -> str | None:
    for pattern in (
        r"geoUrn[^\d]{0,20}(\d+)",
        r"urn:li:fs_geo:(\d+)",
        r'"geoId"\s*:\s*"?(\d+)"?',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


def _extract_geo_id(value: str | None) -> str | None:
    if not value:
        return None
    encoded_match = re.search(r"geoUrn=%5B%22(\d+)%22%5D", value)
    if encoded_match:
        return encoded_match.group(1)
    plain_match = re.search(r"geoUrn=\[?\"?(\d+)", value)
    if plain_match:
        return plain_match.group(1)
    urn_match = re.search(r"(?:fs_geo|geo):(\d+)", value)
    return urn_match.group(1) if urn_match else None


async def _live_resolve_company(name: str) -> ResolvedCompany | None:
    browser = await get_or_create_browser()
    page = browser.page
    await goto_and_check(
        page,
        f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(name)}",
    )
    try:
        await page.wait_for_selector("main", timeout=3000)
    except Exception:
        logger.debug("Company resolver: no <main> for %s", name)

    locator = page.locator("a[href*='/company/']")
    try:
        total = min(await locator.count(), 5)
    except Exception:
        return None

    candidates: list[tuple[str, str, str]] = []
    for idx in range(total):
        link = locator.nth(idx)
        try:
            href = await link.get_attribute("href", timeout=500)
        except Exception:
            continue
        company_url = href or ""
        if company_url.startswith("/"):
            company_url = f"https://www.linkedin.com{company_url}"
        slug = _extract_company_slug(company_url)
        if not slug:
            continue
        try:
            raw_text = await link.inner_text(timeout=500)
            display_name = _extract_company_display_name(raw_text, slug)
        except Exception:
            display_name = _extract_company_display_name(None, slug)
        candidates.append((company_url, slug, display_name))

    best_match: ResolvedCompany | None = None
    normalized_name = _normalize_key(name)
    for company_url, slug, display_name in candidates:
        company_id = None

        if not company_id:
            try:
                await goto_and_check(page, company_url)
                company_id = await _extract_company_filter_id_from_page(page)
                if not company_id:
                    html = await page.content()
                    company_id = _extract_company_id_from_html(html)
            except Exception:
                company_id = None

        if not company_id:
            continue

        if not _company_candidate_matches(name, slug, display_name):
            continue

        company = ResolvedCompany(
            company_id=company_id,
            company_slug=slug,
            company_url=company_url.rstrip("/"),
            display_name=display_name,
        )
        if _normalize_key(display_name) == normalized_name:
            return company
        if best_match is None:
            best_match = company

    return best_match


async def _live_resolve_geo(location: str) -> ResolvedGeo | None:
    browser = await get_or_create_browser()
    page = browser.page
    await goto_and_check(
        page,
        f"https://www.linkedin.com/jobs/search/?keywords=&location={quote_plus(location)}",
    )
    try:
        await page.wait_for_selector("main", timeout=3000)
    except Exception:
        logger.debug("Geo resolver: no <main> for %s", location)

    page_url = getattr(page, "url", "")
    geo_id = _extract_geo_id(page_url)
    if geo_id:
        return ResolvedGeo(geo_id=geo_id, geo_label=location)

    try:
        html = await page.content()
    except Exception:
        html = ""
    geo_id = _extract_geo_id_from_html(html)
    if geo_id:
        return ResolvedGeo(geo_id=geo_id, geo_label=location)

    locator = page.locator("a[href*='geoUrn']")
    try:
        total = min(await locator.count(), 10)
    except Exception:
        return None

    for idx in range(total):
        item = locator.nth(idx)
        try:
            href = await item.get_attribute("href", timeout=500)
        except Exception:
            continue
        geo_id = _extract_geo_id(href)
        if geo_id:
            return ResolvedGeo(geo_id=geo_id, geo_label=location)

    return None


async def resolve_company(name: str) -> ResolvedCompany | None:
    """Resolve a human-readable company name to LinkedIn identifiers."""
    key = _normalize_key(name)
    if not key:
        return None

    cached = _company_cache.get(key)
    if cached is not None:
        return cached

    payload = await _read_cache_payload()
    cached_entry = payload.get("companies", {}).get(key)
    if isinstance(cached_entry, dict) and _is_fresh(cached_entry):
        company = _company_from_payload(cached_entry)
        if company is not None:
            _company_cache[key] = company
            return company

    async with _get_resolution_lock("company", key):
        cached = _company_cache.get(key)
        if cached is not None:
            return cached
        try:
            company = await asyncio.wait_for(
                _live_resolve_company(name),
                timeout=LIVE_RESOLUTION_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning("Company resolution failed for %s", name, exc_info=True)
            return None
        if company is None:
            return None
        await _store_company(key, company)
        return company


async def resolve_geo(location: str) -> ResolvedGeo | None:
    """Resolve a human-readable location to a LinkedIn geo URN."""
    key = _normalize_key(location)
    if not key:
        return None

    cached = _geo_cache.get(key)
    if cached is not None:
        return cached

    payload = await _read_cache_payload()
    cached_entry = payload.get("geos", {}).get(key)
    if isinstance(cached_entry, dict) and _is_fresh(cached_entry):
        geo = _geo_from_payload(cached_entry)
        if geo is not None:
            _geo_cache[key] = geo
            return geo

    async with _get_resolution_lock("geo", key):
        cached = _geo_cache.get(key)
        if cached is not None:
            return cached
        try:
            geo = await asyncio.wait_for(
                _live_resolve_geo(location),
                timeout=LIVE_RESOLUTION_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning("Geo resolution failed for %s", location, exc_info=True)
            return None
        if geo is None:
            return None
        await _store_geo(key, geo)
        return geo


async def resolve_companies(
    names: list[str],
) -> dict[str, ResolvedCompany | None]:
    """Resolve multiple company names concurrently."""
    results = await asyncio.gather(*(resolve_company(name) for name in names))
    return {name: result for name, result in zip(names, results, strict=False)}


async def resolve_geos(
    locations: list[str],
) -> dict[str, ResolvedGeo | None]:
    """Resolve multiple geo locations concurrently."""
    results = await asyncio.gather(*(resolve_geo(location) for location in locations))
    return {
        location: result
        for location, result in zip(locations, results, strict=False)
    }


def reset_resolver_state() -> None:
    """Reset in-memory resolver caches for test isolation."""
    _company_cache.clear()
    _geo_cache.clear()
    _resolution_locks.clear()
