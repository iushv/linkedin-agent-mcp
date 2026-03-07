from typing import Any, Callable, Coroutine
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP


async def get_tool_fn(
    mcp: FastMCP, name: str
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """Extract tool function from FastMCP by name using public API."""
    tool = await mcp.get_tool(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' not found")
    return tool.fn  # type: ignore[attr-defined]


@pytest.fixture
def patch_tool_deps(monkeypatch):
    """Patch ensure_authenticated and get_or_create_browser for all tools."""
    mock_browser = MagicMock()
    mock_browser.page = MagicMock()

    for module in ["person", "company", "job"]:
        monkeypatch.setattr(
            f"linkedin_mcp_server.tools.{module}.ensure_authenticated", AsyncMock()
        )
        monkeypatch.setattr(
            f"linkedin_mcp_server.tools.{module}.get_or_create_browser",
            AsyncMock(return_value=mock_browser),
        )

    monkeypatch.setattr(
        "linkedin_mcp_server.tools.job.acquire_browser_lock", AsyncMock()
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.tools.job.release_browser_lock", lambda: None
    )

    return mock_browser


def _make_mock_extractor(scrape_result: dict) -> MagicMock:
    """Create a mock LinkedInExtractor that returns the given result."""
    mock = MagicMock()
    mock.scrape_person = AsyncMock(return_value=scrape_result)
    mock.scrape_company = AsyncMock(return_value=scrape_result)
    mock.scrape_job = AsyncMock(return_value=scrape_result)
    mock.search_jobs = AsyncMock(return_value=scrape_result)
    mock.extract_page = AsyncMock(return_value="some text")
    return mock


class TestPersonTool:
    async def test_get_person_profile_success(
        self, mock_context, patch_tool_deps, monkeypatch
    ):
        expected = {
            "url": "https://www.linkedin.com/in/test-user/",
            "sections": {"main_profile": "John Doe\nSoftware Engineer"},
            "pages_visited": ["https://www.linkedin.com/in/test-user/"],
            "sections_requested": ["main_profile"],
        }
        mock_extractor = _make_mock_extractor(expected)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.person.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )

        from linkedin_mcp_server.tools.person import register_person_tools

        mcp = FastMCP("test")
        register_person_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_person_profile")
        result = await tool_fn("test-user", mock_context)
        assert result["url"] == "https://www.linkedin.com/in/test-user/"
        assert "main_profile" in result["sections"]
        assert result["sections_requested"] == ["main_profile"]

    async def test_get_person_profile_with_sections(
        self, mock_context, patch_tool_deps, monkeypatch
    ):
        """Verify sections parameter is passed through."""
        expected = {
            "url": "https://www.linkedin.com/in/test-user/",
            "sections": {
                "main_profile": "John Doe",
                "experience": "Work history",
                "contact_info": "Email: test@test.com",
            },
            "pages_visited": [
                "https://www.linkedin.com/in/test-user/",
                "https://www.linkedin.com/in/test-user/details/experience/",
                "https://www.linkedin.com/in/test-user/overlay/contact-info/",
            ],
            "sections_requested": ["main_profile", "experience", "contact_info"],
        }
        mock_extractor = _make_mock_extractor(expected)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.person.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )

        from linkedin_mcp_server.tools.person import register_person_tools

        mcp = FastMCP("test")
        register_person_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_person_profile")
        result = await tool_fn(
            "test-user", mock_context, sections="experience,contact_info"
        )
        assert result["sections_requested"] == [
            "main_profile",
            "experience",
            "contact_info",
        ]
        mock_extractor.scrape_person.assert_awaited_once()

    async def test_get_person_profile_accepts_full_profile_url(
        self, mock_context, patch_tool_deps, monkeypatch
    ):
        expected = {
            "url": "https://www.linkedin.com/in/test-user/",
            "sections": {"main_profile": "John Doe\nSoftware Engineer"},
            "pages_visited": ["https://www.linkedin.com/in/test-user/"],
            "sections_requested": ["main_profile"],
        }
        mock_extractor = _make_mock_extractor(expected)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.person.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )

        from linkedin_mcp_server.tools.person import register_person_tools

        mcp = FastMCP("test")
        register_person_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_person_profile")
        result = await tool_fn(
            "https://www.linkedin.com/in/test-user/",
            mock_context,
        )

        assert result["url"] == "https://www.linkedin.com/in/test-user/"
        mock_extractor.scrape_person.assert_awaited_once_with(
            "test-user",
            ANY,
        )

    async def test_get_person_profile_error(self, mock_context, monkeypatch):
        from linkedin_mcp_server.exceptions import SessionExpiredError

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.person.ensure_authenticated",
            AsyncMock(side_effect=SessionExpiredError()),
        )

        from linkedin_mcp_server.tools.person import register_person_tools

        mcp = FastMCP("test")
        register_person_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_person_profile")
        result = await tool_fn("test-user", mock_context)
        assert result["error"] == "session_expired"


