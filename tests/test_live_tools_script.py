from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import scripts.test_live_tools as live_tools


class FakeClient:
    calls: list[tuple[str, dict[str, Any]]] = []
    results: dict[str, Any] = {}
    listed_tools: set[str] = set(live_tools.EXPECTED_TOOLS)

    def __init__(self, url: str, timeout: int = 120):
        self.url = url
        self.timeout = timeout

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def list_tools(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=name) for name in sorted(self.listed_tools)]

    async def call_tool(
        self,
        name: str,
        args: dict[str, Any],
        raise_on_error: bool = False,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        del raise_on_error, timeout
        self.calls.append((name, args))

        configured = self.results.get(name)
        if callable(configured):
            return configured(args)
        if configured is not None:
            return configured

        if name in live_tools.WRITE_TOOL_NAMES:
            return {"status": "dry_run", "message": f"{name} dry run"}
        if name in live_tools.SESSION_TOOL_NAMES:
            return {"status": "success", "message": "session closed"}
        # Tools with content assertions need a payload that actually satisfies
        # them; an empty dict is a vacuous result and is now a real failure.
        if name == "get_company_posts":
            slug = str(args.get("company_name", ""))
            return {
                "status": "success",
                "data": {
                    "url": f"https://www.linkedin.com/company/{slug}/posts/",
                    "posts": [
                        {
                            "author": slug,
                            "url": "https://www.linkedin.com/feed/update/urn:li:activity:1",
                            "text": f"{slug} post",
                        }
                    ],
                },
            }
        if name == "search_people" and args.get("current_company"):
            company = str(args["current_company"])
            return {
                "status": "success",
                "data": {
                    "results": [
                        {"name": "Sample", "headline": f"Engineer at {company}"}
                    ]
                },
            }
        if name in live_tools._MUST_NOT_BE_EMPTY:
            key, _ = live_tools._MUST_NOT_BE_EMPTY[name]
            filler: Any = (
                {"main_profile": "sample text"} if key == "sections" else [{"id": 1}]
            )
            return {"status": "success", "data": {key: filler}}
        return {"status": "success", "data": {}}


@pytest.fixture(autouse=True)
def reset_fake_client():
    FakeClient.calls = []
    FakeClient.results = {}
    FakeClient.listed_tools = set(live_tools.EXPECTED_TOOLS)
    yield
    FakeClient.calls = []
    FakeClient.results = {}
    FakeClient.listed_tools = set(live_tools.EXPECTED_TOOLS)


def make_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    data = {
        "url": "http://127.0.0.1:8080/mcp",
        "person_username": "ayushkumar-exl",
        "company_name": "anthropicresearch",
        "filter_company": "Uber",
        "filter_keywords": "engineer",
        "job_id": "4252026496",
        "job_keywords": "python developer",
        "job_location": "Remote",
        "feed_count": 3,
        "analytics_limit": 3,
        "conversation_limit": 5,
        "invitation_limit": 5,
        "list_limit": 5,
        "write_profile_url": "https://www.linkedin.com/in/ayushkumar-exl/",
        "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1/",
        "skip_close_session": False,
        "tool_timeout": 90,
        "tool": [],
        "read_only": False,
        "write_only": False,
        "focus_read_conversation": False,
        "thread_id": None,
        "conversation_profile_url": None,
        "read_sleep": 0.0,
        "write_sleep": 0.0,
        "read_conversation_retries": 1,
        "retry_backoff_seconds": 0.0,
        "json_out": None,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


@pytest.mark.asyncio
async def test_main_read_only_runs_no_write_tools(tmp_path, monkeypatch):
    json_out = tmp_path / "live-smoke.json"
    args = make_args(tmp_path, read_only=True, json_out=str(json_out))
    monkeypatch.setattr(live_tools, "parse_args", lambda: args)
    monkeypatch.setattr(live_tools, "Client", FakeClient)

    exit_code = await live_tools.main()

    called_names = [name for name, _ in FakeClient.calls]
    assert exit_code == 0
    assert called_names
    assert set(called_names).issubset(
        live_tools.READ_TOOL_NAMES | live_tools.SESSION_TOOL_NAMES
    )
    assert not (set(called_names) & live_tools.WRITE_TOOL_NAMES)

    report = json.loads(json_out.read_text())
    assert report["mode"] == "read_only"
    assert report["failures"] == 0
    assert report["write_outcomes"] == []
    assert report["session_outcomes"][0]["name"] == "close_session"


@pytest.mark.asyncio
async def test_main_write_only_runs_no_read_tools(tmp_path, monkeypatch):
    args = make_args(tmp_path, write_only=True)
    monkeypatch.setattr(live_tools, "parse_args", lambda: args)
    monkeypatch.setattr(live_tools, "Client", FakeClient)

    exit_code = await live_tools.main()

    called_names = [name for name, _ in FakeClient.calls]
    assert exit_code == 0
    assert called_names
    assert set(called_names).issubset(
        live_tools.WRITE_TOOL_NAMES | live_tools.SESSION_TOOL_NAMES
    )
    assert not (set(called_names) & live_tools.READ_TOOL_NAMES)


@pytest.mark.asyncio
async def test_main_focus_read_conversation_uses_explicit_thread_id(
    tmp_path, monkeypatch
):
    args = make_args(
        tmp_path,
        focus_read_conversation=True,
        thread_id="abc123",
        skip_close_session=True,
    )
    FakeClient.results["read_conversation"] = {
        "status": "success",
        "data": {"thread_id": "abc123", "messages": []},
    }
    monkeypatch.setattr(live_tools, "parse_args", lambda: args)
    monkeypatch.setattr(live_tools, "Client", FakeClient)

    exit_code = await live_tools.main()

    assert exit_code == 0
    assert FakeClient.calls == [("read_conversation", {"thread_id": "abc123"})]


async def test_expected_tools_match_registered_server_tools():
    from linkedin_mcp_server.server import create_mcp_server

    mcp = create_mcp_server()
    registered = {tool.name for tool in await mcp.list_tools()}
    assert live_tools.EXPECTED_TOOLS == registered


def test_build_read_cases_cover_declared_read_tools(tmp_path):
    args = make_args(tmp_path)

    case_names = {case.name for case in live_tools.build_read_cases(args)}

    assert case_names == live_tools.READ_TOOL_NAMES - {"read_conversation"}


def test_build_write_cases_cover_declared_write_tools(tmp_path):
    args = make_args(tmp_path)

    case_names = {case.name for case in live_tools.build_write_cases(args)}

    assert case_names == live_tools.WRITE_TOOL_NAMES


def test_validate_args_requires_focus_target(tmp_path):
    args = make_args(tmp_path, focus_read_conversation=True)

    with pytest.raises(SystemExit, match="requires --thread-id or"):
        live_tools.validate_args(args)


def test_validate_args_rejects_focus_with_write_only(tmp_path):
    args = make_args(
        tmp_path,
        focus_read_conversation=True,
        thread_id="abc123",
        write_only=True,
    )

    with pytest.raises(SystemExit, match="cannot be used with --write-only"):
        live_tools.validate_args(args)


def test_effective_cooldown_prefers_override():
    assert (
        live_tools.effective_cooldown(
            "get_conversations", 2.0, live_tools.READ_TOOL_COOLDOWNS
        )
        == 6.0
    )
    assert (
        live_tools.effective_cooldown(
            "get_person_profile", 2.0, live_tools.READ_TOOL_COOLDOWNS
        )
        == 2.0
    )


def test_write_json_report_serializes_outcomes(tmp_path):
    output_path = tmp_path / "report.json"
    args = make_args(tmp_path, json_out=str(output_path), read_only=True)

    live_tools.write_json_report(
        str(output_path),
        args=args,
        missing=[],
        unexpected=[],
        failures=1,
        read_outcomes=[
            live_tools.ToolOutcome(
                name="get_conversations",
                status="PASS",
                detail="status=success data=conversations",
                duration_seconds=5.9,
                attempts=2,
            )
        ],
        write_outcomes=[],
        session_outcomes=[],
    )

    payload = json.loads(output_path.read_text())
    assert payload["mode"] == "read_only"
    assert payload["failures"] == 1
    assert payload["read_outcomes"][0]["attempts"] == 2
    assert payload["read_outcomes"][0]["name"] == "get_conversations"


# ---------------------------------------------------------------------------
# content_violations — the canary's own assertions must not pass vacuously
# ---------------------------------------------------------------------------


def _case(name: str, **args: Any) -> live_tools.ToolCase:
    return live_tools.ToolCase(name, args, "read")


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "success", "data": data}


class TestContentViolationsCompanyPosts:
    def test_clean_payload_has_no_violations(self):
        result = _ok(
            {
                "url": "https://www.linkedin.com/company/llamaindex/posts/",
                "posts": [
                    {"author": "LlamaIndex", "url": "https://x/1", "text": "hi"},
                ],
            }
        )
        assert (
            live_tools.content_violations(
                _case("get_company_posts", company_name="llamaindex"), result
            )
            == []
        )

    def test_empty_posts_is_a_violation_not_a_skip(self):
        violations = live_tools.content_violations(
            _case("get_company_posts", company_name="llamaindex"), _ok({"posts": []})
        )
        assert any("empty result is not a pass" in v for v in violations)

    def test_detects_cross_company_leak(self):
        result = _ok(
            {
                "url": "https://www.linkedin.com/company/weaviate/posts/",
                "posts": [
                    {"author": "Weaviate", "url": "https://x/1", "text": "vectors"}
                ],
            }
        )
        violations = live_tools.content_violations(
            _case("get_company_posts", company_name="llamaindex"), result
        )
        assert any("slug mismatch" in v for v in violations)

    def test_detects_all_null_urls(self):
        result = _ok(
            {
                "url": "https://www.linkedin.com/company/llamaindex/posts/",
                "posts": [{"author": "LlamaIndex", "url": None, "text": "hi"}],
            }
        )
        violations = live_tools.content_violations(
            _case("get_company_posts", company_name="llamaindex"), result
        )
        assert any("url=null" in v for v in violations)

    def test_detects_all_null_authors(self):
        result = _ok(
            {
                "url": "https://www.linkedin.com/company/llamaindex/posts/",
                "posts": [{"author": None, "url": "https://x/1", "text": "LlamaIndex"}],
            }
        )
        violations = live_tools.content_violations(
            _case("get_company_posts", company_name="llamaindex"), result
        )
        assert any("author=null" in v for v in violations)


class TestContentViolationsSearchPeople:
    def test_matching_headline_passes(self):
        result = _ok({"results": [{"name": "A", "headline": "Engineer at Uber"}]})
        assert (
            live_tools.content_violations(
                _case("search_people", current_company="Uber"), result
            )
            == []
        )

    def test_empty_results_is_a_violation_not_a_skip(self):
        violations = live_tools.content_violations(
            _case("search_people", current_company="Uber"), _ok({"results": []})
        )
        assert any("empty result is not a pass" in v for v in violations)

    def test_blank_headlines_reported_as_unverifiable(self):
        result = _ok({"results": [{"name": "A", "headline": ""}, {"name": "B"}]})
        violations = live_tools.content_violations(
            _case("search_people", current_company="Uber"), result
        )
        assert any("unverifiable" in v for v in violations)

    def test_wrong_company_reported_as_filter_ignored(self):
        result = _ok({"results": [{"name": "A", "headline": "Engineer at Google"}]})
        violations = live_tools.content_violations(
            _case("search_people", current_company="Uber"), result
        )
        assert any("filter ignored" in v for v in violations)

    def test_no_filter_requested_means_no_assertions(self):
        assert (
            live_tools.content_violations(
                _case("search_people", keywords="python"), _ok({"results": []})
            )
            == []
        )


class TestContentViolationsEmptinessGeneralized:
    """Every asserted tool must fail on an empty primary collection, and the
    exemptions must be deliberate rather than accidental."""

    def test_browse_feed_empty_posts_fails(self):
        violations = live_tools.content_violations(
            _case("browse_feed"), _ok({"posts": []})
        )
        assert any("empty 'posts'" in v for v in violations)

    def test_browse_feed_with_posts_passes(self):
        violations = live_tools.content_violations(
            _case("browse_feed"), _ok({"posts": [{"author": "A"}]})
        )
        assert violations == []

    def test_person_profile_empty_sections_fails(self):
        violations = live_tools.content_violations(
            _case("get_person_profile"), _ok({"sections": {}})
        )
        assert any("empty 'sections'" in v for v in violations)

    def test_person_profile_with_sections_passes(self):
        violations = live_tools.content_violations(
            _case("get_person_profile"), _ok({"sections": {"main_profile": "text"}})
        )
        assert violations == []

    def test_exempt_tools_tolerate_emptiness(self):
        for name in ("get_pending_invitations", "get_conversations"):
            assert live_tools.content_violations(_case(name), _ok({})) == []

    def test_every_canary_tool_is_classified(self):
        """Guards against a new tool silently getting no coverage decision."""
        import re as _re
        from pathlib import Path

        script = Path(__file__).resolve().parents[1] / "scripts" / "run_canary.sh"
        canary_tools = set(_re.findall(r"--tool\s+(\w+)", script.read_text()))
        asserted = {"get_company_posts", "search_people"}
        classified = (
            set(live_tools._MUST_NOT_BE_EMPTY)
            | set(live_tools._MAY_BE_EMPTY)
            | asserted
        )

        unclassified = canary_tools - classified
        assert not unclassified, (
            f"canary runs {sorted(unclassified)} with no emptiness decision — "
            "add to _MUST_NOT_BE_EMPTY or _MAY_BE_EMPTY"
        )
