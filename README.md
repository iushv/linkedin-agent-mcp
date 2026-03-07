# LinkedIn Agent MCP

<p align="left">
  <a href="https://pypi.org/project/linkedin-scraper-mcp/" target="_blank"><img src="https://img.shields.io/pypi/v/linkedin-scraper-mcp?color=blue" alt="PyPI"></a>
  <a href="https://github.com/iushv/linkedin-agent-mcp/actions/workflows/ci.yml" target="_blank"><img src="https://github.com/iushv/linkedin-agent-mcp/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI Status"></a>
  <a href="https://github.com/iushv/linkedin-agent-mcp/actions/workflows/release.yml" target="_blank"><img src="https://github.com/iushv/linkedin-agent-mcp/actions/workflows/release.yml/badge.svg?branch=main" alt="Release"></a>
  <a href="https://github.com/iushv/linkedin-agent-mcp/blob/main/LICENSE" target="_blank"><img src="https://img.shields.io/badge/License-Apache%202.0-brightgreen?labelColor=32383f" alt="License"></a>
</p>

Through this LinkedIn Agent MCP, AI assistants like Claude can connect to your LinkedIn. Access profiles and companies, search for jobs and people, manage saved jobs, update job-search profile settings, and inspect analytics.

## Attribution

This project was originally bootstrapped from Daniel Sticker's LinkedIn MCP work and has since been extended into a broader LinkedIn automation and job-search manager. Credit for the original foundation goes to Daniel Sticker and the original `linkedin-mcp-server` project.

## Installation Methods

