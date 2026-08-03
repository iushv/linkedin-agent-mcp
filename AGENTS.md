# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

**Environment Setup:**

- Use `uv` for dependency management: `uv sync` (installs all dependencies)
- Development dependencies: `uv sync --group dev`
- Bump version: `uv version --bump minor` (or `major`, `patch`) - this is the **only manual step** for a release. The GitHub Actions release workflow (`.github/workflows/release.yml`) automatically handles: manifest.json/docker-compose.yml version updates, git tag, Docker build & push, DXT extension, GitHub release, and PyPI publish. After the workflow completes, manually file a PR in the MCP registry to update the version.
- Install browser: `uv run patchright install chromium`
- Patchright bump checklist:
  1. Change `patchright==X.Y.Z` in `pyproject.toml`
  2. Run `uv lock && uv sync`
  3. Run `uv run patchright install chromium`
  4. Run `uv run python scripts/diag_dom.py`
- Run server locally: `uv run -m linkedin_mcp_server --no-headless`
- Run via uvx (PyPI): `uvx linkedin-scraper-mcp`
- Run in Docker: `docker run -it --rm -v ~/.linkedin-mcp:/home/pwuser/.linkedin-mcp iushv/linkedin-agent-mcp:latest`

**Code Quality:**

- Lint: `uv run ruff check .` (auto-fix with `--fix`)
- Format: `uv run ruff format .`
- Type check: `uv run ty check` (using ty, not mypy)
- Tests: `uv run pytest` (with coverage: `uv run pytest --cov`)
- Pre-commit hooks: `uv run pre-commit install` then `uv run pre-commit run --all-files`

**Docker Commands:**

- Build: `docker build -t linkedin-agent-mcp .`
- Login: Use uvx locally first: `uvx linkedin-scraper-mcp --login`

## Architecture Overview

This is a **LinkedIn MCP (Model Context Protocol) Server** that enables AI assistants to interact with LinkedIn through web scraping. The codebase follows a two-phase startup pattern:

1. **Authentication Phase** (`authentication.py`) - Validates LinkedIn browser profile exists
2. **Server Runtime Phase** (`server.py`) - Runs FastMCP server with tool registration

**Core Components:**

- `cli_main.py` - Entry point with CLI argument parsing and orchestration
- `server.py` - FastMCP server setup and tool registration
- `tools/` - LinkedIn scraping tools (person, company, job profiles)
- `drivers/browser.py` - Patchright browser management with persistent profile (singleton)
- `core/` - Inlined browser, auth, and utility code (replaces `linkedin_scraper` dependency)
- `scraping/` - innerText extraction engine with Flag-based section selection
- `config/` - Configuration management (schema, loaders)
- `authentication.py` - LinkedIn profile-based authentication

**Tool Categories:**

- **Person Tools** (`tools/person.py`) - Profile scraping with explicit section selection
- **People Search Tools** (`tools/people.py`) - Member discovery for warm-intro workflows
- **Company Tools** (`tools/company.py`) - Company profile and posts extraction
- **Job Tools** (`tools/job.py`) - Job posting details and search functionality
- **Saved Job Tools** (`tools/saved_jobs.py`) - Saved-jobs queue management
- **Profile Tools** (`tools/profile.py`) - Headline, Open To Work, and skill updates
- **Recommendation Tools** (`tools/recommendations.py`) - Personalized job recommendations

**Available MCP Tools (36, ✍️ = write tool with confirm/dry_run gating):**

| Category | Tools |
|----------|-------|
| Profiles & people | `get_person_profile`, `get_company_profile`, `get_company_posts`, `search_people`, `get_company_people` |
| Jobs | `search_jobs`, `get_job_details`, `save_job` ✍️, `get_saved_jobs`, `get_job_recommendations` |
| Feed & analytics | `browse_feed`, `get_post_reactions`, `get_post_commenters`, `get_my_post_analytics`, `get_profile_analytics` |
| Publishing | `create_post` ✍️, `create_poll` ✍️, `repost` ✍️, `delete_post` ✍️ (destructive) |
| Engagement | `react_to_post` ✍️, `comment_on_post` ✍️, `reply_to_comment` ✍️, `like_comment` ✍️, `get_engagement_health` |
| Messaging | `get_conversations`, `read_conversation`, `send_message` ✍️ |
| Network | `send_connection_request` ✍️, `get_pending_invitations`, `respond_to_invitation` ✍️, `follow_person` ✍️ |
| Own profile | `update_profile_headline` ✍️, `set_open_to_work` ✍️, `add_profile_skills` ✍️, `set_featured_skills` ✍️ |
| Session | `close_session` |

Write-tool safety: confirmation required (`confirm=true` or allowlist in `~/.linkedin-mcp/config.json`), `dry_run` previews, daily/session quotas, CAPTCHA cooldown escalation, and a hash-only audit log at `~/.linkedin-mcp/audit.log`. The authoritative tool list lives in `tests/test_server.py::EXPECTED_TOOLS` — update it, this table, and README.md together when adding tools.

