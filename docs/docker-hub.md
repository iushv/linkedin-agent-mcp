# LinkedIn MCP Server

A Model Context Protocol (MCP) server that connects AI assistants to LinkedIn. Access profiles, companies, and job postings through a Docker container.

## Features

- **Profile Access**: Get detailed LinkedIn profile information
- **Company Profiles**: Extract comprehensive company data
- **Job Details**: Retrieve job posting information
- **Job Search**: Search for jobs with keywords and location filters, including structured `jobs` entries with `title`, `company`, `location`, `job_id`, and `url` when available
- **People Search**: Find LinkedIn members by company, background, title keywords, and location
- **Saved Jobs Queue**: Save jobs and read the current saved-jobs list
- **Profile Job Search Controls**: Update headline, Open To Work, and profile skills with preview-first flows
- **Company Posts**: Get recent posts from a company's LinkedIn feed

## Structured Outputs

- `search_jobs` preserves the raw `sections.search_results` text and adds a structured `jobs` list for downstream automation.
- `search_people` and `get_company_people` return paginated `results` arrays with normalized person cards and resolver metadata. `search_people` also accepts `match_mode=auto|strict|broad` to control fallback broadening.
- `get_saved_jobs` and `get_job_recommendations` return paginated `jobs` arrays.
- Profile-write tools return standardized write envelopes with additive `data` for previews and confirmed changes.
- `get_my_post_analytics` returns parsed post entries under `data.posts`, including `author`, `url`, `text_preview`, `time_ago`, `reactions`, `comments`, `reposts`, and `impressions`.

## Quick Start

Create a browser profile locally, then mount it into Docker.

**Step 1: Create profile using uvx (one-time setup)**

```bash
uvx linkedin-scraper-mcp --login
```

**Step 2: Configure Claude Desktop with Docker**

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "~/.linkedin-mcp:/home/pwuser/.linkedin-mcp",
        "stickerdaniel/linkedin-mcp-server:latest"
      ]
    }
  }
}
```

> **Note:** Docker containers don't have a display server, so you can't use the `--login` command in Docker. Create a profile on your host first.
>
> **Note:** `stdio` is the default transport. Add `--transport streamable-http` only when you specifically want HTTP mode.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `USER_DATA_DIR` | `~/.linkedin-mcp/profile` | Path to persistent browser profile directory |
| `LOG_LEVEL` | `WARNING` | Logging level: DEBUG, INFO, WARNING, ERROR |
| `TIMEOUT` | `5000` | Browser timeout in milliseconds |
| `USER_AGENT` | - | Custom browser user agent |
| `TRANSPORT` | `stdio` | Transport mode: stdio, streamable-http |
| `HOST` | `127.0.0.1` | HTTP server host (for streamable-http transport) |
| `PORT` | `8000` | HTTP server port (for streamable-http transport) |
| `HTTP_PATH` | `/mcp` | HTTP server path (for streamable-http transport) |
| `SLOW_MO` | `0` | Delay between browser actions in ms (debugging) |
| `VIEWPORT` | `1280x720` | Browser viewport size as WIDTHxHEIGHT |
| `CHROME_PATH` | - | Path to Chrome/Chromium executable (rarely needed in Docker) |

**Example with custom timeout:**

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "~/.linkedin-mcp:/home/pwuser/.linkedin-mcp",
        "-e", "TIMEOUT=10000",
        "stickerdaniel/linkedin-mcp-server"
      ]
    }
  }
}
```

## Repository

- **Source**: <https://github.com/stickerdaniel/linkedin-mcp-server>
- **License**: Apache 2.0
