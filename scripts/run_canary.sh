#!/usr/bin/env bash
# Weekly live canary: detects LinkedIn DOM drift before it bites mid-workflow.
#
# Starts the MCP server from this checkout on a dedicated port, runs the
# read-only live harness against the real logged-in profile, and writes a
# timestamped JSON report. Report-only — no LinkedIn writes, no deletes.
#
# Usage: scripts/run_canary.sh [report_dir]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="${1:-$HOME/Documents/Claude/linkedin-canary}"
PORT=8091
URL="http://127.0.0.1:${PORT}/mcp"
STAMP="$(date +%Y-%m-%d_%H%M)"
REPORT="${REPORT_DIR}/canary_${STAMP}.json"
SERVER_LOG="${REPORT_DIR}/canary_${STAMP}.server.log"

# Profile isolation: Claude Desktop keeps its own MCP server running against
# ~/.linkedin-mcp/profile, and Chromium allows only one owner of a persistent
# profile. Sharing it makes contention look like extraction defects, so the
# canary always runs against a throwaway copy.
CANARY_PROFILE="${TMPDIR:-/tmp}/linkedin-canary-profile"

mkdir -p "$REPORT_DIR"

if lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
  echo "SKIP: port ${PORT} already in use." | tee "${REPORT_DIR}/canary_${STAMP}.skipped"
  exit 0
fi

cd "$REPO_DIR"

rm -rf "$CANARY_PROFILE"
if [[ -d "$HOME/.linkedin-mcp/profile" ]]; then
  cp -R "$HOME/.linkedin-mcp/profile" "$CANARY_PROFILE"
  # A copied SingletonLock points at the original process; drop the lock files
  # so this instance can claim its own copy.
  rm -f "$CANARY_PROFILE"/Singleton* 2>/dev/null || true
else
  echo "SKIP: no LinkedIn profile at ~/.linkedin-mcp/profile — run --login first." \
    | tee "${REPORT_DIR}/canary_${STAMP}.skipped"
  exit 0
fi

uv run --no-sync -m linkedin_mcp_server \
  --transport streamable-http --port ${PORT} --log-level WARNING \
  --user-data-dir "$CANARY_PROFILE" \
  >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; rm -rf "$CANARY_PROFILE"' EXIT

# Wait for the HTTP endpoint to come up (max ~30s).
for _ in $(seq 1 30); do
  if lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then break; fi
  sleep 1
done

uv run --no-sync python scripts/test_live_tools.py \
  --url "$URL" \
  --tool get_engagement_health \
  --tool browse_feed \
  --tool get_my_post_analytics \
  --tool get_conversations \
  --tool get_pending_invitations \
  --tool get_person_profile \
  --tool get_company_posts \
  --tool search_people \
  --tool send_connection_request \
  --feed-count 5 --analytics-limit 5 --conversation-limit 5 \
  --tool-timeout 180 --read-sleep 5 \
  --json-out "$REPORT"
HARNESS_RC=$?

cat >&2 <<'GAPS'

KNOWN GAP: send_connection_request runs with dry_run=true, which short-circuits
in run_write_tool BEFORE any navigation. This canary therefore covers its
session/envelope path only and CANNOT detect the nav-overlay failure. A green
canary is not evidence that sending invitations works.
GAPS

echo "Canary report: $REPORT (exit ${HARNESS_RC})"
exit ${HARNESS_RC}
