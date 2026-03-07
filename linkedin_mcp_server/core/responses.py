"""Standardized response envelopes for MCP read/write tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class WriteResult:
    """Normalized write-tool response payload."""

    status: str
    action: str
    resource_url: str | None
    message: str
    performed_at: str
    data: dict[str, Any] | None = None
    cooldown_until: str | None = None
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReadResult:
    """Normalized read-tool response payload."""

    status: str
    action: str
    data: dict[str, Any]
    performed_at: str
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    """UTC timestamp in ISO-8601 format with trailing Z."""
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def write_success(
    action: str,
    message: str,
    resource_url: str | None = None,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return WriteResult(
        status="success",
        action=action,
        resource_url=resource_url,
        message=message,
        performed_at=now_iso(),
        data=data,
        warnings=warnings or [],
    ).to_dict()


def write_dry_run(
    action: str,
    description: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return WriteResult(
        status="dry_run",
        action=action,
        resource_url=None,
        message=description,
        performed_at=now_iso(),
        data=data,
        warnings=[],
    ).to_dict()


def write_error(
    action: str,
    message: str,
    error_code: str = "write_error",
    cooldown_until: str | None = None,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return WriteResult(
        status="error",
        action=action,
        resource_url=None,
        message=message,
        performed_at=now_iso(),
        data=data,
        cooldown_until=cooldown_until,
        warnings=warnings or [],
        error_code=error_code,
    ).to_dict()


def write_quota_exceeded(
    action: str,
    message: str,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return WriteResult(
        status="quota_exceeded",
        action=action,
        resource_url=None,
        message=message,
        performed_at=now_iso(),
        data=data,
        warnings=warnings or [],
        error_code="quota_exceeded",
    ).to_dict()


def read_success(
    action: str,
    data: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return ReadResult(
        status="success",
        action=action,
        data=data,
        performed_at=now_iso(),
        warnings=warnings or [],
    ).to_dict()


def read_error(
    action: str,
    message: str,
    error_code: str = "read_error",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return ReadResult(
        status="error",
        action=action,
        data={},
        performed_at=now_iso(),
        warnings=warnings or [],
        error_code=error_code,
        message=message,
    ).to_dict()