class TestCompanyTools:
    async def test_get_company_profile(
        self, mock_context, patch_tool_deps, monkeypatch
    ):
        expected = {
            "url": "https://www.linkedin.com/company/testcorp/",
            "sections": {"about": "TestCorp\nWe build things"},
            "pages_visited": ["https://www.linkedin.com/company/testcorp/about/"],
            "sections_requested": ["about"],
        }
        mock_extractor = _make_mock_extractor(expected)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.company.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )

        from linkedin_mcp_server.tools.company import register_company_tools

        mcp = FastMCP("test")
        register_company_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_company_profile")
        result = await tool_fn("testcorp", mock_context)
        assert "about" in result["sections"]

    async def test_get_company_posts(self, mock_context, patch_tool_deps, monkeypatch):
        mock_extractor = MagicMock()
        mock_extractor.extract_page = AsyncMock(return_value="Post 1\nPost 2")
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.company.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )

        from linkedin_mcp_server.tools.company import register_company_tools

        mcp = FastMCP("test")
        register_company_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_company_posts")
        result = await tool_fn("testcorp", mock_context)
        assert "posts" in result["sections"]
        assert result["sections"]["posts"] == "Post 1\nPost 2"
        assert result["sections_requested"] == ["posts"]


class TestJobTools:
    async def test_get_job_details(self, mock_context, patch_tool_deps, monkeypatch):
        expected = {
            "url": "https://www.linkedin.com/jobs/view/12345/",
            "sections": {"job_posting": "Software Engineer\nGreat opportunity"},
            "pages_visited": ["https://www.linkedin.com/jobs/view/12345/"],
            "sections_requested": ["job_posting"],
        }
        mock_extractor = _make_mock_extractor(expected)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )

        from linkedin_mcp_server.tools.job import register_job_tools

        mcp = FastMCP("test")
        register_job_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_job_details")
        result = await tool_fn("12345", mock_context)
        assert "job_posting" in result["sections"]

    async def test_search_jobs(self, mock_context, patch_tool_deps, monkeypatch):
        expected = {
            "url": "https://www.linkedin.com/jobs/search/?keywords=python",
            "sections": {"search_results": "Job 1\nJob 2"},
            "pages_visited": ["https://www.linkedin.com/jobs/search/?keywords=python"],
            "sections_requested": ["search_results"],
        }
        mock_extractor = _make_mock_extractor(expected)
        extractor_kwargs = {}

        def _extractor_factory(*args, **kwargs):
            extractor_kwargs.update(kwargs)
            return mock_extractor

        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.LinkedInExtractor",
            _extractor_factory,
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job._extract_structured_job_results",
            AsyncMock(
                return_value=[
                    {
                        "title": "Senior Python Engineer",
                        "company": "Acme",
                        "location": "Remote",
                        "job_id": "4252026496",
                        "url": "https://www.linkedin.com/jobs/view/4252026496/",
                    }
                ]
            ),
        )

        from linkedin_mcp_server.tools.job import register_job_tools

        mcp = FastMCP("test")
        register_job_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "search_jobs")
        result = await tool_fn("python", mock_context, location="Remote")
        assert "search_results" in result["sections"]
        assert result["jobs"][0]["job_id"] == "4252026496"
        assert callable(extractor_kwargs["navigate_fn"])

    async def test_search_jobs_without_location(
        self, mock_context, patch_tool_deps, monkeypatch
    ):
        expected = {
            "url": "https://www.linkedin.com/jobs/search/?keywords=python",
            "sections": {"search_results": "Job 1"},
            "pages_visited": ["https://www.linkedin.com/jobs/search/?keywords=python"],
            "sections_requested": ["search_results"],
        }
        mock_extractor = _make_mock_extractor(expected)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job._extract_structured_job_results",
            AsyncMock(return_value=[]),
        )

        from linkedin_mcp_server.tools.job import register_job_tools

        mcp = FastMCP("test")
        register_job_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "search_jobs")
        result = await tool_fn("python", mock_context)
        assert "search_results" in result["sections"]
        assert result["jobs"] == []
        mock_extractor.search_jobs.assert_awaited_once_with("python", None)

    async def test_search_jobs_falls_back_to_raw_text_parser(
        self, mock_context, patch_tool_deps, monkeypatch
    ):
        expected = {
            "url": "https://www.linkedin.com/jobs/search/?keywords=data+engineer",
            "sections": {
                "search_results": (
                    "Data Engineer\n"
                    "Acme Pte Ltd\n"
                    "Singapore\n"
                    "2 days ago\n"
                )
            },
            "pages_visited": [
                "https://www.linkedin.com/jobs/search/?keywords=data+engineer"
            ],
            "sections_requested": ["search_results"],
        }
        mock_extractor = _make_mock_extractor(expected)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job._extract_structured_job_results",
            AsyncMock(return_value=[]),
        )

        from linkedin_mcp_server.tools.job import register_job_tools

        mcp = FastMCP("test")
        register_job_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "search_jobs")
        result = await tool_fn("data engineer", mock_context)

        assert result["jobs"][0]["title"] == "Data Engineer"
        assert result["jobs"][0]["company"] == "Acme Pte Ltd"
        assert result["jobs"][0]["location"] == "Singapore"
        assert result["jobs"][0]["posting_date"] == "2 days ago"

    async def test_search_jobs_drops_structurally_invalid_rows(
        self, mock_context, patch_tool_deps, monkeypatch
    ):
        expected = {
            "url": "https://www.linkedin.com/jobs/search/?keywords=data+engineer",
            "sections": {"search_results": "Job 1"},
            "pages_visited": [
                "https://www.linkedin.com/jobs/search/?keywords=data+engineer"
            ],
            "sections_requested": ["search_results"],
        }
        mock_extractor = _make_mock_extractor(expected)
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job._extract_structured_job_results",
            AsyncMock(
                return_value=[
                    {
                        "title": "Data engineer in Singapore",
                        "company": "200+ results",
                        "location": None,
                        "job_id": None,
                        "url": None,
                    },
                    {
                        "title": "Senior Python Engineer",
                        "company": "Acme",
                        "location": "Remote",
                        "job_id": "4252026496",
                        "url": "https://www.linkedin.com/jobs/view/4252026496/",
                    },
                ]
            ),
        )

        from linkedin_mcp_server.tools.job import register_job_tools

        mcp = FastMCP("test")
        register_job_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "search_jobs")
        result = await tool_fn("data engineer", mock_context)

        assert len(result["jobs"]) == 1
        assert result["jobs"][0]["title"] == "Senior Python Engineer"

    async def test_search_jobs_releases_browser_lock_on_error(
        self, mock_context, patch_tool_deps, monkeypatch
    ):
        mock_release = MagicMock()
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.release_browser_lock",
            mock_release,
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.get_or_create_browser",
            AsyncMock(side_effect=RuntimeError("jobs broken")),
        )

        from linkedin_mcp_server.tools.job import register_job_tools

        mcp = FastMCP("test")
        register_job_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "search_jobs")
        result = await tool_fn("python", mock_context)
        assert result["error"] == "unknown_error"
        mock_release.assert_called_once()

    async def test_get_job_details_error(self, mock_context, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.ensure_authenticated", AsyncMock()
        )
        mock_browser = MagicMock()
        mock_browser.page = MagicMock()
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.get_or_create_browser",
            AsyncMock(return_value=mock_browser),
        )
        mock_ext = MagicMock()
        mock_ext.scrape_job = AsyncMock(side_effect=RuntimeError("scrape failed"))
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.job.LinkedInExtractor",
            lambda *a, **kw: mock_ext,
        )

        from linkedin_mcp_server.tools.job import register_job_tools

        mcp = FastMCP("test")
        register_job_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_job_details")
        result = await tool_fn("12345", mock_context)
        assert "error" in result


