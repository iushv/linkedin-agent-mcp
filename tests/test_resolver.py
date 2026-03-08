from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import linkedin_mcp_server.core.resolver as resolver


@pytest.fixture(autouse=True)
def isolate_resolver(tmp_path, monkeypatch):
    monkeypatch.setattr(resolver, "STATE_DIR", tmp_path)
    monkeypatch.setattr(resolver, "ENTITY_CACHE_FILE", tmp_path / "entity_cache.json")
    resolver.reset_resolver_state()
    yield tmp_path
    resolver.reset_resolver_state()


def _write_cache(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload))


class TestResolveCompany:
    def test_pick_company_filter_id_prefers_work_here_link(self):
        result = resolver._pick_company_filter_id(
            [
                (
                    "10K+ employees",
                    "/search/results/people/?currentCompany=%5B%223015%22%5D&origin=COMPANY_PAGE_CANNED_SEARCH",
                ),
                (
                    "Vivek & 1 other connection work here",
                    "/search/results/people/?currentCompany=%5B%2211448%22%5D&origin=COMPANY_PAGE_CANNED_SEARCH",
                ),
            ]
        )

        assert result == "11448"

    def test_company_candidate_matches_rejects_unrelated_result(self):
        assert (
            resolver._company_candidate_matches(
                "Mastercard",
                "visa",
                "Visa",
            )
            is False
        )

    def test_extract_company_display_name_uses_first_line(self):
        result = resolver._extract_company_display_name(
            "Mastercard\nIT Services and IT Consulting\nPurchase, NY",
            "mastercard",
        )

        assert result == "Mastercard"

    @pytest.mark.asyncio
    async def test_session_cache_hit(self, monkeypatch):
        company = resolver.ResolvedCompany(
            company_id="2034",
            company_slug="mastercard",
            company_url="https://www.linkedin.com/company/mastercard",
            display_name="Mastercard",
        )
        resolver._company_cache["mastercard"] = company
        live = AsyncMock()
        monkeypatch.setattr(resolver, "_live_resolve_company", live)

        result = await resolver.resolve_company("Mastercard")

        assert result == company
        live.assert_not_called()

    @pytest.mark.asyncio
    async def test_disk_cache_hit(self, isolate_resolver, monkeypatch):
        cached_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _write_cache(
            isolate_resolver / "entity_cache.json",
            {
                "companies": {
                    "mastercard": {
                        "company_id": "2034",
                        "company_slug": "mastercard",
                        "company_url": "https://www.linkedin.com/company/mastercard",
                        "display_name": "Mastercard",
                        "cache_version": resolver.COMPANY_CACHE_VERSION,
                        "cached_at": cached_at,
                    }
                },
                "geos": {},
            },
        )
        live = AsyncMock()
        monkeypatch.setattr(resolver, "_live_resolve_company", live)

        result = await resolver.resolve_company("Mastercard")

        assert result is not None
        assert result.company_id == "2034"
        live.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_disk_cache_falls_back_to_live(
        self,
        isolate_resolver,
        monkeypatch,
    ):
        cached_at = (
            (datetime.now(timezone.utc) - timedelta(days=40))
            .isoformat()
            .replace("+00:00", "Z")
        )
        _write_cache(
            isolate_resolver / "entity_cache.json",
            {
                "companies": {
                    "mastercard": {
                        "company_id": "old",
                        "company_slug": "old",
                        "company_url": "https://www.linkedin.com/company/old",
                        "display_name": "Old",
                        "cache_version": resolver.COMPANY_CACHE_VERSION,
                        "cached_at": cached_at,
                    }
                },
                "geos": {},
            },
        )
        live = AsyncMock(
            return_value=resolver.ResolvedCompany(
                company_id="2034",
                company_slug="mastercard",
                company_url="https://www.linkedin.com/company/mastercard",
                display_name="Mastercard",
            )
        )
        monkeypatch.setattr(resolver, "_live_resolve_company", live)

        result = await resolver.resolve_company("Mastercard")

        assert result is not None
        assert result.company_id == "2034"
        live.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_old_company_cache_version_falls_back_to_live(
        self,
        isolate_resolver,
        monkeypatch,
    ):
        cached_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _write_cache(
            isolate_resolver / "entity_cache.json",
            {
                "companies": {
                    "mastercard": {
                        "company_id": "37413",
                        "company_slug": "mastercard",
                        "company_url": "https://www.linkedin.com/company/mastercard",
                        "display_name": "Mastercard",
                        "cache_version": 1,
                        "cached_at": cached_at,
                    }
                },
                "geos": {},
            },
        )
        live = AsyncMock(
            return_value=resolver.ResolvedCompany(
                company_id="11448",
                company_slug="mastercard",
                company_url="https://www.linkedin.com/company/mastercard",
                display_name="Mastercard",
            )
        )
        monkeypatch.setattr(resolver, "_live_resolve_company", live)

        result = await resolver.resolve_company("Mastercard")

        assert result is not None
        assert result.company_id == "11448"
        live.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_live_resolution_failure_returns_none(self, monkeypatch):
        live = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(resolver, "_live_resolve_company", live)

        result = await resolver.resolve_company("Mastercard")

        assert result is None
        live.assert_awaited_once()


class TestResolveGeo:
    @pytest.mark.asyncio
    async def test_session_cache_hit(self, monkeypatch):
        geo = resolver.ResolvedGeo(geo_id="102454443", geo_label="Singapore")
        resolver._geo_cache["singapore"] = geo
        live = AsyncMock()
        monkeypatch.setattr(resolver, "_live_resolve_geo", live)

        result = await resolver.resolve_geo("Singapore")

        assert result == geo
        live.assert_not_called()

    @pytest.mark.asyncio
    async def test_disk_cache_hit(self, isolate_resolver, monkeypatch):
        cached_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _write_cache(
            isolate_resolver / "entity_cache.json",
            {
                "companies": {},
                "geos": {
                    "singapore": {
                        "geo_id": "102454443",
                        "geo_label": "Singapore",
                        "cached_at": cached_at,
                    }
                },
            },
        )
        live = AsyncMock()
        monkeypatch.setattr(resolver, "_live_resolve_geo", live)

        result = await resolver.resolve_geo("Singapore")

        assert result is not None
        assert result.geo_id == "102454443"
        live.assert_not_called()

    def test_extract_geo_id_from_html(self):
        html = '<div data-test="x">urn:li:fs_geo:102454443</div>'

        result = resolver._extract_geo_id_from_html(html)

        assert result == "102454443"


class TestBatchResolution:
    @pytest.mark.asyncio
    async def test_resolve_companies_returns_partial_results(self, monkeypatch):
        async def _live(name: str):
            if name == "Mastercard":
                return resolver.ResolvedCompany(
                    company_id="2034",
                    company_slug="mastercard",
                    company_url="https://www.linkedin.com/company/mastercard",
                    display_name="Mastercard",
                )
            return None

        monkeypatch.setattr(resolver, "_live_resolve_company", _live)

        result = await resolver.resolve_companies(["Mastercard", "EXL"])

        assert result["Mastercard"] is not None
        assert result["EXL"] is None
