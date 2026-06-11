"""Tests for post reaction/comment analytics tools."""

from __future__ import annotations

from typing import Any, Callable, Coroutine
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from linkedin_mcp_server.core.pagination import encode_next_cursor


async def get_tool_fn(
    mcp: FastMCP, name: str
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    tool = await mcp.get_tool(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' not found")
    return tool.fn  # type: ignore[attr-defined]


class FakeNode:
    def __init__(
        self,
        *,
        text: str = "",
        attrs: dict[str, str] | None = None,
        locator_map: dict[str, Any] | None = None,
    ) -> None:
        self._text = text
        self._attrs = attrs or {}
        self._locator_map = locator_map or {}
        self.click = AsyncMock()
        self.evaluate = AsyncMock()

    @property
    def first(self) -> "FakeNode":
        return self

    async def count(self) -> int:
        return 1

    async def inner_text(self, **kwargs: Any) -> str:
        del kwargs
        return self._text

    async def get_attribute(self, name: str, **kwargs: Any) -> str | None:
        del kwargs
        return self._attrs.get(name)

    def locator(self, selector: str) -> Any:
        return self._locator_map.get(selector, FakeLocator([]))


class FakeLocator:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    @property
    def first(self) -> Any:
        if self._items:
            return self._items[0]
        return FakeEmptyNode()

    async def count(self) -> int:
        return len(self._items)

    def nth(self, index: int) -> Any:
        return self._items[index]


class FakeEmptyNode(FakeNode):
    def __init__(self) -> None:
        super().__init__(text="")
        self.click = AsyncMock(side_effect=RuntimeError("no node"))

    async def count(self) -> int:
        return 0

    async def inner_text(self, **kwargs: Any) -> str:
        del kwargs
        return ""

    async def get_attribute(self, name: str, **kwargs: Any) -> str | None:
        del name, kwargs
        return None


def _reactor_row(
    *,
    name: str,
    profile_url: str,
    headline: str,
    reaction_alt: str,
) -> FakeNode:
    profile_link = FakeNode(text=name, attrs={"href": profile_url})
    reaction_icon = FakeNode(attrs={"alt": reaction_alt})
    return FakeNode(
        text=f"{name}\n{headline}",
        locator_map={
            "a[href*='/in/']": FakeLocator([profile_link]),
            ".artdeco-entity-lockup__subtitle": FakeLocator([FakeNode(text=headline)]),
            "img[alt]": FakeLocator([reaction_icon]),
        },
    )


def _comment_row(
    *,
    name: str,
    profile_url: str,
    headline: str,
    comment_text: str,
    time_ago: str,
    reactions_text: str = "3 reactions",
    row_class: str = "comments-comment-item",
) -> FakeNode:
    profile_link = FakeNode(text=name, attrs={"href": profile_url})
    return FakeNode(
        text=f"{name}\n{headline}\n{comment_text}\n{time_ago}\n{reactions_text}",
        attrs={"class": row_class},
        locator_map={
            "a[href*='/in/']": FakeLocator([profile_link]),
            ".comments-comment-item__header-subtitle": FakeLocator(
                [FakeNode(text=headline)]
            ),
            ".comments-post-meta__headline": FakeLocator([FakeNode(text=headline)]),
            ".comments-comment-meta__description": FakeLocator(
                [FakeNode(text=headline)]
            ),
            "span.t-12.t-normal": FakeLocator([FakeNode(text=headline)]),
            ".artdeco-entity-lockup__subtitle": FakeLocator([FakeNode(text=headline)]),
            ".comments-comment-item__main-content": FakeLocator(
                [FakeNode(text=comment_text)]
            ),
            "span.comments-comment-item__main-content": FakeLocator(
                [FakeNode(text=comment_text)]
            ),
            "[data-test-id='comment-content']": FakeLocator(
                [FakeNode(text=comment_text)]
            ),
            "span.update-components-text span.break-words": FakeLocator(
                [FakeNode(text=comment_text)]
            ),
            "span.break-words": FakeLocator([FakeNode(text=comment_text)]),
            "time.comments-comment-meta__timestamp": FakeLocator(
                [FakeNode(text=time_ago)]
            ),
            ".comments-comment-meta__timestamp": FakeLocator([FakeNode(text=time_ago)]),
            "time[datetime]": FakeLocator([FakeNode(text=time_ago)]),
            "a.comments-comment-meta__timestamp-link time": FakeLocator(
                [FakeNode(text=time_ago)]
            ),
            "span[aria-label$=' ago']": FakeLocator([FakeNode(text=time_ago)]),
            "time": FakeLocator([FakeNode(text=time_ago)]),
        },
    )


class TestPostAnalyticsTools:
    @pytest.fixture(autouse=True)
    def _patch_common(self, monkeypatch):
        self.page = MagicMock()
        self.browser = MagicMock(page=self.page)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.get_or_create_browser",
            AsyncMock(return_value=self.browser),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools._common.ensure_authenticated", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.goto_and_check", AsyncMock()
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed.ensure_page_healthy", AsyncMock()
        )
        monkeypatch.setattr("linkedin_mcp_server.tools.feed.asyncio.sleep", AsyncMock())

    async def test_get_post_reactions_parses_modal(self, monkeypatch):
        from linkedin_mcp_server.tools import feed as feed_module
        from linkedin_mcp_server.tools.feed import register_feed_tools

        count_button = FakeNode()
        modal = FakeNode(text="12 reactions")
        monkeypatch.setitem(
            feed_module.SELECTORS["post_reactions"],
            "count_button",
            MagicMock(find=AsyncMock(return_value=count_button)),
        )
        monkeypatch.setitem(
            feed_module.SELECTORS["post_reactions"],
            "modal",
            MagicMock(find=AsyncMock(return_value=modal)),
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed._load_reaction_modal",
            AsyncMock(
                return_value={
                    "rows": FakeLocator(
                        [
                            _reactor_row(
                                name="Ava Singh",
                                profile_url="/in/ava-singh/",
                                headline="Staff Data Scientist",
                                reaction_alt="Celebrate",
                            ),
                            _reactor_row(
                                name="Ben Shah",
                                profile_url="/in/ben-shah/",
                                headline="ML Engineer",
                                reaction_alt="Love",
                            ),
                            _reactor_row(
                                name="Cara Lee",
                                profile_url="/in/cara-lee/",
                                headline="Founder",
                                reaction_alt="Like",
                            ),
                            _reactor_row(
                                name="Dan Roy",
                                profile_url="/in/dan-roy/",
                                headline="Engineer",
                                reaction_alt="Insightful",
                            ),
                            _reactor_row(
                                name="Eli Kim",
                                profile_url="/in/eli-kim/",
                                headline="Recruiter",
                                reaction_alt="Support",
                            ),
                        ]
                    ),
                    "loaded_count": 5,
                    "total": 12,
                    "partial": False,
                    "warnings": [],
                }
            ),
        )

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_post_reactions")
        result = await tool_fn("urn:li:activity:123", limit=5)

        assert result["status"] == "success"
        assert result["data"]["post_urn"] == "urn:li:activity:123"
        assert result["data"]["total"] == 12
        assert len(result["data"]["results"]) == 5
        assert result["data"]["results"][0] == {
            "name": "Ava Singh",
            "profile_url": "https://www.linkedin.com/in/ava-singh/",
            "headline": "Staff Data Scientist",
            "reaction_type": "celebrate",
        }
        assert count_button.click.await_count == 1

    async def test_get_post_reactions_cursor_pagination(self, monkeypatch):
        from linkedin_mcp_server.tools import feed as feed_module
        from linkedin_mcp_server.tools.feed import register_feed_tools

        count_button = FakeNode()
        modal = FakeNode(text="10 reactions")
        monkeypatch.setitem(
            feed_module.SELECTORS["post_reactions"],
            "count_button",
            MagicMock(find=AsyncMock(return_value=count_button)),
        )
        monkeypatch.setitem(
            feed_module.SELECTORS["post_reactions"],
            "modal",
            MagicMock(find=AsyncMock(return_value=modal)),
        )

        rows = FakeLocator(
            [
                _reactor_row(
                    name=f"User {idx}",
                    profile_url=f"/in/user-{idx}/",
                    headline="Engineer",
                    reaction_alt="Like",
                )
                for idx in range(10)
            ]
        )
        load_modal = AsyncMock(
            return_value={
                "rows": rows,
                "loaded_count": 10,
                "total": 10,
                "partial": False,
                "warnings": [],
            }
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed._load_reaction_modal", load_modal
        )

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_post_reactions")
        result = await tool_fn(
            "urn:li:activity:123",
            limit=5,
            next_cursor=encode_next_cursor(2),
        )

        assert result["status"] == "success"
        assert [item["name"] for item in result["data"]["results"]] == [
            "User 5",
            "User 6",
            "User 7",
            "User 8",
            "User 9",
        ]
        assert load_modal.await_args_list
        assert load_modal.await_args_list[0].kwargs["target_count"] == 10

    async def test_get_post_commenters_top_level_only(self, monkeypatch):
        from linkedin_mcp_server.tools.feed import register_feed_tools

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.feed._load_comment_rows",
            AsyncMock(
                return_value={
                    "rows": FakeLocator(
                        [
                            _comment_row(
                                name="Ava Singh",
                                profile_url="/in/ava-singh/",
                                headline="Staff Data Scientist",
                                comment_text="Great post",
                                time_ago="2h",
                            ),
                            _comment_row(
                                name="Nested Reply",
                                profile_url="/in/reply-user/",
                                headline="Reply User",
                                comment_text="Nested reply",
                                time_ago="1h",
                                row_class="comments-comment-item comments-comment-item--reply",
                            ),
                        ]
                    ),
                    "loaded_count": 2,
                    "total": 2,
                    "partial": False,
                    "warnings": [],
                    "load_more_clicks": 0,
                }
            ),
        )

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_post_commenters")
        result = await tool_fn("urn:li:activity:123", limit=10)

        assert result["status"] == "success"
        assert len(result["data"]["results"]) == 1
        assert result["data"]["results"][0]["name"] == "Ava Singh"
        assert result["data"]["results"][0]["comment_text"] == "Great post"

    async def test_get_post_commenters_load_more_capped(self, monkeypatch):
        from linkedin_mcp_server.tools.extraction.engagement_rows import (
            _load_comment_rows,
        )

        row_locator = FakeLocator([])
        container = FakeNode(
            text="500 comments",
            locator_map={
                "article.comments-comment-item, article.comments-comment-entity, li.comments-comment-item, [data-test-id='comment-item'], [data-id^='urn:li:comment:']": row_locator
            },
        )
        button = FakeNode()
        buttons = FakeLocator([button])

        async def _resolve_optional_locator(group: str, key: str, page: Any) -> Any:
            del group, page
            if key == "container":
                return container
            if key == "load_more":
                return buttons
            return None

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.extraction.engagement_rows._resolve_optional_locator",
            _resolve_optional_locator,
        )
        monkeypatch.setattr("linkedin_mcp_server.tools.feed.asyncio.sleep", AsyncMock())

        result = await _load_comment_rows(MagicMock(), target_count=50)

        assert result["load_more_clicks"] == 10
        assert button.click.await_count == 10
        assert result["partial"] is True
        assert result["warnings"] == ["Stopped after 10 comment pagination clicks."]

    async def test_get_post_commenters_ignores_main_post_article(self, monkeypatch):
        from linkedin_mcp_server.tools.feed import register_feed_tools

        comment_rows = FakeLocator(
            [
                _comment_row(
                    name="Ava Singh",
                    profile_url="/in/ava-singh/",
                    headline="Staff Data Scientist",
                    comment_text="Great post",
                    time_ago="2h",
                ),
                _comment_row(
                    name="Ben Shah",
                    profile_url="/in/ben-shah/",
                    headline="ML Engineer",
                    comment_text="Very true",
                    time_ago="1h",
                ),
                _comment_row(
                    name="Cara Lee",
                    profile_url="/in/cara-lee/",
                    headline="Founder",
                    comment_text="Needed this",
                    time_ago="45m",
                ),
            ]
        )
        container = FakeNode(
            text="3 comments",
            locator_map={
                "article.comments-comment-item, article.comments-comment-entity, li.comments-comment-item, [data-test-id='comment-item'], [data-id^='urn:li:comment:']": comment_rows
            },
        )

        async def _resolve_optional_locator(group: str, key: str, page: Any) -> Any:
            del group, page
            if key == "container":
                return container
            if key == "load_more":
                return FakeLocator([])
            return None

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.extraction.engagement_rows._resolve_optional_locator",
            _resolve_optional_locator,
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.extraction.engagement_rows._extract_post_author_profile_url",
            AsyncMock(return_value="https://www.linkedin.com/in/op-author/"),
        )

        mcp = FastMCP("test")
        register_feed_tools(mcp)
        tool_fn = await get_tool_fn(mcp, "get_post_commenters")
        result = await tool_fn("urn:li:activity:123", limit=10)

        assert result["status"] == "success"
        assert len(result["data"]["results"]) == 3
        assert all(
            row["profile_url"] != "https://www.linkedin.com/in/op-author/"
            for row in result["data"]["results"]
        )

    async def test_get_post_commenters_uses_comment_specific_selectors(self):
        from linkedin_mcp_server.tools.extraction.engagement_rows import (
            _extract_comment_row,
        )

        row = _comment_row(
            name="Ava Singh",
            profile_url="/in/ava-singh/",
            headline="Staff Data Scientist",
            comment_text="Exact comment body",
            time_ago="2h",
        )

        result = await _extract_comment_row(row)

        assert result is not None
        assert result["comment_text"] == "Exact comment body"
        assert result["time_ago"] == "2h"

    async def test_get_post_commenters_skips_op_author(self):
        from linkedin_mcp_server.tools.extraction.engagement_rows import (
            _extract_comment_row,
        )

        row = _comment_row(
            name="Ayush Kumar",
            profile_url="/in/ayush-kumar/",
            headline="Author",
            comment_text="Self comment",
            time_ago="1h",
        )

        result = await _extract_comment_row(
            row,
            post_author_profile_url="https://www.linkedin.com/in/ayush-kumar/?miniProfileUrn=123",
        )

        assert result is None


