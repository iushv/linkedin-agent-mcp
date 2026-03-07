"""
FastMCP server implementation for LinkedIn integration with tool registration.

Creates and configures the MCP server with comprehensive LinkedIn tool suite including
person profiles, company data, job information, and session management capabilities.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

from fastmcp import FastMCP

from linkedin_mcp_server.drivers.browser import close_browser
from linkedin_mcp_server.tools.company import register_company_tools
from linkedin_mcp_server.tools.engagement import register_engagement_tools
from linkedin_mcp_server.tools.feed import register_feed_tools
from linkedin_mcp_server.tools.job import register_job_tools
from linkedin_mcp_server.tools.messaging import register_messaging_tools
from linkedin_mcp_server.tools.network import register_network_tools
from linkedin_mcp_server.tools.people import register_people_tools
from linkedin_mcp_server.tools.person import register_person_tools
from linkedin_mcp_server.tools.post import register_post_tools
from linkedin_mcp_server.tools.profile import register_profile_tools
from linkedin_mcp_server.tools.recommendations import register_recommendation_tools
from linkedin_mcp_server.tools.saved_jobs import register_saved_job_tools

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastMCP) -> AsyncIterator[None]:
    """Manage server lifecycle - cleanup browser on shutdown."""
    logger.info("LinkedIn MCP Server starting...")
    yield
    logger.info("LinkedIn MCP Server shutting down...")
    await close_browser()


def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server with all LinkedIn tools."""
    mcp = FastMCP("linkedin_scraper", lifespan=lifespan)

    # Register all tools
    register_person_tools(mcp)
    register_company_tools(mcp)
    register_job_tools(mcp)
    register_people_tools(mcp)
    register_post_tools(mcp)
    register_saved_job_tools(mcp)
    register_profile_tools(mcp)
    register_recommendation_tools(mcp)
    register_engagement_tools(mcp)
    register_messaging_tools(mcp)
    register_network_tools(mcp)
    register_feed_tools(mcp)

    # Register session management tool
    @mcp.tool()
    async def close_session() -> Dict[str, Any]:
        """Close the current browser session and clean up resources."""
        try:
            await close_browser()
            return {
                "status": "success",
                "message": "Successfully closed the browser session and cleaned up resources",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Error closing browser session: {str(e)}",
            }

    return mcp
