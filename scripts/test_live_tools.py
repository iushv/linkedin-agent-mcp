#!/usr/bin/env python3
"""Live smoke test for the LinkedIn MCP server over streamable HTTP.

This script is intentionally not part of pytest. It is meant for manual
validation against a real logged-in LinkedIn session and a locally running MCP
server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from fastmcp import Client

READ_TOOL_NAMES = {
    "get_person_profile",
    "get_company_profile",
    "get_company_posts",
    "get_job_details",
    "search_jobs",
    "browse_feed",
    "get_conversations",
    "read_conversation",
    "get_pending_invitations",
    "get_profile_analytics",
    "get_my_post_analytics",
}

WRITE_TOOL_NAMES = {
    "create_post",
    "create_poll",
    "delete_post",
    "repost",
    "react_to_post",
    "comment_on_post",
    "reply_to_comment",
    "like_comment",
    "send_message",
    "send_connection_request",
    "respond_to_invitation",
    "follow_person",
}

SESSION_TOOL_NAMES = {"close_session"}

EXPECTED_TOOLS = READ_TOOL_NAMES | WRITE_TOOL_NAMES | SESSION_TOOL_NAMES

READ_TOOL_COOLDOWNS = {
    "get_conversations": 6.0,
    "read_conversation": 6.0,
    "get_profile_analytics": 6.0,
    "get_my_post_analytics": 6.0,
}

WRITE_TOOL_COOLDOWNS = {
    "send_message": 3.0,
}


@dataclass(frozen=True)
class ToolCase:
    name: str
    args: dict[str, Any]
    expect: str


@dataclass(frozen=True)
class ToolOutcome:
    name: str
    status: str
    detail: str
    duration_seconds: float
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080/mcp",
        help="Streamable HTTP MCP endpoint URL.",
    )
    parser.add_argument(
        "--person-username",
        default="ayushkumar-exl",
        help="Profile slug for get_person_profile.",
    )
    parser.add_argument(
        "--company-name",
        default="anthropicresearch",
        help="Company slug for company tools.",
    )
    parser.add_argument(
        "--job-id",
        default="4252026496",
        help="LinkedIn job id for get_job_details.",
    )
    parser.add_argument(
        "--job-keywords",
        default="python developer",
        help="Keywords for search_jobs.",
    )
    parser.add_argument(
        "--job-location",
        default="Remote",
        help="Location for search_jobs.",
    )
    parser.add_argument(
        "--feed-count",
        type=int,
        default=3,
        help="Post count for browse_feed.",
    )
    parser.add_argument(
        "--analytics-limit",
        type=int,
        default=3,
        help="Limit for analytics reads.",
    )
    parser.add_argument(
        "--conversation-limit",
        type=int,
        default=5,
        help="Limit for get_conversations.",
    )
    parser.add_argument(
        "--invitation-limit",
        type=int,
        default=5,
        help="Limit for get_pending_invitations.",
    )
    parser.add_argument(
        "--write-profile-url",
        default="https://www.linkedin.com/in/ayushkumar-exl/",
        help="Profile URL used for write dry runs.",
    )
    parser.add_argument(
        "--post-url",
        default="https://www.linkedin.com/feed/update/urn:li:activity:1/",
        help="Post URL used for write dry runs.",
    )
    parser.add_argument(
        "--skip-close-session",
        action="store_true",
        help="Do not call close_session at the end.",
    )
    parser.add_argument(
        "--tool-timeout",
        type=int,
        default=90,
        help="Timeout in seconds for each tool call.",
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        help="Run only the named tool(s). May be passed multiple times.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--read-only",
        action="store_true",
        help="Run only read tools (plus close_session unless skipped).",
    )
    mode_group.add_argument(
        "--write-only",
        action="store_true",
        help="Run only write dry-run tools (plus close_session unless skipped).",
    )
    parser.add_argument(
        "--focus-read-conversation",
        action="store_true",
        help=(
            "Run only read_conversation and skip inbox discovery. "
            "Requires --thread-id or --conversation-profile-url."
        ),
    )
    parser.add_argument(
        "--thread-id",
        help="Explicit thread id for read_conversation.",
    )
    parser.add_argument(
        "--conversation-profile-url",
        help="Explicit profile URL for read_conversation.",
    )
    parser.add_argument(
        "--read-sleep",
        type=float,
        default=2.0,
        help="Minimum seconds to wait between read tools.",
    )
    parser.add_argument(
        "--write-sleep",
        type=float,
        default=0.5,
        help="Minimum seconds to wait between write tools.",
    )
    parser.add_argument(
        "--read-conversation-retries",
        type=int,
        default=1,
        help="Retry count for read_conversation on MCP timeouts.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=5.0,
        help="Base backoff in seconds for timeout retries.",
    )
    parser.add_argument(
        "--json-out",
        help="Write run results as JSON to this path.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.focus_read_conversation and not (
        args.thread_id or args.conversation_profile_url
    ):
        raise SystemExit(
            "--focus-read-conversation requires --thread-id or "
            "--conversation-profile-url"
        )

    if args.focus_read_conversation and args.write_only:
        raise SystemExit("--focus-read-conversation cannot be used with --write-only")


def short_detail(result: Any) -> str:
    payload = normalize_result(result)
    if isinstance(payload, dict):
        if "status" in payload:
            status = str(payload.get("status"))
            message = str(payload.get("message", ""))
            if status == "success" and isinstance(payload.get("data"), dict):
                keys = ",".join(sorted(payload["data"].keys()))
                return f"status=success data={keys}"
            if status == "dry_run":
                return f"status=dry_run {message}".strip()
            return f"status={status} {message}".strip()
        if "error" in payload:
            return f"error={payload['error']}"
        if "url" in payload and "sections" in payload:
            return f"sections={len(payload.get('sections', {}))} url={payload['url']}"
        return ",".join(sorted(payload.keys()))
    return repr(payload)


def classify_result(result: Any, expect: str) -> tuple[str, str]:
    payload = normalize_result(result)
    if expect == "close":
        if isinstance(payload, dict) and payload.get("status") == "success":
            return "PASS", short_detail(payload)
        return "FAIL", short_detail(payload)

    if expect == "dry_run":
        if isinstance(payload, dict) and payload.get("status") == "dry_run":
            return "PASS", short_detail(payload)
        return "FAIL", short_detail(payload)

    if isinstance(payload, dict) and payload.get("status") == "error":
        return "FAIL", short_detail(payload)
    if isinstance(payload, dict) and "error" in payload:
        return "FAIL", short_detail(payload)
    return "PASS", short_detail(payload)


def unwrap_data(result: Any) -> dict[str, Any]:
    payload = normalize_result(result)
    if isinstance(payload, dict) and payload.get("status") == "success":
        data = payload.get("data")
        if isinstance(data, dict):
            return data
    if isinstance(payload, dict):
        return payload
    return {}


def normalize_result(result: Any) -> Any:
    for attr in ("data", "structured_content"):
        payload = getattr(result, attr, None)
        if isinstance(payload, dict):
            return payload
    return result


def print_table(title: str, outcomes: list[ToolOutcome]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    for outcome in outcomes:
        attempts = f" x{outcome.attempts}" if outcome.attempts > 1 else ""
        print(
            f"{outcome.name:28} {outcome.status:7} "
            f"{outcome.duration_seconds:6.1f}s{attempts} {outcome.detail}"
        )


def is_timeout_exception(exc: Exception) -> bool:
    return "Timed out while waiting for response" in str(exc)


def effective_cooldown(
    tool_name: str,
    base_sleep_seconds: float,
    cooldown_overrides: dict[str, float],
) -> float:
    return max(base_sleep_seconds, cooldown_overrides.get(tool_name, 0.0))


async def invoke_case(
    client: Client,
    case: ToolCase,
    timeout_seconds: int,
    retries: int = 0,
    retry_backoff_seconds: float = 0.0,
) -> tuple[ToolOutcome, Any]:
    started_at = perf_counter()
    attempts = retries + 1

    for attempt in range(1, attempts + 1):
        try:
            result = await client.call_tool(
                case.name,
                case.args,
                raise_on_error=False,
                timeout=timeout_seconds,
            )
        except Exception as exc:
            if attempt < attempts and is_timeout_exception(exc):
                backoff = retry_backoff_seconds * attempt
                if backoff > 0:
                    print(
                        f"{case.name} timed out on attempt {attempt}/{attempts}; "
                        f"backing off for {backoff:.1f}s",
                        flush=True,
                    )
                    await asyncio.sleep(backoff)
                continue
            return (
                ToolOutcome(
                    case.name,
                    "FAIL",
                    f"exception={type(exc).__name__}: {exc}",
                    perf_counter() - started_at,
                    attempt,
                ),
                None,
            )

        status, detail = classify_result(result, case.expect)
        return (
            ToolOutcome(
                case.name,
                status,
                detail,
                perf_counter() - started_at,
                attempt,
            ),
            result,
        )

    return (
        ToolOutcome(
            case.name,
            "FAIL",
            "exhausted retries without a response",
            perf_counter() - started_at,
            attempts,
        ),
        None,
    )


async def maybe_sleep(seconds: float, *, reason: str | None = None) -> None:
    if seconds <= 0:
        return
    if reason:
        print(f"Cooling down for {seconds:.1f}s after {reason}...", flush=True)
    await asyncio.sleep(seconds)


def build_read_cases(args: argparse.Namespace) -> list[ToolCase]:
    return [
        ToolCase(
            "get_person_profile",
            {"linkedin_username": args.person_username},
            "read",
        ),
        ToolCase(
            "get_company_profile",
            {"company_name": args.company_name},
            "read",
        ),
        ToolCase(
            "get_company_posts",
            {"company_name": args.company_name},
            "read",
        ),
        ToolCase("get_job_details", {"job_id": args.job_id}, "read"),
        ToolCase(
            "search_jobs",
            {"keywords": args.job_keywords, "location": args.job_location},
            "read",
        ),
        ToolCase("browse_feed", {"count": args.feed_count}, "read"),
        ToolCase(
            "get_conversations",
            {"limit": args.conversation_limit},
            "read",
        ),
        ToolCase(
            "get_pending_invitations",
            {"limit": args.invitation_limit},
            "read",
        ),
        ToolCase("get_profile_analytics", {}, "read"),
        ToolCase(
            "get_my_post_analytics",
            {"limit": args.analytics_limit},
            "read",
        ),
    ]


def build_write_cases(args: argparse.Namespace) -> list[ToolCase]:
    return [
        ToolCase(
            "create_post",
            {
                "text": "Live smoke dry-run from scripts/test_live_tools.py",
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "create_poll",
            {
                "question": "Live smoke test?",
                "options": ["Yes", "Still yes"],
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "delete_post",
            {
                "post_url": args.post_url,
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "repost",
            {
                "post_url": args.post_url,
                "comment": "Dry run validation",
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "react_to_post",
            {
                "post_url": args.post_url,
                "reaction": "like",
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "comment_on_post",
            {
                "post_url": args.post_url,
                "text": "Dry run validation",
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "reply_to_comment",
            {
                "post_url": args.post_url,
                "comment_index": 0,
                "text": "Dry run validation",
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "like_comment",
            {
                "post_url": args.post_url,
                "comment_index": 0,
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "send_message",
            {
                "profile_url": args.write_profile_url,
                "text": "Dry run validation",
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "send_connection_request",
            {
                "profile_url": args.write_profile_url,
                "note": "Dry run validation",
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "respond_to_invitation",
            {
                "profile_url": args.write_profile_url,
                "action": "accept",
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
        ToolCase(
            "follow_person",
            {
                "profile_url": args.write_profile_url,
                "confirm": True,
                "dry_run": True,
            },
            "dry_run",
        ),
    ]


def select_cases(
    cases: list[ToolCase],
    selected_tools: set[str],
    *,
    include_only: set[str] | None = None,
) -> list[ToolCase]:
    if include_only is not None:
        cases = [case for case in cases if case.name in include_only]
    if selected_tools:
        cases = [case for case in cases if case.name in selected_tools]
    return cases


def resolve_conversation_args(
    args: argparse.Namespace,
    cached_results: dict[str, Any],
) -> dict[str, Any] | None:
    if args.thread_id:
        return {"thread_id": args.thread_id}
    if args.conversation_profile_url:
        return {"profile_url": args.conversation_profile_url}

    conversations = unwrap_data(cached_results.get("get_conversations"))
    for item in conversations.get("conversations", []):
        if isinstance(item, dict) and item.get("thread_id"):
            return {"thread_id": item["thread_id"]}
    for item in conversations.get("conversations", []):
        if isinstance(item, dict) and item.get("profile_url"):
            return {"profile_url": item["profile_url"]}
    return None


def should_run_read_conversation(
    selected_tools: set[str],
    args: argparse.Namespace,
) -> bool:
    if args.write_only:
        return False
    if args.focus_read_conversation:
        return True
    if selected_tools:
        return "read_conversation" in selected_tools
    return True


def write_json_report(
    path_str: str,
    *,
    args: argparse.Namespace,
    missing: list[str],
    unexpected: list[str],
    failures: int,
    read_outcomes: list[ToolOutcome],
    write_outcomes: list[ToolOutcome],
    session_outcomes: list[ToolOutcome],
) -> None:
    path = Path(path_str)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "url": args.url,
        "tool_timeout_seconds": args.tool_timeout,
        "mode": (
            "read_only"
            if args.read_only
            else "write_only"
            if args.write_only
            else "all"
        ),
        "selected_tools": args.tool,
        "focus_read_conversation": args.focus_read_conversation,
        "thread_id": args.thread_id,
        "conversation_profile_url": args.conversation_profile_url,
        "read_sleep_seconds": args.read_sleep,
        "write_sleep_seconds": args.write_sleep,
        "read_conversation_retries": args.read_conversation_retries,
        "retry_backoff_seconds": args.retry_backoff_seconds,
        "missing_tools": missing,
        "unexpected_tools": unexpected,
        "failures": failures,
        "read_outcomes": [outcome.to_dict() for outcome in read_outcomes],
        "write_outcomes": [outcome.to_dict() for outcome in write_outcomes],
        "session_outcomes": [outcome.to_dict() for outcome in session_outcomes],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote JSON report to {path}", flush=True)


async def main() -> int:
    args = parse_args()
    validate_args(args)

    selected_tools = set(args.tool)
    if args.focus_read_conversation:
        selected_tools = {"read_conversation"}

    seed_read_conversation = (
        should_run_read_conversation(selected_tools, args)
        and not args.focus_read_conversation
        and not args.thread_id
        and not args.conversation_profile_url
    )

    async with Client(args.url, timeout=120) as client:
        try:
            tool_names = {tool.name for tool in await client.list_tools()}
        except Exception as exc:
            print(f"Failed to list tools: {type(exc).__name__}: {exc}")
            return 1

        missing = sorted(EXPECTED_TOOLS - tool_names)
        unexpected = sorted(tool_names - EXPECTED_TOOLS)
        print(f"Connected to {args.url}")
        print(f"Registered tools: {len(tool_names)}")
        if missing:
            print(f"Missing tools: {', '.join(missing)}")
        if unexpected:
            print(f"Extra tools: {', '.join(unexpected)}")

        read_include_only = None
        if args.read_only:
            read_include_only = READ_TOOL_NAMES
        elif args.write_only:
            read_include_only = set()

        read_cases = build_read_cases(args)
        if seed_read_conversation:
            selected_with_seed = set(selected_tools)
            if selected_with_seed:
                selected_with_seed.add("get_conversations")
        else:
            selected_with_seed = selected_tools

        read_cases = select_cases(
            read_cases,
            selected_with_seed,
            include_only=read_include_only,
        )

        read_outcomes: list[ToolOutcome] = []
        cached_results: dict[str, Any] = {}
        for index, case in enumerate(read_cases):
            print(f"Running {case.name}...", flush=True)
            outcome, result = await invoke_case(client, case, args.tool_timeout)
            read_outcomes.append(outcome)
            if outcome.status == "PASS":
                cached_results[case.name] = result
            if index < len(read_cases) - 1:
                cooldown = effective_cooldown(
                    case.name, args.read_sleep, READ_TOOL_COOLDOWNS
                )
                await maybe_sleep(cooldown, reason=case.name)

        if should_run_read_conversation(selected_tools, args):
            conversation_args = resolve_conversation_args(args, cached_results)
            if conversation_args is None:
                detail = (
                    "No conversation thread_id/profile_url available from "
                    "get_conversations"
                )
                if args.focus_read_conversation:
                    detail = "Focused read_conversation mode requires a usable target"
                read_outcomes.append(
                    ToolOutcome(
                        "read_conversation",
                        "SKIP",
                        detail,
                        0.0,
                    )
                )
            else:
                if read_cases:
                    await maybe_sleep(
                        effective_cooldown(
                            "read_conversation",
                            args.read_sleep,
                            READ_TOOL_COOLDOWNS,
                        ),
                        reason="read_conversation",
                    )
                outcome, _ = await invoke_case(
                    client,
                    ToolCase("read_conversation", conversation_args, "read"),
                    args.tool_timeout,
                    retries=args.read_conversation_retries,
                    retry_backoff_seconds=args.retry_backoff_seconds,
                )
                read_outcomes.append(outcome)

        write_include_only = None
        if args.write_only:
            write_include_only = WRITE_TOOL_NAMES
        elif args.read_only:
            write_include_only = set()

        write_cases = select_cases(
            build_write_cases(args),
            selected_tools,
            include_only=write_include_only,
        )

        write_outcomes: list[ToolOutcome] = []
        for index, case in enumerate(write_cases):
            print(f"Running {case.name}...", flush=True)
            outcome, _ = await invoke_case(client, case, args.tool_timeout)
            write_outcomes.append(outcome)
            if index < len(write_cases) - 1:
                cooldown = effective_cooldown(
                    case.name, args.write_sleep, WRITE_TOOL_COOLDOWNS
                )
                await maybe_sleep(cooldown, reason=case.name)

        session_outcomes: list[ToolOutcome] = []
        if not args.skip_close_session and (
            not selected_tools or "close_session" in selected_tools
        ):
            outcome, _ = await invoke_case(
                client,
                ToolCase("close_session", {}, "close"),
                args.tool_timeout,
            )
            session_outcomes.append(outcome)

    print_table("Read Tools", read_outcomes)
    print_table("Write Tools", write_outcomes)
    if session_outcomes:
        print_table("Session", session_outcomes)

    failures = 0
    if missing:
        failures += len(missing)
    failures += sum(
        1
        for outcome in [*read_outcomes, *write_outcomes, *session_outcomes]
        if outcome.status == "FAIL"
    )
    print(f"\nFailures: {failures}")

    if args.json_out:
        write_json_report(
            args.json_out,
            args=args,
            missing=missing,
            unexpected=unexpected,
            failures=failures,
            read_outcomes=read_outcomes,
            write_outcomes=write_outcomes,
            session_outcomes=session_outcomes,
        )

    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