class TestPostAnalyticsHelpers:
    def test_clean_member_name_strips_accessibility_noise(self):
        from linkedin_mcp_server.tools.extraction.activity_text import (
            _clean_member_name,
        )

        assert (
            _clean_member_name(
                "Divakar Vijayasarathy View Divakar Vijayasarathy’s profile 3rd degree connection · 3rd+"
            )
            == "Divakar Vijayasarathy"
        )
        assert (
            _clean_member_name(
                "Ayush Kumar Ayush Kumar • You Verified • You AI/ML Engineer"
            )
            == "Ayush Kumar"
        )

    @pytest.mark.asyncio
    async def test_load_reaction_modal_uses_modal_scoped_rows(self, monkeypatch):
        from linkedin_mcp_server.tools.extraction.engagement_rows import (
            _load_reaction_modal,
        )

        row_locator = FakeLocator([FakeNode(text="row 1"), FakeNode(text="row 2")])
        modal = FakeNode(
            text="2 reactions",
            locator_map={
                ".social-details-reactors-tab-body li": row_locator,
                "[role='listitem']": FakeLocator([]),
            },
        )

        monkeypatch.setattr("linkedin_mcp_server.tools.feed.asyncio.sleep", AsyncMock())

        result = await _load_reaction_modal(MagicMock(), modal, target_count=2)

        assert result["rows"] is row_locator
        assert result["loaded_count"] == 2
        assert result["total"] == 2
