#!/bin/bash
export HOME=/Users/ayushkumar

mkdir -p /tmp/linkedin-mcp-debug
echo "=== Started at $(date) ===" >> /tmp/linkedin-mcp-debug/stderr.log
echo "HOME=$HOME, USER=$USER, PATH=$PATH" >> /tmp/linkedin-mcp-debug/stderr.log

exec /Users/ayushkumar/.local/bin/linkedin-scraper-mcp \
  --transport stdio \
  --log-level DEBUG \
  2>>/tmp/linkedin-mcp-debug/stderr.log
