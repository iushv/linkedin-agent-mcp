#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${HOME:-}" ]]; then
  echo "HOME must be set before running run_mcp.sh" >&2
  exit 1
fi

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export UV_TOOL_DIR="${UV_TOOL_DIR:-${HOME}/.local/share/uv/tools}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${HOME}/.cache/uv}"
export TIMEOUT="${TIMEOUT:-60000}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v uv >/dev/null 2>&1 && [[ -f "${script_dir}/pyproject.toml" ]]; then
  if [[ -z "${CHROME_PATH:-}" ]]; then
    if ! uv run --directory "${script_dir}" python -c "from linkedin_mcp_server.drivers.browser import ensure_browser_binary; ensure_browser_binary(headless=True)"; then
      echo "ERROR: Chromium browser pre-flight failed. Run: uv run patchright install chromium" >&2
      exit 1
    fi
  fi
  exec uv run --directory "${script_dir}" -m linkedin_mcp_server --transport stdio --log-level WARNING "$@"
fi

if command -v uvx >/dev/null 2>&1; then
  exec uvx linkedin-scraper-mcp --transport stdio --log-level WARNING "$@"
fi

if command -v linkedin-scraper-mcp >/dev/null 2>&1; then
  exec linkedin-scraper-mcp --transport stdio --log-level WARNING "$@"
fi

echo "Could not find 'uv', 'uvx', or 'linkedin-scraper-mcp' in PATH." >&2
exit 127
