"""Tests for server.py: tool registration and lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

EXPECTED_TOOLS = {
    "get_person_profile",
    "get_company_profile",
    "get_company_posts",
    "get_job_details",
    "search_jobs",
    "search_people",
    "get_company_people",
    "save_job",
    "get_saved_jobs",
    "update_profile_headline",
    "set_open_to_work",
    "add_profile_skills",
    "set_featured_skills",
    "get_job_recommendations",
    "create_post",
    "create_poll",
    "delete_post",
    "repost",
    "react_to_post",
    "comment_on_post",
    "reply_to_comment",
    "like_comment",
    "get_conversations",
    "read_conversation",
    "send_message",
    "send_connection_request",
    "get_pending_invitations",
    "respond_to_invitation",
    "follow_person",
    "browse_feed",
    "get_my_post_analytics",
    "get_profile_analytics",
    "close_session",
}


class TestServerRegistration:
    def test_all_expected_tools_registered(self):
        from linkedin_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        registered = {name for name in mcp._tool_manager._tools}
        missing = EXPECTED_TOOLS - registered
        assert not missing, f"Missing tools: {missing}"

    @pytest.mark.asyncio
    async def test_close_session_callable(self):
        from linkedin_mcp_server.server import create_mcp_server

        mcp = create_mcp_server()
        tool = await mcp.get_tool("close_session")
        assert tool is not None

    @pytest.mark.asyncio
    async def test_lifespan_closes_browser(self):
        from linkedin_mcp_server.server import create_mcp_server, lifespan

        mcp = create_mcp_server()

        with patch(
            "linkedin_mcp_server.server.close_browser", new_callable=AsyncMock
        ) as mock_close:
            async with lifespan(mcp):
                pass
            mock_close.assert_awaited_once()
