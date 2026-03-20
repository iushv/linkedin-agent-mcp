"""Reusable browser interaction primitives with retries and human-like delays."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Awaitable, Callable

from patchright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .exceptions import InteractionError
from .selectors import LocatorChain, SELECTORS


async def human_delay(min_ms: int = 500, max_ms: int = 1500) -> None:
    """Sleep for a random short duration to reduce bot-like timing patterns."""
    if min_ms < 0 or max_ms < 0:
        raise ValueError("Delay bounds must be non-negative")
    if min_ms > max_ms:
        min_ms, max_ms = max_ms, min_ms
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def _with_retries(
    op_name: str,
    operation: Callable[[], Awaitable[None]],
    retries: int = 2,
    retry_delay_ms: int = 350,
) -> None:
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            await operation()
            return
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            await asyncio.sleep(retry_delay_ms / 1000)

    raise InteractionError(
        f"Interaction failed for operation '{op_name}': {last_error}",
        action=op_name,
        context={"retries": retries},
    ) from last_error


async def click_element(
    page: Page, locator_chain: LocatorChain, timeout: int = 5000
) -> None:
    """Resolve and click an element using a resilient locator chain."""

    async def _click() -> None:
        locator = await locator_chain.find(page, timeout=timeout)
        await locator.click(timeout=timeout)
        await human_delay()

    await _with_retries(f"click:{locator_chain.name}", _click)


async def type_text(
    page: Page,
    locator_chain: LocatorChain,
    text: str,
    delay: int = 50,
    timeout: int = 5000,
) -> None:
    """Resolve an input/editor, focus it, and insert text.

    For short text (< 200 chars), uses keystroke simulation for realism.
    For longer text (>= 200 chars), injects via clipboard paste or JS
    to avoid Playwright's per-char timeout on large Unicode strings.
    """

    async def _type() -> None:
        locator = await locator_chain.find(page, timeout=timeout)
        await locator.click(timeout=timeout)
        await human_delay(120, 400)

        if len(text) < 200:
            # Short text — keystroke simulation is fine
            try:
                await locator.fill("", timeout=timeout)
            except Exception:
                pass
            await locator.type(text, delay=delay, timeout=timeout)
        else:
            # Long text — inject directly to avoid keystroke timeout.
            # Try fill() first (works on <input>/<textarea>), then
            # fall back to JS insertion for contenteditable editors.
            try:
                await locator.fill(text, timeout=timeout)
            except Exception:
                # Contenteditable — use execCommand('insertText') which
                # fires React's synthetic input events correctly.
                await page.evaluate(
                    """(text) => {
                        const el = document.querySelector(
                            '.ql-editor[contenteditable="true"], '
                            + '[contenteditable="true"][role="textbox"], '
                            + '[contenteditable="true"]'
                        );
                        if (el) {
                            el.focus();
                            document.execCommand('selectAll', false, null);
                            document.execCommand('insertText', false, text);
                        }
                    }""",
                    text,
                )
        await human_delay()

    await _with_retries(f"type:{locator_chain.name}", _type)


async def wait_for_modal(page: Page, timeout: int = 8000) -> None:
    """Wait for a LinkedIn modal or composer overlay to become visible."""
    # LinkedIn uses .artdeco-modal for traditional dialogs and
    # .share-creation-state / [role="dialog"] for the post composer overlay.
    modal = page.locator(".artdeco-modal, .share-creation-state, [role='dialog']").first
    try:
        await modal.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError as exc:
        raise InteractionError(
            "Timed out waiting for modal dialog",
            action="wait_for_modal",
            context={"timeout": timeout, "url": page.url},
        ) from exc


async def dismiss_modal(page: Page, timeout: int = 5000) -> bool:
    """Dismiss the currently active modal if present."""
    modal = page.locator(".artdeco-modal").first

    try:
        if await modal.is_visible(timeout=1000):
            await click_element(
                page, SELECTORS["common"]["dismiss_modal"], timeout=timeout
            )
            await modal.wait_for(state="hidden", timeout=timeout)
            return True
    except PlaywrightTimeoutError:
        return False
    except Exception as exc:
        raise InteractionError(
            f"Failed to dismiss modal: {exc}",
            action="dismiss_modal",
            context={"url": page.url},
        ) from exc

    return False


async def upload_file(
    page: Page,
    file_input_chain: LocatorChain,
    file_path: str,
    timeout: int = 5000,
) -> None:
    """Upload a file through a resolved file input."""
    path = Path(file_path).expanduser()
    if not path.exists() or not path.is_file():
        raise InteractionError(
            f"Upload file does not exist: {path}",
            action="upload_file",
            context={"path": str(path)},
        )

    async def _upload() -> None:
        locator = await file_input_chain.find(page, timeout=timeout)
        await locator.set_input_files(str(path), timeout=timeout)
        await human_delay(600, 1400)

    await _with_retries("upload_file", _upload)


async def click_and_confirm(
    page: Page,
    click_chain: LocatorChain,
    confirm_chain: LocatorChain,
    timeout: int = 5000,
) -> None:
    """Execute two-step actions such as delete + confirmation."""
    await click_element(page, click_chain, timeout=timeout)
    await human_delay(250, 650)
    await click_element(page, confirm_chain, timeout=timeout)
