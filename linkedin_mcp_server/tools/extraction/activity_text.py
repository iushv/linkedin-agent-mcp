"""Pure-text parsers for LinkedIn feed and activity content.

No Playwright dependencies — everything here parses innerText snapshots,
so it can be tested with plain string fixtures.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from linkedin_mcp_server.tools._common import parse_count


_CARD_TYPE_SUGGESTED_MARKERS = ("suggested for you",)


_NON_ACTIONABLE_CARD_TYPES = {
    "announcement",
    "newsletter",
    "promoted",
    "sponsored",
    "suggested",
}


_NUM_IN_TEXT_RE = re.compile(r"([\d,.kKmM]+)")


_TIME_AGO_RE = re.compile(r"^\d+\s*(?:m|h|d|w|mo|yr)s?$", re.IGNORECASE)


_BARE_NUMBER_RE = re.compile(r"^([\d,.kKmM]+)$")


_SECTION_END_RE = re.compile(
    r"\n(?:Experience|Education|Skills|Licenses|Recommendations|Honors|Languages|Interests|Top Voices|People you may know|People also viewed)\n",
    re.IGNORECASE,
)


_ACTIVITY_TAIL_NOISE = {
    "comment",
    "follow",
    "like",
    "more",
    "react",
    "repost",
    "send",
    "share",
    "view",
}


def _extract_metric(text: str, phrase: str) -> int | None:
    # Tight patterns: number and phrase on the same or adjacent line
    pattern_before = re.compile(rf"([\d,.kKmM]+)\s+{re.escape(phrase)}", re.IGNORECASE)
    pattern_after = re.compile(
        rf"{re.escape(phrase)}\s*:?\s*([\d,.kKmM]+)", re.IGNORECASE
    )

    match = pattern_before.search(text) or pattern_after.search(text)
    if match:
        return parse_count(match.group(1))

    # Loose pattern: number on one line, phrase within 2 lines below.
    # Handles LinkedIn dashboard widgets where DOM renders them in separate
    # elements, producing ``181\nDiscover who...\nprofile views``.
    loose_before = re.compile(
        rf"([\d,.kKmM]+)\s*\n(?:[^\n]{{0,80}}\n){{0,2}}\s*{re.escape(phrase)}",
        re.IGNORECASE,
    )
    m = loose_before.search(text)
    if m:
        return parse_count(m.group(1))

    return None


def _extract_time_ago(text: str) -> str | None:
    match = re.search(
        r"(\d+\s*(?:m|h|d|w|mo|yr)s?\s*ago|\d+[mhdw])",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1)


def _extract_post_from_text(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    author = lines[0] if lines else ""

    # Skip common feed metadata lines when building text preview.
    content_lines = [
        line
        for line in lines[1:]
        if line.lower() not in {"follow", "like", "comment", "repost", "send"}
    ]
    text_preview = "\n".join(content_lines[:6])

    reactions = _extract_metric(text, "reactions")
    if reactions is None:
        reactions = _extract_metric(text, "likes")
    comments = _extract_metric(text, "comments")

    return {
        "author": author,
        "text": text_preview,
        "reactions_count": reactions,
        "comments_count": comments,
        "time_ago": _extract_time_ago(text),
    }


def _classify_card(text: str) -> str:
    """Classify feed cards for actionability diagnostics."""
    normalized = " ".join(text.strip().lower().split())
    head = normalized[:240]

    if head.startswith("announcement"):
        return "announcement"
    if head.startswith("newsletter"):
        return "newsletter"
    if "sponsored" in head:
        return "sponsored"
    if "promoted" in head:
        return "promoted"
    if any(marker in head for marker in _CARD_TYPE_SUGGESTED_MARKERS):
        return "suggested"
    if head.startswith("suggested"):
        return "suggested"
    return "regular"


def _is_actionable_card_type(card_type: str) -> bool:
    return card_type not in _NON_ACTIONABLE_CARD_TYPES


def _parse_named_total(text: str | None, noun_pattern: str) -> int | None:
    if not text:
        return None
    match = re.search(
        rf"([\d,.kKmM]+)\s+(?:total\s+)?{noun_pattern}",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return parse_count(match.group(1))


def _normalize_reaction_type(label: str | None) -> str | None:
    if not label:
        return None
    lowered = label.strip().lower()
    for reaction in (
        "celebrate",
        "support",
        "insightful",
        "love",
        "funny",
        "like",
    ):
        if reaction in lowered:
            return reaction
    return None


def _clean_member_name(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = " ".join(raw.split())
    for marker in (" View ", " • "):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()

    tokens = cleaned.split()
    max_repeat = min(4, len(tokens) // 2)
    for size in range(max_repeat, 0, -1):
        if tokens[:size] == tokens[size : size * 2]:
            cleaned = " ".join(tokens[:size])
            break

    return cleaned or None


def _same_linkedin_profile(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    try:
        return urlparse(left).path.rstrip("/") == urlparse(right).path.rstrip("/")
    except Exception:
        return left == right


def _is_activity_metric_line(line: str) -> bool:
    lowered = line.lower()
    if lowered in _ACTIVITY_TAIL_NOISE:
        return True
    return bool(
        re.match(
            r"^(?:[\d,.kKmM]+\s+(?:impressions?|reactions?|likes?|comments?|reposts?|views?)|(?:impressions?|reactions?|likes?|comments?|reposts?|views?)\s*:?\s*[\d,.kKmM]+)$",
            line,
            re.IGNORECASE,
        )
    )


def _is_activity_metadata_line(line: str) -> bool:
    lowered = line.lower()
    if lowered == "follow":
        return True
    if "visible to anyone" in lowered:
        return True
    if "on or off linkedin" in lowered:
        return True
    if "followers" in lowered or "connections" in lowered:
        return True
    if re.fullmatch(
        r"\d+\s*(?:m|h|d|w|mo|yr)s?\s*ago(?:\s*[•·].*)?",
        lowered,
        re.IGNORECASE,
    ):
        return True
    return False


def _build_post_analytics_item(
    text: str,
    url: str | None = None,
    post_urn: str | None = None,
) -> dict[str, Any]:
    summary = _extract_post_from_text(text)
    reactions = _extract_metric(text, "reactions")
    if reactions is None:
        reactions = _extract_metric(text, "likes")

    return {
        "author": summary["author"] or None,
        "url": url,
        "post_urn": post_urn,
        "text_preview": (summary["text"] or text[:240])[:240],
        "time_ago": summary["time_ago"],
        "reactions": reactions,
        "comments": _extract_metric(text, "comments"),
        "reposts": _extract_metric(text, "reposts"),
        "impressions": _extract_metric(text, "impressions"),
    }


def _build_activity_post_analytics_item(
    text: str,
    url: str | None = None,
    post_urn: str | None = None,
) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    while lines and re.match(r"^feed post number \d+$", lines[0], re.IGNORECASE):
        lines.pop(0)

    time_ago = _extract_time_ago(text)
    time_index: int | None = None
    for idx, line in enumerate(lines):
        if _extract_time_ago(line):
            time_index = idx
            break

    author: str | None = None
    prelude = lines[:time_index] if time_index is not None else lines[:3]
    for line in prelude:
        cleaned = re.sub(
            r"\s+(?:posted|reposted)\s+this$",
            "",
            line,
            flags=re.IGNORECASE,
        ).strip()
        if not cleaned or _is_activity_metric_line(cleaned):
            continue
        author = cleaned
        break

    preview_lines = lines[time_index + 1 :] if time_index is not None else lines[1:]
    while preview_lines and _is_activity_metric_line(preview_lines[-1]):
        preview_lines.pop()
    while preview_lines and preview_lines[-1] == author:
        preview_lines.pop()
    preview_lines = [
        line
        for line in preview_lines
        if not _is_activity_metadata_line(line) and line != author
    ]

    text_preview = "\n".join(preview_lines[:8])[:300]

    return {
        "author": author,
        "url": url,
        "post_urn": post_urn,
        "text_preview": text_preview,
        "time_ago": time_ago,
        "reactions": _extract_metric(text, "reactions")
        or _extract_metric(text, "likes"),
        "comments": _extract_metric(text, "comments"),
        "reposts": _extract_metric(text, "reposts"),
        "impressions": _extract_metric(text, "impressions"),
    }


def _post_identity(post: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (
        post.get("url") or post.get("post_urn"),
        post.get("time_ago"),
        post.get("text_preview"),
    )


def _looks_like_analytics_card_text(text: str) -> bool:
    """Heuristic to filter recent-activity containers down to real post cards.

    LinkedIn often renders engagement counts as icon+number combos (no keyword),
    so we accept any card with substantial text (> 200 chars) OR one that does
    contain an explicit engagement keyword.  Short strings (< 50 chars) are
    always excluded as they're almost certainly navigation / UI chrome.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    has_engagement_keyword = any(
        token in lowered
        for token in (
            "impressions",
            "reactions",
            "likes",
            "comments",
            "reposts",
            "views",
        )
    )
    if has_engagement_keyword:
        return True
    if len(stripped) < 50:
        return False
    # Posts themselves are usually long; fall back to length for keyword-less cards.
    return len(stripped) > 200


