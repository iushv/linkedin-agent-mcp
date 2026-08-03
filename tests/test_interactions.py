"""Tests for core/interactions.py browser primitives."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from linkedin_mcp_server.core.exceptions import InteractionError


# ---------------------------------------------------------------------------
# human_delay
# ---------------------------------------------------------------------------


class TestHumanDelay:
    @pytest.mark.asyncio
    async def test_calls_sleep_in_bounds(self, monkeypatch):
        from linkedin_mcp_server.core import interactions

        captured: list[float] = []

        async def _fake_sleep(secs):
            captured.append(secs)

        monkeypatch.setattr("asyncio.sleep", _fake_sleep)
        monkeypatch.setattr("random.uniform", lambda a, b: (a + b) / 2)

        await interactions.human_delay(100, 200)

        assert len(captured) == 1
        assert 0.1 <= captured[0] <= 0.2

    @pytest.mark.asyncio
    async def test_negative_raises(self):
        from linkedin_mcp_server.core.interactions import human_delay

        with pytest.raises(ValueError):
            await human_delay(-1, 100)


# ---------------------------------------------------------------------------
# _with_retries
# ---------------------------------------------------------------------------


class TestWithRetries:
    @pytest.mark.asyncio
    async def test_succeeds_on_second(self, monkeypatch):
        from linkedin_mcp_server.core.interactions import _with_retries

        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        call_count = 0

        async def _op():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("transient")

        await _with_retries("test_op", _op, retries=2, retry_delay_ms=0)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_raises(self, monkeypatch):
        from linkedin_mcp_server.core.interactions import _with_retries

        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        async def _always_fail():
            raise RuntimeError("permanent")

        with pytest.raises(InteractionError, match="Interaction failed"):
            await _with_retries("test_op", _always_fail, retries=1, retry_delay_ms=0)


# ---------------------------------------------------------------------------
# click_element
# ---------------------------------------------------------------------------


def _make_mock_page_and_chain(*, resolves: bool = True):
    """Create mock page + LocatorChain that either resolves or fails."""
    mock_locator = MagicMock()
    mock_locator.click = AsyncMock()
    mock_locator.count = AsyncMock(return_value=1 if resolves else 0)
    mock_locator.first = mock_locator

    chain = MagicMock()
    chain.name = "test_chain"
    if resolves:
        chain.find = AsyncMock(return_value=mock_locator)
    else:
        from linkedin_mcp_server.core.exceptions import SelectorError

        chain.find = AsyncMock(
            side_effect=SelectorError(
                "fail", chain_name="test", tried_strategies=["a"], url="http://x"
            )
        )

    page = MagicMock()
    return page, chain, mock_locator


class TestClickElement:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        from linkedin_mcp_server.core.interactions import click_element

        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        page, chain, locator = _make_mock_page_and_chain(resolves=True)

        await click_element(page, chain, timeout=500)
        locator.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_raises_interaction_error(self, monkeypatch):
        from linkedin_mcp_server.core.interactions import click_element

        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        page, chain, _ = _make_mock_page_and_chain(resolves=False)

        with pytest.raises(InteractionError):
            await click_element(page, chain, timeout=500)


# ---------------------------------------------------------------------------
# type_text
# ---------------------------------------------------------------------------


class TestTypeText:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        from linkedin_mcp_server.core.interactions import type_text

        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        page, chain, locator = _make_mock_page_and_chain(resolves=True)
        locator.fill = AsyncMock()
        locator.type = AsyncMock()

        await type_text(page, chain, "hello", delay=0, timeout=500)
        locator.type.assert_awaited_once_with("hello", delay=0, timeout=500)


# ---------------------------------------------------------------------------
# wait_for_modal
# ---------------------------------------------------------------------------


class TestWaitForModal:
    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        from patchright.async_api import TimeoutError as PwTimeout

        from linkedin_mcp_server.core.interactions import wait_for_modal

        modal = MagicMock()
        modal.wait_for = AsyncMock(side_effect=PwTimeout("timed out"))

        page = MagicMock()
        page.locator.return_value.first = modal

        with pytest.raises(InteractionError, match="Timed out waiting for modal"):
            await wait_for_modal(page, timeout=100)


# ---------------------------------------------------------------------------
# dismiss_modal
# ---------------------------------------------------------------------------


class TestDismissModal:
    @staticmethod
    def _make_button(*, visible: bool, count: int = 1):
        button = MagicMock()
        button.first = button
        button.count = AsyncMock(return_value=count)
        button.is_visible = AsyncMock(return_value=visible)
        button.click = AsyncMock()
        return button

    @pytest.mark.asyncio
    async def test_closes_visible_modal(self, monkeypatch):
        from linkedin_mcp_server.core.interactions import dismiss_modal

        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        modal = MagicMock()
        modal.is_visible = AsyncMock(return_value=True)
        modal.wait_for = AsyncMock()
        dismiss_button = self._make_button(visible=True)
        missing_button = self._make_button(visible=False, count=0)

        def _modal_locator(selector: str):
            if selector == "button.artdeco-modal__dismiss":
                return dismiss_button
            return missing_button

        def _modal_get_by_label(label: str, exact: bool = True):
            del exact
            if label in {"Dismiss", "Close"}:
                return missing_button
            return missing_button

        modal.locator = MagicMock(side_effect=_modal_locator)
        modal.get_by_label = MagicMock(side_effect=_modal_get_by_label)

        page = MagicMock()
        page.locator.return_value.first = modal

        result = await dismiss_modal(page, timeout=500)
        assert result is True
        dismiss_button.click.assert_awaited_once_with(timeout=500)

    @pytest.mark.asyncio
    async def test_skips_hidden_dismiss_button(self):
        from linkedin_mcp_server.core.interactions import dismiss_modal

        modal = MagicMock()
        modal.is_visible = AsyncMock(return_value=True)
        modal.wait_for = AsyncMock()
        hidden_dismiss = self._make_button(visible=False, count=1)
        missing_button = self._make_button(visible=False, count=0)

        def _modal_locator(selector: str):
            if selector == "button.artdeco-modal__dismiss":
                return missing_button
            if selector == "button[aria-label='Dismiss']":
                return hidden_dismiss
            return missing_button

        def _modal_get_by_label(label: str, exact: bool = True):
            del exact
            if label == "Dismiss":
                return hidden_dismiss
            return missing_button

        modal.locator = MagicMock(side_effect=_modal_locator)
        modal.get_by_label = MagicMock(side_effect=_modal_get_by_label)

        page = MagicMock()
        page.locator.return_value.first = modal

        result = await dismiss_modal(page, timeout=500)

        assert result is False
        hidden_dismiss.click.assert_not_awaited()
        modal.wait_for.assert_not_awaited()


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------


class TestUploadFile:
    @pytest.mark.asyncio
    async def test_nonexistent_raises(self):
        from linkedin_mcp_server.core.interactions import upload_file

        page = MagicMock()
        chain = MagicMock()

        with pytest.raises(InteractionError, match="does not exist"):
            await upload_file(page, chain, "/nonexistent/file.png")


# ---------------------------------------------------------------------------
# click_and_confirm
# ---------------------------------------------------------------------------


class TestClickAndConfirm:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        from linkedin_mcp_server.core.interactions import click_and_confirm

        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        click_chain = MagicMock()
        confirm_chain = MagicMock()
        page = MagicMock()

        with patch(
            "linkedin_mcp_server.core.interactions.click_element",
            new_callable=AsyncMock,
        ) as mock_click:
            await click_and_confirm(page, click_chain, confirm_chain, timeout=500)

        assert mock_click.await_count == 2
        calls = mock_click.call_args_list
        assert calls[0].args[1] is click_chain
        assert calls[1].args[1] is confirm_chain

    @pytest.mark.asyncio
    async def test_first_fails(self, monkeypatch):
        from linkedin_mcp_server.core.interactions import click_and_confirm

        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        click_chain = MagicMock()
        confirm_chain = MagicMock()
        page = MagicMock()

        with (
            patch(
                "linkedin_mcp_server.core.interactions.click_element",
                new_callable=AsyncMock,
                side_effect=InteractionError("click failed", action="click"),
            ),
            pytest.raises(InteractionError, match="click failed"),
        ):
            await click_and_confirm(page, click_chain, confirm_chain)


class TestTypeTimeoutCoversKeystrokes:
    """The typing timeout must cover the whole keystroke sequence.

    At the defaults (50ms/char, 5000ms) anything over 100 characters could
    never finish, while the fill() fast path only engages at 200 -- so every
    note between 100 and 199 characters was a guaranteed timeout. A real
    129-character invite note needs 6450ms and was given 5000.
    """

    @pytest.mark.asyncio
    async def test_long_note_gets_a_budget_that_fits(self):
        from linkedin_mcp_server.core.interactions import type_text

        note = "x" * 129
        locator = MagicMock()
        locator.click = AsyncMock()
        locator.fill = AsyncMock()
        locator.type = AsyncMock()

        chain = MagicMock()
        chain.name = "network_note_input"
        chain.find = AsyncMock(return_value=locator)

        with patch(
            "linkedin_mcp_server.core.interactions.human_delay", new=AsyncMock()
        ):
            await type_text(MagicMock(), chain, note)

        assert locator.type.await_args is not None
        granted = locator.type.await_args.kwargs["timeout"]
        required = len(note) * 50
        assert granted > required, (
            f"{len(note)} chars need {required}ms but only {granted}ms was allowed"
        )

    @pytest.mark.asyncio
    async def test_short_note_keeps_the_default_floor(self):
        from linkedin_mcp_server.core.interactions import type_text

        locator = MagicMock()
        locator.click = AsyncMock()
        locator.fill = AsyncMock()
        locator.type = AsyncMock()

        chain = MagicMock()
        chain.name = "network_note_input"
        chain.find = AsyncMock(return_value=locator)

        with patch(
            "linkedin_mcp_server.core.interactions.human_delay", new=AsyncMock()
        ):
            await type_text(MagicMock(), chain, "hi")

        assert locator.type.await_args is not None
        assert locator.type.await_args.kwargs["timeout"] >= 5000

    @pytest.mark.asyncio
    async def test_every_length_in_the_dead_zone_is_survivable(self):
        """100-199 chars was the band that always failed."""
        from linkedin_mcp_server.core.interactions import type_text

        for length in (100, 129, 150, 199):
            locator = MagicMock()
            locator.click = AsyncMock()
            locator.fill = AsyncMock()
            locator.type = AsyncMock()

            chain = MagicMock()
            chain.name = "chain"
            chain.find = AsyncMock(return_value=locator)

            with patch(
                "linkedin_mcp_server.core.interactions.human_delay", new=AsyncMock()
            ):
                await type_text(MagicMock(), chain, "y" * length)

            assert locator.type.await_args is not None
            granted = locator.type.await_args.kwargs["timeout"]
            assert granted > length * 50, f"{length} chars still under-budgeted"
