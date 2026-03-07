"""Scenario-driven mock page/locator for browser IO testing."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock


class MockLocator:
    """A chainable mock Playwright locator that records actions."""

    def __init__(
        self,
        call_log: list[tuple[str, str]],
        name: str = "unknown",
        *,
        count_val: int = 1,
        text: str = "",
        href: str | None = None,
    ):
        self._log = call_log
        self._name = name
        self._count_val = count_val
        self._text = text
        self._href = href

    @property
    def first(self) -> "MockLocator":
        return self

    async def count(self) -> int:
        return self._count_val

    async def click(self, **kwargs: Any) -> None:
        self._log.append(("click", self._name))

    async def hover(self, **kwargs: Any) -> None:
        self._log.append(("hover", self._name))

    async def type(self, text: str, **kwargs: Any) -> None:
        self._log.append(("type", self._name))

    async def fill(self, text: str, **kwargs: Any) -> None:
        self._log.append(("fill", self._name))

    async def inner_text(self, **kwargs: Any) -> str:
        return self._text

    async def get_attribute(self, attr: str, **kwargs: Any) -> str | None:
        if attr == "href":
            return self._href
        return None

    async def set_input_files(self, path: str, **kwargs: Any) -> None:
        self._log.append(("upload", self._name))

    async def is_visible(self, **kwargs: Any) -> bool:
        return self._count_val > 0

    async def wait_for(self, **kwargs: Any) -> None:
        pass

    def nth(self, n: int) -> "MockLocator":
        return self

    def locator(self, selector: str) -> "MockLocator":
        return MockLocator(self._log, name=f"{self._name}>{selector}")


class MockPageBuilder:
    """Fluent builder for a scenario-driven mock Page object.

    Registers expected browser interactions in order. The built page
    routes selector lookups and navigations through the registered
    scenarios, recording every action for post-hoc assertion.
    """

    def __init__(self) -> None:
        self._call_log: list[tuple[str, str]] = []
        self._current_url = "about:blank"
        self._role_locators: dict[str, MockLocator] = {}
        self._default_locator_count = 0
        self._modal_visible = False
        self._post_url: str | None = None

    def on_goto(self, url: str) -> "MockPageBuilder":
        self._current_url = url
        return self

    def on_role(
        self, role: str, name: str, *, count: int = 1
    ) -> "MockPageBuilder":
        key = f"{role}:{name}"
        self._role_locators[key] = MockLocator(
            self._call_log, name=key, count_val=count
        )
        return self

    def with_modal(self, visible: bool = True) -> "MockPageBuilder":
        self._modal_visible = visible
        return self

    def with_post_url(self, url: str) -> "MockPageBuilder":
        self._post_url = url
        return self

    def build(self) -> MagicMock:
        log = self._call_log
        role_map = self._role_locators
        default_count = self._default_locator_count
        current_url = self._current_url
        modal_visible = self._modal_visible
        post_url = self._post_url

        page = MagicMock()

        # page.url
        page.url = current_url

        # page.goto
        async def _goto(url: str, **kwargs: Any) -> None:
            nonlocal current_url
            log.append(("goto", url))
            current_url = url
            page.url = url

        page.goto = AsyncMock(side_effect=_goto)

        # page.locator
        def _locator(selector: str) -> MockLocator:
            # Check for modal
            if "artdeco-modal" in selector:
                return MockLocator(
                    log,
                    name="modal",
                    count_val=1 if modal_visible else 0,
                )
            # Check feed update link
            if 'href*="/feed/update/"' in selector:
                if post_url:
                    return MockLocator(
                        log, name="post_link", count_val=1, href=post_url
                    )
                return MockLocator(log, name="post_link", count_val=0)
            # Check for snackbar/toast
            if "alert" in selector or "toast" in selector or "feedback" in selector:
                return MockLocator(
                    log, name="snackbar", count_val=0, text=""
                )
            # Rate-limit selectors
            if "captcha" in selector.lower():
                return MockLocator(log, name="captcha", count_val=0)
            if "data-test-id" in selector:
                return MockLocator(log, name="challenge", count_val=0)
            if selector == "main":
                return MockLocator(log, name="main", count_val=1)
            if selector == "body":
                return MockLocator(log, name="body", count_val=1, text="normal page")
            return MockLocator(
                log, name=f"css:{selector}", count_val=default_count
            )

        page.locator = MagicMock(side_effect=_locator)

        # page.get_by_role
        def _get_by_role(role: str, name: str = "", **kwargs: Any) -> MockLocator:
            key = f"{role}:{name}"
            if key in role_map:
                return role_map[key]
            return MockLocator(log, name=key, count_val=0)

        page.get_by_role = MagicMock(side_effect=_get_by_role)

        # page.get_by_text
        def _get_by_text(text: str, **kwargs: Any) -> MockLocator:
            return MockLocator(log, name=f"text:{text}", count_val=0)

        page.get_by_text = MagicMock(side_effect=_get_by_text)

        # Attach log for assertions
        page.call_log = log

        return page
