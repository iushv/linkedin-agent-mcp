"""Diagnostic utility for LinkedIn feed identifier extraction drift.

Usage:
  uv run python scripts/diag_dom.py
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from patchright.async_api import Page, async_playwright

from linkedin_mcp_server.tools.feed import _extract_post_identifier


DEFAULT_PROFILE_DIR = Path("~/.linkedin-mcp/profile").expanduser()
MAX_ANCESTOR_DEPTH = 6
MAX_DESCENDANTS = 80
MAX_ANCHORS = 20
SCRIPT_WINDOW_CHARS = 4000


@dataclass
class StrategyStats:
    strategy_1_permalink: int = 0
    strategy_2_ancestor_urn: int = 0
    strategy_3_descendant_urn: int = 0
    strategy_4_broad_anchor: int = 0
    strategy_5_hydration_state: int = 0
    no_match: int = 0


def classify_card(text: str) -> str:
    normalized = " ".join(text.strip().lower().split())
    head = normalized[:240]
    if "sponsored" in head:
        return "sponsored"
    if "promoted" in head:
        return "promoted"
    if "suggested for you" in head or head.startswith("suggested"):
        return "suggested"
    return "regular"


def is_engagement_actionable(identifier: dict[str, Any]) -> bool:
    post_urn = identifier.get("post_urn")
    if isinstance(post_urn, str) and post_urn:
        return True
    url = identifier.get("url")
    if not isinstance(url, str) or not url:
        return False
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    return "/feed/update/" in path or "/activity-" in path


async def resolve_cards(page: Page):
    candidates = [
        page.get_by_role("listitem").filter(
            has=page.get_by_role("heading", name="Feed post")
        ),
        page.locator("[role='listitem']:has(h2:has-text('Feed post'))"),
        page.locator("main article"),
        page.locator("main [role='article']"),
    ]
    for locator in candidates:
        try:
            if await locator.count() > 0:
                return locator
        except Exception:
            continue
    return None


async def strategy_1_permalink(card: Any) -> list[str]:
    selectors = (
        "a[href*='/feed/update/']",
        "a[href*='/posts/']",
        "a[href*='/activity-']",
        "a[href*='urn%3Ali%3Aactivity']",
        "a[data-tracking-control-name*='update']",
    )
    hits: list[str] = []
    for selector in selectors:
        try:
            loc = card.locator(selector)
            if await loc.count() == 0:
                continue
            href = await loc.first.get_attribute("href", timeout=400)
            if href:
                hits.append(f"{selector} => {href}")
        except Exception:
            continue
    return hits


async def strategy_2_ancestor(card: Any) -> list[dict[str, Any]]:
    return await card.evaluate(
        """(el, maxDepth) => {
          const attrs = ["data-urn", "data-id", "data-activity-urn", "data-update-id", "data-test-id", "href"];
          const urnRe = /urn:li:activity:\\d+/;
          const rows = [];
          let current = el;
          for (let depth = 0; current && depth <= maxDepth; depth += 1) {
            const entry = { depth, attrs: {}, urn_hits: [] };
            for (const attrName of attrs) {
              const value = current.getAttribute?.(attrName);
              if (!value) continue;
              entry.attrs[attrName] = value;
              const m = String(value).match(urnRe);
              if (m) entry.urn_hits.push({ attr: attrName, urn: m[0] });
            }
            for (const attrName of current.getAttributeNames?.() || []) {
              if (!attrName.startsWith("data-")) continue;
              if (entry.attrs[attrName]) continue;
              const value = current.getAttribute(attrName);
              if (!value) continue;
              entry.attrs[attrName] = value;
              const m = String(value).match(urnRe);
              if (m) entry.urn_hits.push({ attr: attrName, urn: m[0] });
            }
            rows.push(entry);
            current = current.parentElement;
          }
          return rows;
        }""",
        MAX_ANCESTOR_DEPTH,
    )


async def strategy_3_descendant(card: Any) -> dict[str, Any]:
    return await card.evaluate(
        """(el, maxNodes) => {
          const attrs = ["href", "data-urn", "data-id", "data-activity-urn", "data-update-id", "data-test-id", "aria-label"];
          const urnRe = /urn:li:activity:\\d+/;
          const hits = [];
          const dataAttrs = {};
          const nodes = [el, ...el.querySelectorAll("*")].slice(0, maxNodes);
          for (const node of nodes) {
            for (const attrName of attrs) {
              const value = node.getAttribute?.(attrName);
              if (!value) continue;
              const m = String(value).match(urnRe);
              if (m) hits.push({ attr: attrName, urn: m[0], value: String(value).slice(0, 200) });
            }
            for (const attrName of node.getAttributeNames?.() || []) {
              if (!attrName.startsWith("data-")) continue;
              const value = node.getAttribute(attrName);
              if (!value) continue;
              dataAttrs[attrName] = (dataAttrs[attrName] || 0) + 1;
            }
          }
          return { hits, data_attrs: dataAttrs };
        }""",
        MAX_DESCENDANTS,
    )


async def strategy_4_broad_anchor(card: Any) -> dict[str, Any]:
    anchors: list[dict[str, str]] = []
    patterns = ("/feed/update/", "/posts/", "/activity-", "urn%3Ali%3Aactivity")
    try:
        loc = card.locator("a[href]")
        count = min(await loc.count(), MAX_ANCHORS)
        for idx in range(count):
            href = await loc.nth(idx).get_attribute("href", timeout=400)
            if not href:
                continue
            if any(pattern in href for pattern in patterns):
                anchors.append({"index": str(idx), "href": href[:300]})
    except Exception:
        pass
    return {"matches": anchors}


async def strategy_5_hydration_state(card: Any) -> dict[str, Any]:
    return await card.evaluate(
        """(el, windowChars) => {
          const urnRe = /urn:li:activity:\\d+/g;
          const ignoreLine = /^(?:\\d+[smhdw]|\\d+\\s*(?:m|h|d|w|mo|yr)s?\\s*ago|follow|like|comment|repost|send|promoted|sponsored)$/i;
          const tokens = (el.innerText || "")
            .split(/\\n+/)
            .map((line) => line.replace(/\\s+/g, " ").trim().toLowerCase())
            .filter((line) => line.length >= 24 && line.length <= 160 && !ignoreLine.test(line))
            .slice(0, 3);

          const scripts = Array.from(document.querySelectorAll("script:not([src])"))
            .map((node) => node.textContent || "")
            .filter((text) => text.includes("urn:li:activity:"))
            .slice(0, 40)
            .map((text) => (text.length > 800000 ? text.slice(0, 800000) : text));

          const urnHits = new Set();
          let tokenMatchCount = 0;
          for (const blob of scripts) {
            const lower = blob.toLowerCase();
            for (const token of tokens) {
              const idx = lower.indexOf(token);
              if (idx === -1) continue;
              tokenMatchCount += 1;
              const start = Math.max(0, idx - windowChars);
              const end = Math.min(blob.length, idx + token.length + windowChars);
              const slice = blob.slice(start, end);
              const urns = slice.match(urnRe);
              if (!urns) continue;
              for (const urn of urns) urnHits.add(urn);
            }
          }

          return {
            token_count: tokens.length,
            token_match_count: tokenMatchCount,
            scripts_with_activity: scripts.length,
            urn_hits: Array.from(urnHits).slice(0, 10),
          };
        }""",
        SCRIPT_WINDOW_CHARS,
    )


async def inspect_feed(page: Page, count: int) -> None:
    print("\n=== FEED IDENTIFIER DIAGNOSTICS ===\n")
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    await asyncio.sleep(5)

    cards = await resolve_cards(page)
    if cards is None:
        print("No feed cards found with current selectors.")
        return

    total_cards = min(await cards.count(), count)
    print(f"Inspecting {total_cards} card(s)\n")

    stats = StrategyStats()
    type_counts = {"regular": 0, "promoted": 0, "sponsored": 0, "suggested": 0}
    actionable_total = 0
    regular_actionable = 0
    regular_total = 0
    engagement_actionable_total = 0
    regular_engagement_actionable = 0

    for idx in range(total_cards):
        card = cards.nth(idx)
        try:
            text = await card.inner_text(timeout=2000)
        except Exception:
            text = ""

        card_type = classify_card(text)
        type_counts[card_type] = type_counts.get(card_type, 0) + 1
        if card_type == "regular":
            regular_total += 1

        strategy1_hits = await strategy_1_permalink(card)
        strategy2_dump = await strategy_2_ancestor(card)
        strategy3_dump = await strategy_3_descendant(card)
        strategy4_dump = await strategy_4_broad_anchor(card)
        strategy5_dump = await strategy_5_hydration_state(card)
        identifier = await _extract_post_identifier(card)
        actionable = (
            identifier.get("url") is not None or identifier.get("post_urn") is not None
        )
        engagement_actionable = is_engagement_actionable(identifier)
        actionable_total += 1 if actionable else 0
        engagement_actionable_total += 1 if engagement_actionable else 0
        if card_type == "regular" and actionable:
            regular_actionable += 1
        if card_type == "regular" and engagement_actionable:
            regular_engagement_actionable += 1

        strategy_hit = "no_match"
        if strategy1_hits:
            stats.strategy_1_permalink += 1
            strategy_hit = "strategy_1_permalink"
        elif any(entry.get("urn_hits") for entry in strategy2_dump):
            stats.strategy_2_ancestor_urn += 1
            strategy_hit = "strategy_2_ancestor_urn"
        elif strategy3_dump.get("hits"):
            stats.strategy_3_descendant_urn += 1
            strategy_hit = "strategy_3_descendant_urn"
        elif strategy4_dump.get("matches"):
            stats.strategy_4_broad_anchor += 1
            strategy_hit = "strategy_4_broad_anchor"
        elif strategy5_dump.get("urn_hits"):
            stats.strategy_5_hydration_state += 1
            strategy_hit = "strategy_5_hydration_state"
        else:
            stats.no_match += 1

        print(f"Card #{idx + 1}")
        print(f"  card_type: {card_type}")
        print(f"  strategy_hit: {strategy_hit}")
        print(f"  identifier: {identifier}")
        if strategy1_hits:
            print("  strategy_1 hits:")
            for hit in strategy1_hits[:3]:
                print(f"    - {hit}")
        ancestor_urns = [
            urn_hit for entry in strategy2_dump for urn_hit in entry.get("urn_hits", [])
        ]
        print(f"  strategy_2 ancestor urn hits: {len(ancestor_urns)}")
        print(
            f"  strategy_3 descendant urn hits: {len(strategy3_dump.get('hits', []))}"
        )
        print(f"  strategy_4 anchor matches: {len(strategy4_dump.get('matches', []))}")
        print(
            "  strategy_5 hydration state: "
            f"scripts={strategy5_dump.get('scripts_with_activity', 0)}, "
            f"token_matches={strategy5_dump.get('token_match_count', 0)}, "
            f"urn_hits={len(strategy5_dump.get('urn_hits', []))}"
        )
        print()

    print("\n=== SUMMARY ===")
    print(f"Total cards: {total_cards}")
    print(
        "Card types: "
        f"regular={type_counts.get('regular', 0)}, "
        f"promoted={type_counts.get('promoted', 0)}, "
        f"sponsored={type_counts.get('sponsored', 0)}, "
        f"suggested={type_counts.get('suggested', 0)}"
    )
    print(f"Actionable (url OR post_urn): {actionable_total}/{total_cards}")
    print(
        "Engagement actionable "
        "(post_urn OR /feed/update/ OR /activity- URL): "
        f"{engagement_actionable_total}/{total_cards}"
    )
    print(
        "Regular actionable: "
        f"{regular_actionable}/{regular_total if regular_total > 0 else 0}"
    )
    print(
        "Regular engagement actionable: "
        f"{regular_engagement_actionable}/{regular_total if regular_total > 0 else 0}"
    )
    print("Strategy hit distribution:")
    print(f"  strategy_1_permalink: {stats.strategy_1_permalink}")
    print(f"  strategy_2_ancestor_urn: {stats.strategy_2_ancestor_urn}")
    print(f"  strategy_3_descendant_urn: {stats.strategy_3_descendant_urn}")
    print(f"  strategy_4_broad_anchor: {stats.strategy_4_broad_anchor}")
    print(f"  strategy_5_hydration_state: {stats.strategy_5_hydration_state}")
    print(f"  no_match: {stats.no_match}")


async def run(profile_dir: Path, count: int, headless: bool) -> None:
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            viewport={"width": 1440, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await inspect_feed(page, count=count)
        finally:
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose LinkedIn feed identifier extraction drift."
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help=f"Persistent browser profile directory (default: {DEFAULT_PROFILE_DIR})",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Maximum number of feed cards to inspect.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run with visible browser window for debugging.",
    )
    args = parser.parse_args()
    asyncio.run(
        run(
            profile_dir=args.profile_dir.expanduser(),
            count=max(1, args.count),
            headless=not args.headed,
        )
    )


if __name__ == "__main__":
    main()
