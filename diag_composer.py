"""
Diagnostic: test mouse.click + pointer events on LinkedIn composer trigger.
Usage: python3 diag_composer.py
"""

import asyncio
import json
from patchright.async_api import async_playwright

PROFILE = "/Users/ayushkumar/.linkedin-mcp/profile"
FEED_URL = "https://www.linkedin.com/feed/"

EDITOR_SELECTORS = (
    ".artdeco-modal .ql-editor, "
    ".share-creation-state .ql-editor, "
    "[role='dialog'] .ql-editor, "
    ".ql-editor[contenteditable='true'], "
    "[contenteditable='true']"
)


async def wait_for_editor(page, timeout_ms=8000):
    try:
        await page.wait_for_selector(EDITOR_SELECTORS, timeout=timeout_ms)
        return True
    except Exception:
        return False


async def check_editor_present(page):
    for sel in [
        ".ql-editor",
        "[contenteditable='true']",
        "[role='textbox']",
        "[role='dialog']",
        ".artdeco-modal",
        ".share-creation-state",
    ]:
        try:
            n = await page.locator(sel).count()
            if n > 0:
                return sel, n
        except Exception:
            pass
    return None, 0


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            args=["--no-sandbox"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        print("Navigating to feed (domcontentloaded)...")
        await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=30000)
        print("Waiting 6s for React to fully initialize...")
        await asyncio.sleep(6)

        # Scroll to top
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)

        # Find the trigger element
        trigger = (
            page.locator("div[role='button']").filter(has_text="Start a post").first
        )
        count = await trigger.count()
        print(f"Trigger count: {count}")

        if count == 0:
            print("ERROR: trigger not found — checking page URL:", page.url)
            await browser.close()
            return

        box = await trigger.bounding_box()
        print(f"Bounding box: {box}")
        visible = await trigger.is_visible()
        print(f"Visible: {visible}")

        # --- Attempt 1: page.mouse.click() at center of element ---
        print("\n=== Attempt 1: page.mouse.click() ===")
        if box:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            await page.mouse.move(cx, cy)
            await asyncio.sleep(0.3)
            await page.mouse.click(cx, cy)
            print(f"mouse.click({cx:.1f}, {cy:.1f}) done — waiting 4s...")
            await asyncio.sleep(4)
            sel, n = await check_editor_present(page)
            if sel:
                print(f"SUCCESS (attempt 1): found {n}× '{sel}'")
                await browser.close()
                return
            else:
                print("No editor after mouse.click — continuing")

        # --- Attempt 2: full pointer event sequence via JS ---
        print("\n=== Attempt 2: dispatchEvent pointer sequence ===")
        result = await page.evaluate("""() => {
            const el = [...document.querySelectorAll('div[role="button"]')]
                .find(e => e.textContent.includes('Start a post'));
            if (!el) return {error: 'element not found'};
            const rect = el.getBoundingClientRect();
            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            const opts = {bubbles:true, cancelable:true, clientX:cx, clientY:cy};
            el.dispatchEvent(new PointerEvent('pointerover', opts));
            el.dispatchEvent(new PointerEvent('pointerenter', opts));
            el.dispatchEvent(new MouseEvent('mouseover', opts));
            el.dispatchEvent(new MouseEvent('mouseenter', opts));
            el.dispatchEvent(new MouseEvent('mousemove', opts));
            el.dispatchEvent(new PointerEvent('pointerdown', opts));
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new PointerEvent('pointerup', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
            el.focus();
            return {fired: true, tag: el.tagName, text: el.textContent.trim().slice(0,60)};
        }""")
        print(f"dispatchEvent result: {result}")
        await asyncio.sleep(4)
        sel, n = await check_editor_present(page)
        if sel:
            print(f"SUCCESS (attempt 2): found {n}× '{sel}'")
            await browser.close()
            return
        else:
            print("No editor after pointer events — continuing")

        # --- Attempt 3: click a child element (span/img inside the button) ---
        print("\n=== Attempt 3: click child element ===")
        child_result = await page.evaluate("""() => {
            const el = [...document.querySelectorAll('div[role="button"]')]
                .find(e => e.textContent.includes('Start a post'));
            if (!el) return {error: 'not found'};
            const children = [...el.querySelectorAll('*')];
            const info = children.slice(0,5).map(c => ({
                tag: c.tagName, cls: c.className.toString().slice(0,60),
                text: c.textContent?.trim().slice(0,40)
            }));
            // Click the deepest span child
            const span = el.querySelector('span') || el.querySelector('div') || el;
            const rect = span.getBoundingClientRect();
            return {
                children_info: info,
                clicked_tag: span.tagName,
                cx: rect.left + rect.width/2,
                cy: rect.top + rect.height/2
            };
        }""")
        print(f"Child info: {json.dumps(child_result, indent=2)}")
        if "cx" in child_result and "cy" in child_result:
            await page.mouse.click(child_result["cx"], child_result["cy"])
            await asyncio.sleep(4)
            sel, n = await check_editor_present(page)
            if sel:
                print(f"SUCCESS (attempt 3 child click): found {n}× '{sel}'")
                await browser.close()
                return
            else:
                print("No editor after child click")

        # --- Attempt 4: look for an alternative trigger (textarea, placeholder) ---
        print("\n=== Attempt 4: look for placeholder/input-based trigger ===")
        alt_result = await page.evaluate("""() => {
            const candidates = [
                ...document.querySelectorAll('[placeholder*="post" i]'),
                ...document.querySelectorAll('[data-placeholder*="post" i]'),
                ...document.querySelectorAll('button[aria-label*="post" i]'),
                ...document.querySelectorAll('[aria-label*="Start a post" i]'),
            ];
            return candidates.slice(0,5).map(e => ({
                tag: e.tagName, type: e.type, role: e.getAttribute('role'),
                placeholder: e.getAttribute('placeholder') || e.getAttribute('data-placeholder'),
                ariaLabel: e.getAttribute('aria-label'),
                cls: e.className?.toString().slice(0,80)
            }));
        }""")
        print(f"Alt triggers: {json.dumps(alt_result, indent=2)}")

        # --- Final: dump all role=button elements near top of page ---
        print("\n=== All role=button elements (first 10) ===")
        buttons = await page.evaluate("""() => {
            const els = [...document.querySelectorAll('[role="button"]')];
            return els.slice(0,10).map(e => ({
                tag: e.tagName,
                text: e.textContent?.trim().slice(0,60),
                ariaLabel: e.getAttribute('aria-label'),
                cls: e.className?.toString().slice(0,60)
            }));
        }""")
        for b in buttons:
            print(b)

        print("\nDiagnostic complete.")
        await asyncio.sleep(2)
        await browser.close()


asyncio.run(main())
