"""Tests for people-search MCP tools."""

from __future__ import annotations

from typing import Any, Callable, Coroutine, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_mcp_server.core.resolver import ResolvedCompany, ResolvedGeo


async def get_tool_fn(
    mcp: Any, name: str
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    tool = await mcp.get_tool(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' not found")
    return cast(Callable[..., Coroutine[Any, Any, dict[str, Any]]], tool.fn)


def make_person_row(
    text: str,
    href: str | None = "https://www.linkedin.com/in/person-1/",
) -> MagicMock:
    row = MagicMock()
    row.inner_text = AsyncMock(return_value=text)

    link = MagicMock()
    link.count = AsyncMock(return_value=1 if href else 0)
    link.get_attribute = AsyncMock(return_value=href)

    nested = MagicMock()
    nested.first = link
    row.locator = MagicMock(return_value=nested)
    return row


def make_rows(rows: list[MagicMock]) -> MagicMock:
    locator = MagicMock()
    locator.count = AsyncMock(return_value=len(rows))
    locator.nth = lambda idx: rows[idx]
    return locator


class TestSearchPeople:
    @pytest.fixture(autouse=True)
    def _patch_deps(self, monkeypatch):
        self.page = MagicMock()
        self.page.evaluate = AsyncMock()
        self.page.wait_for_selector = AsyncMock()
        body = MagicMock()
        body.inner_text = AsyncMock(return_value="77 results")
        self.page.locator = MagicMock(return_value=body)
        browser = MagicMock()
        browser.page = self.page

        self.mock_goto_and_check = AsyncMock()
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.goto_and_check", self.mock_goto_and_check
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people._maybe_wait_for_page_gap",
            AsyncMock(),
        )

    async def test_search_people_with_all_filters(self, monkeypatch):
        rows = make_rows(
            [
                make_person_row(
                    "\n".join(
                        [
                            "Priya Sharma",
                            "Senior ML Engineer at Mastercard",
                            "Singapore",
                            "2nd degree connection",
                            "3 shared connections",
                        ]
                    ),
                    "/in/priya-sharma/",
                )
            ]
        )
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(
                side_effect=[
                    ResolvedCompany(
                        company_id="2034",
                        company_slug="mastercard",
                        company_url="https://www.linkedin.com/company/mastercard",
                        display_name="Mastercard",
                    ),
                    ResolvedCompany(
                        company_id="999",
                        company_slug="exl",
                        company_url="https://www.linkedin.com/company/exl",
                        display_name="EXL",
                    ),
                ]
            ),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(
                return_value=ResolvedGeo(geo_id="102454443", geo_label="Singapore")
            ),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(
            keywords="machine learning engineer",
            current_company="Mastercard",
            past_company="EXL",
            location="Singapore",
        )

        assert result["status"] == "success"
        data = result["data"]
        assert data["total"] == 77
        assert data["filters_applied"] == {
            "current_company": "2034",
            "past_company": "999",
            "location": "102454443",
        }
        assert len(data["results"]) == 1
        assert data["results"][0]["name"] == "Priya Sharma"
        assert data["results"][0]["profile_url"] == (
            "https://www.linkedin.com/in/priya-sharma/"
        )

    async def test_search_people_unresolved_filter_returns_warning(self, monkeypatch):
        rows = make_rows(
            [
                make_person_row(
                    "\n".join(
                        [
                            "Alex Tan",
                            "Engineer at Mastercard",
                            "Singapore",
                        ]
                    ),
                )
            ]
        )
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(
                side_effect=[
                    ResolvedCompany(
                        company_id="2034",
                        company_slug="mastercard",
                        company_url="https://www.linkedin.com/company/mastercard",
                        display_name="Mastercard",
                    ),
                    None,
                ]
            ),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(return_value=None),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(
            keywords="engineer",
            current_company="Mastercard",
            past_company="EXL",
            location="Singapore",
        )

        assert result["status"] == "success"
        warnings = result["data"]["warnings"]
        assert warnings is not None
        assert any("past_company='EXL'" in warning for warning in warnings)
        assert any("location='Singapore'" in warning for warning in warnings)

    async def test_search_people_next_cursor_overrides_page(self, monkeypatch):
        rows = make_rows([])
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(return_value=None),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.core.pagination import encode_next_cursor
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        cursor = encode_next_cursor(4)
        result = await tool_fn(
            keywords="engineer",
            page=2,
            next_cursor=cursor,
        )

        assert result["status"] == "success"
        assert self.mock_goto_and_check.await_args_list
        searched_url = cast(Any, self.mock_goto_and_check.await_args_list[0]).args[1]
        assert "page=4" in searched_url

    async def test_search_people_invalid_match_mode_returns_error(self):
        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(keywords="engineer", match_mode="nope")

        assert result["status"] == "error"
        assert result["error_code"] == "validation_error"

    async def test_search_people_invalid_cards_dropped(self, monkeypatch):
        rows = make_rows(
            [
                make_person_row(
                    "Missing URL\nHeadline",
                    None,
                ),
                make_person_row(
                    "Valid Person\nSenior Engineer at Mastercard\nSingapore",
                    "/in/valid-person/",
                ),
            ]
        )
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(return_value=None),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(keywords="engineer")

        assert result["status"] == "success"
        assert len(result["data"]["results"]) == 1
        assert result["data"]["results"][0]["name"] == "Valid Person"

    async def test_search_people_scans_past_leading_invalid_rows(self, monkeypatch):
        rows = make_rows(
            [make_person_row("Home", None) for _ in range(8)]
            + [
                make_person_row(
                    "Valid Person\nSenior Engineer at Mastercard\nSingapore",
                    "/in/valid-person/",
                )
            ]
        )
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(return_value=None),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(keywords="engineer", limit=1)

        assert result["status"] == "success"
        assert len(result["data"]["results"]) == 1

    async def test_search_people_partial_result(self, monkeypatch):
        rows = make_rows(
            [
                make_person_row("Priya Sharma\nEngineer at Mastercard", "/in/priya/"),
                make_person_row("Second Person\nEngineer at Mastercard", "/in/second/"),
            ]
        )
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(return_value=None),
        )

        perf_values = iter([0.0, 46.0, 46.0])
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.perf_counter",
            lambda: next(perf_values),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(keywords="engineer")

        assert result["status"] == "success"
        assert result["data"]["partial"] is True

    async def test_search_people_keyword_fallback_when_filters_unresolved(
        self, monkeypatch
    ):
        rows = make_rows(
            [
                make_person_row(
                    "\n".join(
                        [
                            "Divya Monga • 2nd",
                            "Machine Learning Engineer at Mastercard",
                            "Singapore, Singapore",
                            "Past: Associate managing consultant at EXL",
                        ]
                    ),
                    "/in/divyamonga/",
                )
            ]
        )
        chain = MagicMock()
        chain.resolve = AsyncMock(side_effect=[make_rows([]), rows])
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(
                return_value=ResolvedGeo(geo_id="102454443", geo_label="Singapore")
            ),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(
            keywords="machine learning engineer",
            current_company="Mastercard",
            past_company="EXL",
            location="Singapore",
        )

        assert result["status"] == "success"
        assert len(result["data"]["results"]) == 1
        assert self.mock_goto_and_check.await_count == 2

    async def test_search_people_broadens_when_keyword_fallback_empty(
        self, monkeypatch
    ):
        empty_rows = make_rows([])
        broadened_rows = make_rows(
            [
                make_person_row(
                    "\n".join(
                        [
                            "Divya Monga • 2nd",
                            "Machine Learning Engineer at Mastercard",
                            "Singapore, Singapore",
                            "Current: Machine Learning Engineer at Mastercard",
                            "Past: Senior Consultant at EXL",
                        ]
                    ),
                    "/in/divyamonga/",
                )
            ]
        )
        chain = MagicMock()
        chain.resolve = AsyncMock(side_effect=[empty_rows, empty_rows, broadened_rows])
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(
                return_value=ResolvedGeo(geo_id="102454443", geo_label="Singapore")
            ),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(
            keywords="machine learning engineer",
            current_company="Mastercard",
            past_company="EXL",
            location="Singapore",
        )

        assert result["status"] == "success"
        assert len(result["data"]["results"]) == 1
        warnings = result["data"]["warnings"]
        assert warnings is not None
        assert any("broadened search" in warning for warning in warnings)
        assert self.mock_goto_and_check.await_count == 3

    async def test_search_people_strict_mode_skips_fallbacks(self, monkeypatch):
        empty_rows = make_rows([])
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=empty_rows)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(
                return_value=ResolvedGeo(geo_id="102454443", geo_label="Singapore")
            ),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(
            keywords="machine learning engineer",
            current_company="Mastercard",
            past_company="EXL",
            location="Singapore",
            match_mode="strict",
        )

        assert result["status"] == "success"
        assert result["data"]["results"] == []
        assert result["data"]["match_mode"] == "strict"
        assert self.mock_goto_and_check.await_count == 1

    async def test_search_people_broad_mode_uses_broadened_query_immediately(
        self, monkeypatch
    ):
        rows = make_rows(
            [
                make_person_row(
                    "\n".join(
                        [
                            "Gunjita Dhingra • 2nd",
                            "VISA Consultant, Data Science & Analytics | Ex- Walmart | Ex- EXL",
                            "Dubai, United Arab Emirates",
                            "Current: Data Science and Analytics at Visa",
                            "Past: EXL",
                        ]
                    ),
                    "/in/gunjita-dhingra/",
                )
            ]
        )
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {"people": {"search_result_cards": chain}},
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_geo",
            AsyncMock(return_value=None),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "search_people")
        result = await tool_fn(
            keywords="machine learning engineer",
            current_company="Visa",
            past_company="EXL",
            match_mode="broad",
        )

        assert result["status"] == "success"
        assert len(result["data"]["results"]) == 1
        assert result["data"]["match_mode"] == "broad"
        assert self.mock_goto_and_check.await_count == 1