def _parse_posts_from_activity_text(text: str, limit: int) -> list[dict[str, Any]]:
    """Parse post analytics from raw profile-page innerText.

    LinkedIn's profile Activity section has the shape (per post):
        <Author Name> [split point: "posted/reposted this • "] <time_ago>
        <time_ago repeated>
        <post body text>
        …
        <bare reaction count>   ← just a number, no label
        <N> comments
        <N> reposts             ← sometimes
        <Author Name>           ← start of NEXT post's attribution

    After re.split() on the "posted/reposted this •" pattern:
        parts[0]  = preamble ending with "<Author Name> "
        parts[1]  = "<time_ago>\n<time_ago>\n<content>\n<counts>\n<NextAuthor> "
        parts[2]  = …
    So the author of post i lives at the END of parts[i] (0-indexed).
    """
    match = _SECTION_END_RE.search(text)
    if match:
        text = text[: match.start()]

    _SPLIT_PAT = re.compile(r"(?:posted|reposted)\s+this\s*[•·]?\s*", re.IGNORECASE)
    parts = _SPLIT_PAT.split(text)

    posts: list[dict[str, Any]] = []
    for idx, raw_block in enumerate(parts[1:], start=1):
        if not raw_block.strip():
            continue

        lines = [ln.strip() for ln in raw_block.splitlines() if ln.strip()]
        if not lines:
            continue

        # Author is the LAST non-empty line of the PREVIOUS part
        prev_lines = [ln.strip() for ln in parts[idx - 1].splitlines() if ln.strip()]
        author = prev_lines[-1] if prev_lines else None

        # First line(s) of the block are usually the time-ago stamp ("1d", "5d", …)
        time_ago: str | None = None
        if lines and _TIME_AGO_RE.match(lines[0]):
            time_ago = lines.pop(0)
            # LinkedIn often repeats the stamp on the very next line
            if lines and _TIME_AGO_RE.match(lines[0]):
                lines.pop(0)

        # Strip trailing UI labels and the next post's author name so the
        # backward engagement scanner can reach real metrics.
        while lines:
            last_line = lines[-1]
            last_lower = last_line.lower()
            if last_lower in _ACTIVITY_TAIL_NOISE:
                lines.pop()
                continue
            if last_line == author:
                lines.pop()
                continue
            if len(last_line) < 60 and not re.search(r"[\d#]", last_line):
                lines.pop()
                continue
            break

        # Separate engagement tail from post body.
        # Engagement lines appear AFTER the body (near the end of the block):
        # a bare number (reactions), then "N comments", then "N reposts".
        # Walk backwards from the end to collect them.
        reactions: int | None = None
        comments: int | None = None
        reposts: int | None = None

        while lines:
            last = lines[-1]
            m_comments = re.match(r"^([\d,.kKmM]+)\s+comments?$", last, re.IGNORECASE)
            m_reposts = re.match(r"^([\d,.kKmM]+)\s+reposts?$", last, re.IGNORECASE)
            m_bare = _BARE_NUMBER_RE.match(last)

            if m_comments:
                comments = parse_count(m_comments.group(1))
                lines.pop()
            elif m_reposts:
                reposts = parse_count(m_reposts.group(1))
                lines.pop()
            elif m_bare and reactions is None:
                # A bare trailing number is the reaction count on profile activity.
                reactions = parse_count(m_bare.group(1))
                lines.pop()
            else:
                break

        text_preview = "\n".join(lines[:8])[:300]

        posts.append(
            {
                "author": author,
                "url": None,
                "post_urn": None,
                "text_preview": text_preview,
                "time_ago": time_ago or _extract_time_ago(raw_block),
                "reactions": reactions,
                "comments": comments,
                "reposts": reposts,
                "impressions": None,  # not available on profile page
            }
        )

        if len(posts) >= limit:
            break

    return posts