[![uvx](https://img.shields.io/badge/uvx-Quick_Install-de5fe9?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNDEiIGhlaWdodD0iNDEiIHZpZXdCb3g9IjAgMCA0MSA0MSIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTS01LjI4NjE5ZS0wNiAwLjE2ODYyOUwwLjA4NDMwOTggMjAuMTY4NUwwLjE1MTc2MiAzNi4xNjgzQzAuMTYxMDc1IDM4LjM3NzQgMS45NTk0NyA0MC4xNjA3IDQuMTY4NTkgNDAuMTUxNEwyMC4xNjg0IDQwLjA4NEwzMC4xNjg0IDQwLjA0MThMMzEuMTg1MiA0MC4wMzc1QzMzLjM4NzcgNDAuMDI4MiAzNS4xNjgzIDM4LjIwMjYgMzUuMTY4MyAzNlYzNkwzNy4wMDAzIDM2TDM3LjAwMDMgMzkuOTk5Mkw0MC4xNjgzIDM5Ljk5OTZMMzkuOTk5NiAtOS45NDY1M2UtMDdMMjEuNTk5OCAwLjA3NzU2ODlMMjEuNjc3NCAxNi4wMTg1TDIxLjY3NzQgMjUuOTk5OEwyMC4wNzc0IDI1Ljk5OThMMTguMzk5OCAyNS45OTk4TDE4LjQ3NzQgMTYuMDMyTDE4LjM5OTggMC4wOTEwNTkzTC01LjI4NjE5ZS0wNiAwLjE2ODYyOVoiIGZpbGw9IiNERTVGRTkiLz4KPC9zdmc+Cg==)](#-uvx-setup-recommended---universal)
[![Docker](https://img.shields.io/badge/Docker-Universal_MCP-008fe2?style=for-the-badge&logo=docker&logoColor=008fe2)](#-docker-setup)
[![Install DXT Extension](https://img.shields.io/badge/Claude_Desktop_DXT-d97757?style=for-the-badge&logo=anthropic)](#-claude-desktop-dxt-extension)
[![Development](https://img.shields.io/badge/Development-Local-ffdc53?style=for-the-badge&logo=python&logoColor=ffdc53)](#-local-setup-develop--contribute)

<https://github.com/user-attachments/assets/eb84419a-6eaf-47bd-ac52-37bc59c83680>

## Usage Examples

```
Research the background of this candidate https://www.linkedin.com/in/ayushkumar-exl/
```

```
Get this company profile for partnership discussions https://www.linkedin.com/company/inframs/
```

```
Suggest improvements for my CV to target this job posting https://www.linkedin.com/jobs/view/4252026496
```

```
What has Anthropic been posting about recently? https://www.linkedin.com/company/anthropicresearch/
```

## Features & Tool Status

| Tool | Description | Status |
|------|-------------|--------|
| `get_person_profile` | Get profile info with explicit section selection (experience, education, interests, honors, languages, contact_info) | Working |
| `get_company_profile` | Extract company information with explicit section selection (posts, jobs) | Working |
| `get_company_posts` | Get recent posts from a company's LinkedIn feed | Working |
| `search_jobs` | Search for jobs with keywords and location filters | Working |
| `get_job_details` | Get detailed information about a specific job posting | Working |
| `search_people` | Search LinkedIn members by keywords, current company, past company, and location; supports `match_mode=auto|strict|broad` | Working |
| `get_company_people` | Find people at a target company with optional past-company and title filters | Working |
| `save_job` | Save a LinkedIn job to the current account's queue | Working |
| `get_saved_jobs` | List the current account's saved jobs with pagination metadata | Working |
| `update_profile_headline` | Update the logged-in profile headline with preview support | Working |
| `set_open_to_work` | Enable or disable Open To Work preferences with preview support | Working |
| `add_profile_skills` | Add new skills to the logged-in profile with preview support | Working |
| `set_featured_skills` | Best-effort featured-skill ordering flow | Experimental |
| `get_job_recommendations` | Read LinkedIn's personalized job recommendations feed | Working |
| `close_session` | Close browser session and clean up resources | Working |

> [!IMPORTANT]
> **Breaking change:** LinkedIn recently made some changes to prevent scraping. The newest version uses [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) with persistent browser profiles instead of Playwright with session files. Old `session.json` files and `LINKEDIN_COOKIE` env vars are no longer supported. Run `--login` again to create a new profile + cookie file that can be mounted in docker. 02/2026

## Structured Helper Fields

Some tools return additive structured fields alongside the existing raw text output.

- `search_jobs` keeps `sections.search_results` and also returns `jobs`, a structured list with `title`, `company`, `location`, `job_id`, and `url` when those values can be resolved from the current LinkedIn DOM.
- `search_people` and `get_company_people` return paginated `results` arrays with normalized `PersonCard` fields and `filters_applied` / `warnings` metadata. `search_people.match_mode` controls whether the tool stays strict, broadens automatically, or runs a broad company/background search immediately.
- `get_saved_jobs` and `get_job_recommendations` return paginated `jobs` arrays with normalized `JobCard` fields.
- Profile-write tools (`update_profile_headline`, `set_open_to_work`, `add_profile_skills`, `set_featured_skills`) support preview-first flows via `dry_run` or `confirm=false` and return structured write envelopes with additive `data`.
- `get_my_post_analytics` returns the standard read envelope and exposes parsed posts at `data.posts`. Each post object includes `author`, `url`, `text_preview`, `time_ago`, `reactions`, `comments`, `reposts`, and `impressions`.

<br/>
<br/>

## 🚀 uvx Setup (Recommended - Universal)

**Prerequisites:** Install uv and run `uvx patchright install chromium` to set up the browser.

### Installation

**Step 1: Create a session (first time only)**

```bash
uvx linkedin-scraper-mcp --login
```

This opens a browser for you to log in manually (5 minute timeout for 2FA, captcha, etc.). The browser profile is saved to `~/.linkedin-mcp/profile/`.

**Step 2: Client Configuration:**

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "uvx",
      "args": ["linkedin-scraper-mcp"]
    }
  }
}
```

> [!NOTE]
> Sessions may expire over time. If you encounter authentication issues, run `uvx linkedin-scraper-mcp --login` again

### uvx Setup Help

<details>
<summary><b>🔧 Configuration</b></summary>

**Transport Modes:**

- **Default (stdio)**: Standard communication for local MCP servers
- **Streamable HTTP**: For web-based MCP server
- If no transport is specified, the server defaults to `stdio`
- An interactive terminal without explicit transport shows a chooser prompt

**CLI Options:**

- `--login` - Open browser to log in and save persistent profile
- `--no-headless` - Show browser window (useful for debugging scraping issues)
- `--log-level {DEBUG,INFO,WARNING,ERROR}` - Set logging level (default: WARNING)
- `--transport {stdio,streamable-http}` - Optional: force transport mode (default: stdio)
- `--host HOST` - HTTP server host (default: 127.0.0.1)
- `--port PORT` - HTTP server port (default: 8000)
- `--path PATH` - HTTP server path (default: /mcp)
- `--logout` - Clear stored LinkedIn browser profile
- `--timeout MS` - Browser timeout for page operations in milliseconds (default: 5000)
- `--user-data-dir PATH` - Path to persistent browser profile directory (default: ~/.linkedin-mcp/profile)
- `--chrome-path PATH` - Path to Chrome/Chromium executable (for custom browser installations)

**Basic Usage Examples:**

```bash
# Create a session interactively
uvx linkedin-scraper-mcp --login

# Run with debug logging
uvx linkedin-scraper-mcp --log-level DEBUG
```

**HTTP Mode Example (for web-based MCP clients):**

```bash
uvx linkedin-scraper-mcp --transport streamable-http --host 127.0.0.1 --port 8080 --path /mcp
```

Runtime server logs are emitted by FastMCP/Uvicorn.

**Test with mcp inspector:**

1. Install and run mcp inspector ```bunx @modelcontextprotocol/inspector```
2. Click pre-filled token url to open the inspector in your browser
3. Select `Streamable HTTP` as `Transport Type`
4. Set `URL` to `http://localhost:8080/mcp`
5. Connect
6. Test tools

</details>

<details>
<summary><b>❗ Troubleshooting</b></summary>

**Installation issues:**

- Ensure you have uv installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Check uv version: `uv --version` (should be 0.4.0 or higher)

**Session issues:**

- Browser profile is stored at `~/.linkedin-mcp/profile/`
- Make sure you have only one active LinkedIn session at a time

**Login issues:**

- LinkedIn may require a login confirmation in the LinkedIn mobile app for `--login`
- You might get a captcha challenge if you logged in frequently. Run `uvx linkedin-scraper-mcp --login` which opens a browser where you can solve it manually.

**Timeout issues:**

- If pages fail to load or elements aren't found, try increasing the timeout: `--timeout 10000`
- Users on slow connections may need higher values (e.g., 15000-30000ms)
- Can also set via environment variable: `TIMEOUT=10000`

**Custom Chrome path:**

- If Chrome is installed in a non-standard location, use `--chrome-path /path/to/chrome`
- Can also set via environment variable: `CHROME_PATH=/path/to/chrome`

</details>

<br/>
<br/>

## 🐳 Docker Setup

**Prerequisites:** Make sure you have [Docker](https://www.docker.com/get-started/) installed and running.

### Authentication

Docker runs headless (no browser window), so you need to create a browser profile locally first and mount it into the container.

**Step 1: Create profile using uvx (one-time setup)**

```bash
uvx linkedin-scraper-mcp --login
```

This opens a browser window where you log in manually (5 minute timeout for 2FA, captcha, etc.). The browser profile is saved to `~/.linkedin-mcp/profile/`.

**Step 2: Configure Claude Desktop with Docker**

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "~/.linkedin-mcp:/home/pwuser/.linkedin-mcp",
        "iushv/linkedin-agent-mcp:latest"
      ]
    }
  }
}
```

> [!NOTE]
> Sessions may expire over time. If you encounter authentication issues, run `uvx linkedin-scraper-mcp --login` again locally.

> [!NOTE]
> **Why can't I run `--login` in Docker?** Docker containers don't have a display server. Create a profile on your host using the [uvx setup](#-uvx-setup-recommended---universal) and mount it into Docker.

### Docker Setup Help

<details>
<summary><b>🔧 Configuration</b></summary>

**Transport Modes:**

- **Default (stdio)**: Standard communication for local MCP servers
- **Streamable HTTP**: For a web-based MCP server
- If no transport is specified, the server defaults to `stdio`
- An interactive terminal without explicit transport shows a chooser prompt

**CLI Options:**

- `--log-level {DEBUG,INFO,WARNING,ERROR}` - Set logging level (default: WARNING)
- `--transport {stdio,streamable-http}` - Optional: force transport mode (default: stdio)
- `--host HOST` - HTTP server host (default: 127.0.0.1)
- `--port PORT` - HTTP server port (default: 8000)
- `--path PATH` - HTTP server path (default: /mcp)
- `--logout` - Clear stored LinkedIn browser profile
- `--timeout MS` - Browser timeout for page operations in milliseconds (default: 5000)
- `--user-data-dir PATH` - Path to persistent browser profile directory (default: ~/.linkedin-mcp/profile)
- `--chrome-path PATH` - Path to Chrome/Chromium executable (rarely needed in Docker)

> [!NOTE]
> `--login` and `--no-headless` are not available in Docker (no display server). Use the [uvx setup](#-uvx-setup-recommended---universal) to create profiles.

**HTTP Mode Example (for web-based MCP clients):**

```bash
docker run -it --rm \
  -v ~/.linkedin-mcp:/home/pwuser/.linkedin-mcp \
  -p 8080:8080 \
  iushv/linkedin-agent-mcp:latest \
  --transport streamable-http --host 0.0.0.0 --port 8080 --path /mcp
```

Runtime server logs are emitted by FastMCP/Uvicorn.

**Test with mcp inspector:**

1. Install and run mcp inspector ```bunx @modelcontextprotocol/inspector```
2. Click pre-filled token url to open the inspector in your browser
3. Select `Streamable HTTP` as `Transport Type`
4. Set `URL` to `http://localhost:8080/mcp`
5. Connect
6. Test tools

</details>

<details>
<summary><b>❗ Troubleshooting</b></summary>

**Docker issues:**

- Make sure [Docker](https://www.docker.com/get-started/) is installed
- Check if Docker is running: `docker ps`

**Login issues:**

- Make sure you have only one active LinkedIn session at a time
- LinkedIn may require a login confirmation in the LinkedIn mobile app for `--login`
- You might get a captcha challenge if you logged in frequently. Run `uvx linkedin-scraper-mcp --login` which opens a browser where you can solve captchas manually. See the [uvx setup](#-uvx-setup-recommended---universal) for prerequisites.

**Timeout issues:**

- If pages fail to load or elements aren't found, try increasing the timeout: `--timeout 10000`
- Users on slow connections may need higher values (e.g., 15000-30000ms)
- Can also set via environment variable: `TIMEOUT=10000`

**Custom Chrome path:**

- If Chrome is installed in a non-standard location, use `--chrome-path /path/to/chrome`
- Can also set via environment variable: `CHROME_PATH=/path/to/chrome`

</details>

<br/>
<br/>

## 📦 Claude Desktop (DXT Extension)

**Prerequisites:** [Claude Desktop](https://claude.ai/download) and [Docker](https://www.docker.com/get-started/) installed & running

**One-click installation** for Claude Desktop users:

1. Download the [DXT extension](https://github.com/iushv/linkedin-agent-mcp/releases/latest)
2. Double-click to install into Claude Desktop
3. Create a session: `uvx linkedin-scraper-mcp --login`

> [!NOTE]
> Sessions may expire over time. If you encounter authentication issues, run `uvx linkedin-scraper-mcp --login` again.

### DXT Extension Setup Help

<details>
<summary><b>❗ Troubleshooting</b></summary>

**First-time setup timeout:**

- Claude Desktop has a ~60 second connection timeout
- If the Docker image isn't cached, the pull may exceed this timeout
- **Fix:** Pre-pull the image before first use:

  ```bash
  docker pull iushv/linkedin-agent-mcp:2.3.0
  ```

- Then restart Claude Desktop

**Docker issues:**

- Make sure [Docker](https://www.docker.com/get-started/) is installed
- Check if Docker is running: `docker ps`

**Login issues:**

- Make sure you have only one active LinkedIn session at a time
- LinkedIn may require a login confirmation in the LinkedIn mobile app for `--login`
- You might get a captcha challenge if you logged in frequently. Run `uvx linkedin-scraper-mcp --login` which opens a browser where you can solve captchas manually. See the [uvx setup](#-uvx-setup-recommended---universal) for prerequisites.

**Timeout issues:**

- If pages fail to load or elements aren't found, try increasing the timeout: `--timeout 10000`
- Users on slow connections may need higher values (e.g., 15000-30000ms)
- Can also set via environment variable: `TIMEOUT=10000`

</details>

<br/>
<br/>

## 🐍 Local Setup (Develop & Contribute)

**Prerequisites:** [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/) installed

### Installation

```bash
# 1. Clone repository
git clone https://github.com/iushv/linkedin-agent-mcp
cd linkedin-agent-mcp

# 2. Install UV package manager (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Install dependencies
uv sync
uv sync --group dev

# 4. Install Patchright browser
uv run patchright install chromium

# 5. Install pre-commit hooks
uv run pre-commit install

# 6. Create a session (first time only)
uv run -m linkedin_mcp_server --login

# 7. Start the server
uv run -m linkedin_mcp_server
```

### Local Setup Help

<details>
<summary><b>🔧 Configuration</b></summary>

**CLI Options:**

- `--login` - Open browser to log in and save persistent profile
- `--no-headless` - Show browser window (useful for debugging scraping issues)
- `--log-level {DEBUG,INFO,WARNING,ERROR}` - Set logging level (default: WARNING)
- `--transport {stdio,streamable-http}` - Optional: force transport mode (default: stdio)
- `--host HOST` - HTTP server host (default: 127.0.0.1)
- `--port PORT` - HTTP server port (default: 8000)
- `--path PATH` - HTTP server path (default: /mcp)
- `--logout` - Clear stored LinkedIn browser profile
- `--timeout MS` - Browser timeout for page operations in milliseconds (default: 5000)
- `--status` - Check if current session is valid and exit
- `--user-data-dir PATH` - Path to persistent browser profile directory (default: ~/.linkedin-mcp/profile)
- `--slow-mo MS` - Delay between browser actions in milliseconds (default: 0, useful for debugging)
- `--user-agent STRING` - Custom browser user agent
- `--viewport WxH` - Browser viewport size (default: 1280x720)
- `--chrome-path PATH` - Path to Chrome/Chromium executable (for custom browser installations)
- `--help` - Show help

> **Note:** Most CLI options have environment variable equivalents. See `.env.example` for details.

**HTTP Mode Example (for web-based MCP clients):**

```bash
uv run -m linkedin_mcp_server --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

**Live smoke test:**

```bash
uv run -m linkedin_mcp_server --transport streamable-http --host 127.0.0.1 --port 8080 --path /mcp
uv run python scripts/test_live_tools.py --url http://127.0.0.1:8080/mcp
```

Target specific tools, add pacing, or retry `read_conversation` after timeouts:

```bash
uv run python scripts/test_live_tools.py \
  --url http://127.0.0.1:8080/mcp \
  --read-only \
  --tool get_conversations \
  --tool read_conversation \
  --read-sleep 3 \
  --read-conversation-retries 2 \
  --retry-backoff-seconds 8 \
  --json-out output/live-smoke.json
```

Run only the write dry-run tools:

```bash
uv run python scripts/test_live_tools.py \
  --url http://127.0.0.1:8080/mcp \
  --write-only
```

Run a focused `read_conversation` check against a known thread id:

```bash
uv run python scripts/test_live_tools.py \
  --url http://127.0.0.1:8080/mcp \
  --focus-read-conversation \
  --thread-id abc123
```

**Claude Desktop:**

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "uv",
      "args": ["--directory", "/path/to/linkedin-agent-mcp", "run", "-m", "linkedin_mcp_server"]
    }
  }
}
```

`stdio` is used by default for this config.

</details>

<details>
<summary><b>❗ Troubleshooting</b></summary>

**Login issues:**

- Make sure you have only one active LinkedIn session at a time
- LinkedIn may require a login confirmation in the LinkedIn mobile app for `--login`
- You might get a captcha challenge if you logged in frequently. The `--login` command opens a browser where you can solve it manually.

**Scraping issues:**

- Use `--no-headless` to see browser actions and debug scraping problems
- Add `--log-level DEBUG` to see more detailed logging

**Session issues:**

- Browser profile is stored at `~/.linkedin-mcp/profile/`
- Use `--logout` to clear the profile and start fresh

**Python/Patchright issues:**

- Check Python version: `python --version` (should be 3.12+)
- Reinstall Patchright: `uv run patchright install chromium`
- Reinstall dependencies: `uv sync --reinstall`

**Timeout issues:**

- If pages fail to load or elements aren't found, try increasing the timeout: `--timeout 10000`
- Users on slow connections may need higher values (e.g., 15000-30000ms)
- Can also set via environment variable: `TIMEOUT=10000`

**Custom Chrome path:**

- If Chrome is installed in a non-standard location, use `--chrome-path /path/to/chrome`
- Can also set via environment variable: `CHROME_PATH=/path/to/chrome`

</details>

Feel free to open an [issue](https://github.com/iushv/linkedin-agent-mcp/issues) or [PR](https://github.com/iushv/linkedin-agent-mcp/pulls)!

<br/>
<br/>

## Acknowledgements

Built with [FastMCP](https://gofastmcp.com/) and [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python).

⚠️ Use in accordance with [LinkedIn's Terms of Service](https://www.linkedin.com/legal/user-agreement). Web scraping may violate LinkedIn's terms. This tool is for personal use only.

## License

This project is licensed under the Apache 2.0 license.

<br>
