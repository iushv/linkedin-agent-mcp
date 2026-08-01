"""Tests for core/selectors.py LocatorChain contracts."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_mcp_server.core.exceptions import SelectorError
from linkedin_mcp_server.core.selectors import (
    CSS,
    SELECTORS,
    AriaLabel,
    LocatorChain,
    LocatorStrategy,
    Role,
    Text,
)


def _make_strategy(*, resolves: bool, name: str = "test") -> Any:
    """Create a mock LocatorStrategy that either resolves or fails."""
    strategy = MagicMock(spec=LocatorStrategy)
    strategy.describe.return_value = name

    locator = MagicMock()
    if resolves:
        locator.count = AsyncMock(return_value=1)
        locator.first = locator
    else:
        locator.count = AsyncMock(return_value=0)

    strategy.locator.return_value = locator
    return strategy


class TestLocatorChain:
    @pytest.mark.asyncio
    async def test_first_match_wins(self):
        s1: Any = _make_strategy(resolves=True, name="s1")
        s2: Any = _make_strategy(resolves=True, name="s2")
        chain = LocatorChain(name="test", strategies=[s1, s2])

        page = MagicMock()
        page.url = "https://linkedin.com/in/user/"

        result = await chain.resolve(page)
        s1.locator.assert_called_once_with(page)
        s2.locator.assert_not_called()
        assert result is not None

    @pytest.mark.asyncio
    async def test_fallback_to_second(self):
        s1: Any = _make_strategy(resolves=False, name="s1")
        s2: Any = _make_strategy(resolves=True, name="s2")
        chain = LocatorChain(name="test", strategies=[s1, s2])

        page = MagicMock()
        page.url = "https://linkedin.com/in/user/"

        result = await chain.resolve(page)
        s1.locator.assert_called_once()
        s2.locator.assert_called_once()
        assert result is not None

    @pytest.mark.asyncio
    async def test_all_fail_raises_selector_error(self):
        s1 = _make_strategy(resolves=False, name="s1")
        s2 = _make_strategy(resolves=False, name="s2")
        chain = LocatorChain(name="test", strategies=[s1, s2])

        page = MagicMock()
        page.url = "https://linkedin.com/in/user/"
        # Mock collect_page_debug to avoid complex page interaction
        with pytest.raises(SelectorError):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    "linkedin_mcp_server.core.selectors.collect_page_debug",
                    AsyncMock(return_value={}),
                )
                await chain.resolve(page)

    @pytest.mark.asyncio
    async def test_failure_telemetry(self):
        s1 = _make_strategy(resolves=False, name="css:.test")
        s2 = _make_strategy(resolves=False, name="role:button")
        chain = LocatorChain(name="my_chain", strategies=[s1, s2])

        page = MagicMock()
        page.url = "https://linkedin.com/feed/"

        with pytest.raises(SelectorError) as exc_info:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    "linkedin_mcp_server.core.selectors.collect_page_debug",
                    AsyncMock(return_value={}),
                )
                await chain.resolve(page)

        err = exc_info.value
        assert err.context["tried_strategies"] == ["css:.test", "role:button"]
        assert err.context["url"] == "https://linkedin.com/feed/"
        assert err.context["chain_name"] == "my_chain"


@pytest.mark.parametrize(
    "group,key",
    [
        ("feed", "post_cards"),
        ("messaging", "conversation_items"),
        ("network", "invitation_rows"),
        ("post_composer", "trigger"),
        ("post_composer", "submit"),
        ("engagement", "like"),
        ("messaging", "send_button"),
        ("common", "dismiss_modal"),
    ],
)
def test_critical_chains_have_multiple_strategies(group: str, key: str):
    chain = SELECTORS[group][key]
    assert len(chain.strategies) >= 2, (
        f"{group}.{key} has only {len(chain.strategies)} strategy"
    )


@pytest.mark.parametrize(
    "group,key",
    [
        ("feed", "post_cards"),
        ("messaging", "conversation_items"),
        ("network", "invitation_rows"),
        ("post_composer", "trigger"),
        ("post_composer", "submit"),
        ("engagement", "like"),
        ("messaging", "send_button"),
        ("common", "dismiss_modal"),
    ],
)
def test_critical_chains_start_with_role_or_text(group: str, key: str):
    chain = SELECTORS[group][key]
    first = chain.strategies[0]
    assert isinstance(first, (Role, Text, AriaLabel))
    assert not isinstance(first, CSS)


def test_all_chain_names_unique():
    """No duplicate .name across the entire SELECTORS registry."""
    seen: dict[str, str] = {}
    for group_name, chains in SELECTORS.items():
        for key, chain in chains.items():
            fqn = f"{group_name}.{key}"
            assert chain.name not in seen, (
                f"Duplicate chain name '{chain.name}': {seen[chain.name]} vs {fqn}"
            )
            seen[chain.name] = fqn


def test_no_empty_strategy_lists():
    """Every registered chain must have at least one strategy."""
    for group_name, chains in SELECTORS.items():
        for key, chain in chains.items():
            assert len(chain.strategies) >= 1, (
                f"{group_name}.{key} has empty strategies list"
            )


class _FakeLocator:
    """Minimal locator: knows how many elements it represents and its id."""

    def __init__(self, name: str, count: int = 1):
        self.name = name
        self._count = count
        self.first = self

    async def count(self) -> int:
        return self._count


def _root(matches: dict[str, Any], url: str = "https://x/") -> Any:
    """Build a fake page/scope. Returns Any so it satisfies Page/Locator params."""
    return _FakeRoot(matches, url)


class _FakeRoot:
    """A page or scope that answers selector queries from a fixed map."""

    def __init__(self, matches: dict[str, _FakeLocator], url: str = "https://x/"):
        self._matches = matches
        self.url = url

    def locator(self, selector: str) -> _FakeLocator:
        return self._matches.get(selector, _FakeLocator(f"empty:{selector}", 0))

    def get_by_role(self, role: str, name: Any = None, exact: bool = False):
        key = f"role:{role}"
        return self._matches.get(key, _FakeLocator(f"empty:{key}", 0))

    def get_by_text(self, text: str, exact: bool = False):
        return _FakeLocator("empty:text", 0)

    def get_by_label(self, label: str, exact: bool = False):
        return _FakeLocator("empty:label", 0)


class TestScopedResolutionAndOrdering:
    """These must be separate tests.

    With the chain reordered specific-first, a single combined test passes
    without ever exercising the scope -- shipping scoping untested. Each test
    below fails if only the *other* fix is present.
    """

    @pytest.mark.asyncio
    async def test_scope_beats_a_stale_match_outside_the_dialog(self):
        """Proves scoping: the specific CSS also matches outside the dialog."""
        real = _FakeLocator("note-in-dialog")
        stale = _FakeLocator("stale-note-elsewhere")

        page = _root({"textarea#custom-message": stale})
        dialog = _root({"textarea#custom-message": real})

        chain_under_test = LocatorChain(
            name="network_note_input",
            strategies=[CSS("textarea#custom-message"), Role("textbox")],
        )

        found = await chain_under_test.find(page, scope=dialog)
        assert getattr(found, "name") == "note-in-dialog"

    @pytest.mark.asyncio
    async def test_specific_strategy_wins_over_generic_without_a_scope(self):
        """Proves ordering: no dialog present, generic would grab the search box."""
        note = _FakeLocator("note-field")
        search_box = _FakeLocator("global-search-box")

        page = _root({"textarea#custom-message": note, "role:textbox": search_box})

        chain_under_test = LocatorChain(
            name="network_note_input",
            strategies=[CSS("textarea#custom-message"), Role("textbox")],
        )

        found = await chain_under_test.find(page)
        assert getattr(found, "name") == "note-field"

    @pytest.mark.asyncio
    async def test_multi_match_emits_a_warning(self, caplog):
        """The signal that makes silent wrong-element selection visible."""
        page = _root({"role:textbox": _FakeLocator("ambiguous", count=3)})

        chain_under_test = LocatorChain(
            name="network_note_input", strategies=[Role("textbox")]
        )

        with caplog.at_level("WARNING"):
            await chain_under_test.find(page)

        assert "matched 3 elements" in caplog.text
        assert "network_note_input" in caplog.text

    @pytest.mark.asyncio
    async def test_single_match_is_silent(self):
        page = _root({"role:textbox": _FakeLocator("only-one", count=1)})
        chain_under_test = LocatorChain(
            name="quiet_chain", strategies=[Role("textbox")]
        )

        found = await chain_under_test.find(page)
        assert getattr(found, "name") == "only-one"


class TestNoteInputChainOrdering:
    def test_specific_css_precedes_generic_role(self):
        """Locks the ordering invariant for the chain that regressed."""
        strategies = SELECTORS["network"]["note_input"].strategies
        descriptions = [s.describe() for s in strategies]

        css_index = descriptions.index("css:textarea#custom-message")
        role_index = descriptions.index("role:textbox")

        assert css_index < role_index, (
            "a bare Role('textbox') before the specific CSS matches the global "
            "search box, which is earlier in the DOM"
        )
