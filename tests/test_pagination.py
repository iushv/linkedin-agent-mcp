from linkedin_mcp_server.core.pagination import (
    PaginatedResponse,
    build_paginated_response,
    decode_cursor,
    encode_next_cursor,
)
from linkedin_mcp_server.core.schemas import PersonCard


class TestCursorHelpers:
    def test_decode_cursor_takes_precedence_over_page(self):
        cursor = encode_next_cursor(4)
        assert decode_cursor(cursor, page=2) == 4

    def test_decode_cursor_falls_back_to_page(self):
        assert decode_cursor(None, page=3) == 3

    def test_decode_cursor_defaults_to_one(self):
        assert decode_cursor(None, page=None) == 1


class TestPaginatedResponse:
    def test_build_paginated_response_with_total(self):
        response = build_paginated_response(
            [PersonCard(name="Priya", profile_url="https://linkedin.com/in/priya")],
            page=1,
            limit=10,
            total=25,
        )
        assert response.has_next is True
        assert response.next_cursor is not None

    def test_build_paginated_response_without_total_uses_limit(self):
        response = build_paginated_response(
            [PersonCard(name="Priya", profile_url="https://linkedin.com/in/priya")],
            page=1,
            limit=10,
            total=None,
        )
        assert response.has_next is False
        assert response.next_cursor is None

    def test_to_dict_serializes_dataclass_results(self):
        response = PaginatedResponse(
            results=[
                PersonCard(name="Priya", profile_url="https://linkedin.com/in/priya")
            ],
            total=1,
            page=1,
            has_next=False,
            next_cursor=None,
        )
        result = response.to_dict()
        assert result["results"][0]["name"] == "Priya"
