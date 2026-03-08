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