class TestCompanyToolsExtended:
    async def test_get_company_posts_empty_text(
        self, mock_context, patch_tool_deps, monkeypatch
    ):
        mock_extractor = MagicMock()
        mock_extractor.extract_page = AsyncMock(return_value="")
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.company.LinkedInExtractor",
            lambda *a, **kw: mock_extractor,
        )

        from linkedin_mcp_server.tools.company import register_company_tools

        mcp = FastMCP("test")
        register_company_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_company_posts")
        result = await tool_fn("testcorp", mock_context)
        assert result["sections"] == {}

    async def test_get_company_profile_error(self, mock_context, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.company.ensure_authenticated", AsyncMock()
        )
        mock_browser = MagicMock()
        mock_browser.page = MagicMock()
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.company.get_or_create_browser",
            AsyncMock(return_value=mock_browser),
        )
        mock_ext = MagicMock()
        mock_ext.scrape_company = AsyncMock(
            side_effect=RuntimeError("company scrape failed")
        )
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.company.LinkedInExtractor",
            lambda *a, **kw: mock_ext,
        )

        from linkedin_mcp_server.tools.company import register_company_tools

        mcp = FastMCP("test")
        register_company_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_company_profile")
        result = await tool_fn("testcorp", mock_context)
        assert "error" in result

    async def test_get_company_posts_error(self, mock_context, monkeypatch):
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.company.ensure_authenticated", AsyncMock()
        )
        mock_browser = MagicMock()
        mock_browser.page = MagicMock()
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.company.get_or_create_browser",
            AsyncMock(return_value=mock_browser),
        )
        mock_ext = MagicMock()
        mock_ext.extract_page = AsyncMock(side_effect=RuntimeError("posts failed"))
        monkeypatch.setattr(
            "linkedin_mcp_server.tools.company.LinkedInExtractor",
            lambda *a, **kw: mock_ext,
        )

        from linkedin_mcp_server.tools.company import register_company_tools

        mcp = FastMCP("test")
        register_company_tools(mcp)

        tool_fn = await get_tool_fn(mcp, "get_company_posts")
        result = await tool_fn("testcorp", mock_context)
        assert "error" in result
