#!/bin/bash
export HOME=/Users/ayushkumar
export PATH="/Users/ayushkumar/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export UV_TOOL_DIR="/Users/ayushkumar/.local/share/uv/tools"
export UV_CACHE_DIR="/Users/ayushkumar/.cache/uv"
exec /Users/ayushkumar/.local/bin/uvx linkedin-scraper-mcp --transport stdio --log-level WARNING
