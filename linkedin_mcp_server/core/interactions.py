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
    """Resolve an input/editor, focus it, and type text with per-char delay."""

    async def _type() -> None:
        locator = await locator_chain.find(page, timeout=timeout)
        await locator.click(timeout=timeout)
        await human_delay(120, 400)
        try:
            await locator.fill("", timeout=timeout)
        except Exception:
            # Contenteditable targets often do not support fill.
            pass
        await locator.type(text, delay=delay, timeout=timeout)
        await human_delay()

    await _with_retries(f"type:{locator_chain.name}", _type)


async def wait_for_modal(page: Page, timeout: int = 5000) -> None:
    """Wait for a LinkedIn Artdeco modal to become visible."""
    modal = page.locator(".artdeco-modal").first
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