**Tool Return Format:**

Legacy extractor tools return: `{url, sections: {name: raw_text}, pages_visited, sections_requested}`

Additive structured fields:

- `search_jobs` also returns `jobs`, a structured list with `title`, `company`, `location`, `job_id`, and `url` when those fields can be resolved from the LinkedIn DOM.
- `search_people` and `get_company_people` return paginated `results` arrays with normalized person-card fields plus `filters_applied` and `warnings`. `search_people.match_mode` controls how aggressively the tool broadens when exact matches are sparse.
- `get_saved_jobs` and `get_job_recommendations` return paginated `jobs` arrays with normalized job-card fields.
- Profile-write tools return standardized write envelopes and surface preview/change details under `data`.
- `browse_feed` and `get_my_post_analytics` add `post_urn` alongside `url` so feed/activity posts remain actionable even when LinkedIn hides a visible permalink.
- `get_post_reactions` and `get_post_commenters` return paginated `results` arrays for post-engagement outreach workflows.
- `get_conversations` adds `thread_url` and `participant_profile_url`, and `read_conversation` accepts `thread_url` in addition to `thread_id` / `profile_url`.
- Engagement-style write tools accept canonical post URLs, relative post paths, or raw `urn:li:activity:*` references and apply a temporary engagement cooldown after CAPTCHA/checkpoint challenges.
- `get_my_post_analytics` returns parsed post entries under `data.posts` with `author`, `url`, `post_urn`, `text_preview`, `time_ago`, `reactions`, `comments`, `reposts`, and `impressions`.

**Scraping Architecture (`scraping/`):**

- `fields.py` - `PersonScrapingFields` and `CompanyScrapingFields` Flag enums
- `extractor.py` - `LinkedInExtractor` class using navigate-scroll-innerText pattern
- **One flag = one navigation.** Each `PersonScrapingFields` / `CompanyScrapingFields` flag must map to exactly one page navigation. Never combine multiple URLs behind a single flag.

**Core Subpackage (`core/`):**

- `exceptions.py` - Exception hierarchy (AuthenticationError, RateLimitError, etc.)
- `browser.py` - `BrowserManager` with persistent context and cookie import/export
- `auth.py` - `is_logged_in()`, `wait_for_manual_login()`, `warm_up_browser()`
- `utils.py` - `detect_rate_limit()`, `scroll_to_bottom()`, `handle_modal_close()`

**Authentication Flow:**

- Uses persistent browser profile at `~/.linkedin-mcp/profile/`
- Run with `--login` to create a profile via browser login

**Transport Modes:**

- `stdio` (default) - Standard I/O for CLI MCP clients
- `streamable-http` - HTTP server mode for web-based MCP clients

## Development Notes

- **Python Version:** Requires Python 3.12+
- **Package Manager:** Uses `uv` for fast dependency resolution
- **Browser:** Uses Patchright (anti-detection Playwright fork) with Chromium
- **Logging:** Configurable levels, JSON format for non-interactive mode
- **Error Handling:** Comprehensive exception handling for LinkedIn rate limits, captchas, etc.

**Key Dependencies:**

- `fastmcp` - MCP server framework
- `patchright` - Anti-detection browser automation (Playwright fork)

**Configuration:**

- CLI arguments with comprehensive help (`--help`)
- Browser profile stored at `~/.linkedin-mcp/profile/`

**Commit Message Format:**

- Follow conventional commits: `type(scope): subject`
- Types: feat, fix, docs, style, refactor, test, chore, perf, ci
- Keep subject <50 chars, imperative mood

## Commit Message Guidelines

**Commit Message Rules:**

- Always use the commit message format type(scope): subject
- Types: feat, fix, docs, style, refactor, test, chore, perf, ci
- Keep subject <50 chars, imperative mood

## Important Development Notes

### Development Workflow

- Never sign a PR or commit with Claude Code
- When implementing a new feature/fix, follow this process:
  1. Check open issues. If no issue exists for the feature, create one that follows the feature issue template.
  2. Create a new branch from `main` and name it `feature/issue-number-short-description`
  3. Implement the feature
  4. Test the feature
  5. Make sure the README.md, docs/docker-hub.md and AGENTS.md is updated with the new feature
  6. Create a PR with a short description of the feature/fix
  7. First review the PR with ai agents.
  8. Manually review the PR and merge it if it's approved. Do not squash the commits.
  9. Delete the branch after the PR is merged.

## btca

When you need up-to-date information about technologies used in this project, use btca to query source repositories directly.

**Available resources**: fastmcp, patchright, pytest, ruff, ty, uv, inquirer, pythonDotenv, pyperclip, preCommit

### Usage

```bash
btca ask -r <resource> -q "<question>"
```

Use multiple `-r` flags to query multiple resources at once:

```bash
btca ask -r fastmcp -r patchright -q "How do I set up browser context with FastMCP tools?"
```
