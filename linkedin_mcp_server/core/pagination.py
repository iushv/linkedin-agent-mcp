"""Helpers for paginated MCP tool responses."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Generic, Protocol, TypeVar, cast


class _DataclassInstance(Protocol):
    __dataclass_fields__: dict[str, Any]

T = TypeVar("T")


@dataclass
class PaginatedResponse(Generic[T]):
    """Standard envelope for paginated tool results."""

    results: list[T]
    total: int | None
    page: int
    has_next: bool
    next_cursor: str | None
    partial: bool = False
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert dataclass results into JSON-serializable dictionaries."""
        serialized_results: list[Any] = []
        for item in self.results:
            if is_dataclass(item) and not isinstance(item, type):
                serialized_results.append(asdict(cast(_DataclassInstance, item)))
            else:
                serialized_results.append(item)

        return {
            "results": serialized_results,
            "total": self.total,
            "page": self.page,
            "has_next": self.has_next,
            "next_cursor": self.next_cursor,
            "partial": self.partial,
            "warnings": self.warnings,
        }


def encode_next_cursor(page: int) -> str:
    """Encode the next page into an opaque cursor."""
    payload = json.dumps({"page": page}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_cursor(next_cursor: str | None, page: int | None = None) -> int:
    """Decode a cursor, falling back to the explicit page number or page 1."""
    if next_cursor:
        try:
            decoded = base64.urlsafe_b64decode(next_cursor.encode("ascii"))
            payload = json.loads(decoded.decode("utf-8"))
            cursor_page = int(payload["page"])
            if cursor_page > 0:
                return cursor_page
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass

    if page is not None and page > 0:
        return page
    return 1


def build_paginated_response(
    results: list[T],
    *,
    page: int,
    limit: int,
    total: int | None = None,
    partial: bool = False,
    warnings: list[str] | None = None,
) -> PaginatedResponse[T]:
    """Build a paginated response using the current page and limit."""
    has_next = False
    if total is not None:
        has_next = page * limit < total
    elif len(results) >= limit:
        has_next = True

    next_cursor = encode_next_cursor(page + 1) if has_next else None
    return PaginatedResponse(
        results=results,
        total=total,
        page=page,
        has_next=has_next,
        next_cursor=next_cursor,
        partial=partial,
        warnings=warnings,
    )
