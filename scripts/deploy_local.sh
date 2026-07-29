#!/usr/bin/env bash
# Deploy this checkout to ~/.local/bin/linkedin-scraper-mcp reproducibly.
#
# Encodes four failure modes that each cost real debugging time:
#
#   1. `uv tool install --force .` SILENTLY NO-OPS on a same-version rebuild.
#      The code changes, the version doesn't, and the deploy stays stale.
#   2. uv's wheel cache can serve a build from before files were deleted, so a
#      full uninstall/reinstall still ships modules that no longer exist in
#      source. Only `uv cache clean` clears it.
#   3. `uv tool install` DOES NOT read uv.lock. It resolves transitive deps
#      fresh, so production can get a newer minor than CI ever tested. On
#      2026-07-29 that shipped fastmcp 3.4.5 against a lockfile pinning 3.4.2.
#   4. Rewriting site-packages under a RUNNING MCP server breaks that server
#      until it restarts: it holds the old modules and lazily imports the new
#      ones. Symptom is an ImportError from a symbol that plainly exists.
#
# Usage: scripts/deploy_local.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

CONSTRAINTS="$(mktemp -t linkedin-mcp-constraints)"
trap 'rm -f "$CONSTRAINTS"' EXIT

echo "==> Exporting locked production dependencies"
uv export --frozen --no-dev --no-emit-project --no-hashes \
  --format requirements-txt -o "$CONSTRAINTS" -q

echo "==> Clearing stale build artifacts and wheel cache"
rm -rf build/ dist/ ./*.egg-info 2>/dev/null || true
uv cache clean linkedin-scraper-mcp >/dev/null 2>&1 || true

echo "==> Installing (constrained to uv.lock versions)"
uv tool uninstall linkedin-scraper-mcp >/dev/null 2>&1 || true
uv tool install --constraints "$CONSTRAINTS" . 2>&1 | tail -2

echo
echo "==> Verifying deployed build matches this checkout"
CHECKOUT_FP="$(uv run --no-sync -m linkedin_mcp_server --doctor \
  --user-data-dir /nonexistent 2>/dev/null | awk '/build:/{print $2}')"
DEPLOYED_FP="$(cd /tmp && "$HOME/.local/bin/linkedin-scraper-mcp" --doctor \
  --user-data-dir /nonexistent 2>/dev/null | awk '/build:/{print $2}')"

echo "    checkout: ${CHECKOUT_FP:-unknown}"
echo "    deployed: ${DEPLOYED_FP:-unknown}"
if [[ -n "$CHECKOUT_FP" && "$CHECKOUT_FP" == "$DEPLOYED_FP" ]]; then
  echo "    ✅ match"
else
  echo "    ❌ MISMATCH — deploy did not take effect" >&2
  exit 1
fi

echo
echo "⚠️  RESTART CLAUDE DESKTOP before using the LinkedIn MCP."
echo "    site-packages changed underneath any running server process; until it"
echo "    restarts it will fail with import errors for symbols that do exist."
