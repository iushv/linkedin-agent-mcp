"""Tests for logging_config.py formatters and configuration."""

from __future__ import annotations

import json
import logging

from linkedin_mcp_server.logging_config import (
    CompactFormatter,
    MCPJSONFormatter,
    configure_logging,
)


class TestMCPJSONFormatter:
    def test_valid_json_output(self):
        formatter = MCPJSONFormatter()
        record = logging.LogRecord(
            name="linkedin_mcp_server.tools.post",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="test message",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "WARNING"
        assert data["message"] == "test message"
        assert "timestamp" in data
        assert "logger" in data


class TestCompactFormatter:
    def test_strips_prefix(self):
        formatter = CompactFormatter()
        record = logging.LogRecord(
            name="linkedin_mcp_server.tools.post",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="compact test",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        assert "tools.post" in output
        assert "linkedin_mcp_server.tools.post" not in output


class TestConfigureLogging:
    def test_level_propagates(self):
        configure_logging(log_level="DEBUG", json_format=False)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_noisy_loggers_silenced(self):
        configure_logging(log_level="DEBUG", json_format=False)
        assert logging.getLogger("urllib3").level >= logging.ERROR