class TestGetCompanyPeople:
    @pytest.fixture(autouse=True)
    def _patch_deps(self, monkeypatch):
        self.page = MagicMock()
        self.page.evaluate = AsyncMock()
        self.page.wait_for_selector = AsyncMock()
        body = MagicMock()
        body.inner_text = AsyncMock(return_value="35 results")
        self.page.locator = MagicMock(return_value=body)
        browser = MagicMock()
        browser.page = self.page

        self.mock_goto_and_check = AsyncMock()
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.get_or_create_browser",
            AsyncMock(return_value=browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.goto_and_check", self.mock_goto_and_check
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people._maybe_wait_for_page_gap",
            AsyncMock(),
        )

    async def test_get_company_people_resolves_slug_and_company_id(self, monkeypatch):
        rows = make_rows(
            [
                make_person_row(
                    "\n".join(
                        [
                            "Chris Lim",
                            "Platform Engineer at Visa",
                            "Singapore",
                            "2nd degree connection",
                        ]
                    ),
                    "/in/chris-lim/",
                )
            ]
        )
        chain = MagicMock()
        chain.resolve = AsyncMock(return_value=rows)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {
                "people": {"search_result_cards": chain},
                "company_people": {"people_cards": chain},
            },
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(
                side_effect=[
                    ResolvedCompany(
                        company_id="1185",
                        company_slug="visa",
                        company_url="https://www.linkedin.com/company/visa",
                        display_name="Visa",
                    ),
                    ResolvedCompany(
                        company_id="999",
                        company_slug="exl",
                        company_url="https://www.linkedin.com/company/exl",
                        display_name="EXL",
                    ),
                ]
            ),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_company_people")
        result = await tool_fn(
            company_name="Visa",
            past_company="EXL",
            title_keyword="engineer",
        )

        assert result["status"] == "success"
        assert result["data"]["filters_applied"] == {
            "company_name": "1185",
            "past_company": "999",
            "title_keyword": "engineer",
        }
        assert result["data"]["results"][0]["current_company"] == "Visa"

    async def test_get_company_people_keyword_fallback_when_filtered_search_empty(
        self, monkeypatch
    ):
        empty_rows = make_rows([])
        fallback_rows = make_rows(
            [
                make_person_row(
                    "\n".join(
                        [
                            "Shashwat Thakur • 2nd",
                            "Senior Data Engineer, Visa Inc",
                            "Ghaziabad, Uttar Pradesh, India",
                            "Past: EXL",
                        ]
                    ),
                    "/in/shashwat-thakur/",
                )
            ]
        )
        search_chain = MagicMock()
        search_chain.resolve = AsyncMock(side_effect=[empty_rows, fallback_rows])
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.SELECTORS",
            {
                "people": {"search_result_cards": search_chain},
                "company_people": {"people_cards": search_chain},
            },
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.people.resolve_company",
            AsyncMock(
                side_effect=[
                    ResolvedCompany(
                        company_id="1277",
                        company_slug="visa",
                        company_url="https://www.linkedin.com/company/visa",
                        display_name="Visa",
                    ),
                    ResolvedCompany(
                        company_id="163743",
                        company_slug="exl-service",
                        company_url="https://www.linkedin.com/company/exl-service",
                        display_name="EXL",
                    ),
                ]
            ),
        )

        from fastmcp import FastMCP
        from linkedin_mcp_server.tools.people import register_people_tools

        mcp = FastMCP("test")
        register_people_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_company_people")
        result = await tool_fn(company_name="Visa", past_company="EXL", limit=5)

        assert result["status"] == "success"
        assert len(result["data"]["results"]) == 1
        assert self.mock_goto_and_check.await_count == 2
