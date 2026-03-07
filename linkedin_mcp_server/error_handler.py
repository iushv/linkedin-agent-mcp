"""
Centralized error handling for LinkedIn MCP Server with structured responses.

Provides DRY approach to error handling across all tools with consistent MCP response
format, specific LinkedIn error categorization, and proper logging integration.
"""

import logging
from typing import Any, Dict

from linkedin_mcp_server.core.exceptions import (
    AuthenticationError,
    ConcurrencyError,
    ElementNotFoundError,
    InteractionError,
    LinkedInScraperException,
    NetworkError,
    ProfileNotFoundError,
    QuotaExceededError,
    RateLimitError,
    ScrapingError,
    SelectorError,
)

from linkedin_mcp_server.exceptions import (
    CredentialsNotFoundError,
    LinkedInMCPError,
    SessionExpiredError,
)

logger = logging.getLogger(__name__)


def handle_tool_error(exception: Exception, context: str = "") -> Dict[str, Any]:
    """
    Handle errors from tool functions and return structured responses.

    Args:
        exception: The exception that occurred
        context: Context about which tool failed

    Returns:
        Structured error response dictionary
    """
    return convert_exception_to_response(exception, context)


def convert_exception_to_response(
    exception: Exception, context: str = ""
) -> Dict[str, Any]:
    """
    Convert an exception to a structured MCP response.

    Args:
        exception: The exception to convert
        context: Additional context about where the error occurred

    Returns:
        Structured error response dictionary
    """
    if isinstance(exception, CredentialsNotFoundError):
        logger.warning("Credentials not found in %s: %s", context, exception)
        return {
            "error": "authentication_not_found",
            "message": str(exception),
            "resolution": "Run with --login to create a browser profile.",
        }

    elif isinstance(exception, SessionExpiredError):
        logger.warning("Session expired in %s: %s", context, exception)
        return {
            "error": "session_expired",
            "message": str(exception),
            "resolution": "Run with --login to create a new browser profile.",
        }

    elif isinstance(exception, AuthenticationError):
        logger.warning("Authentication failed in %s: %s", context, exception)
        return {
            "error": "authentication_failed",
            "message": str(exception),
            "resolution": "Run with --login to re-authenticate.",
        }

    elif isinstance(exception, RateLimitError):
        wait_time = getattr(exception, "suggested_wait_time", 300)
        logger.warning("Rate limit in %s: %s (wait=%ds)", context, exception, wait_time)
        return {
            "error": "rate_limit",
            "message": str(exception),
            "suggested_wait_seconds": wait_time,
            "resolution": f"LinkedIn rate limit detected. Wait {wait_time} seconds before trying again.",
        }

    elif isinstance(exception, QuotaExceededError):
        logger.warning("Quota exceeded in %s: %s", context, exception)
        return {
            "error": "quota_exceeded",
            "message": str(exception),
            "tool_name": exception.tool_name,
            "limit": exception.limit,
            "used": exception.used,
            "resolution": "Wait until quota resets or increase quota in ~/.linkedin-mcp/config.json.",
        }

    elif isinstance(exception, ConcurrencyError):
        logger.warning("Write concurrency issue in %s: %s", context, exception)
        return {
            "error": "concurrency_error",
            "message": str(exception),
            "resolution": "Another write operation is in progress. Retry shortly.",
        }

    elif isinstance(exception, ProfileNotFoundError):
        logger.warning("Profile not found in %s: %s", context, exception)
        return {
            "error": "profile_not_found",
            "message": str(exception),
            "resolution": "Check the profile URL is correct and the profile exists.",
        }

    elif isinstance(exception, ElementNotFoundError):
        logger.warning("Element not found in %s: %s", context, exception)
        return {
            "error": "element_not_found",
            "message": str(exception),
            "resolution": "LinkedIn page structure may have changed. Please report this issue.",
        }

    elif isinstance(exception, SelectorError):
        logger.warning("Selector resolution failed in %s: %s", context, exception)
        return {
            "error": "selector_error",
            "message": str(exception),
            "telemetry": exception.context,
            "resolution": "LinkedIn UI may have changed. Check selector telemetry and update locator chains.",
        }

    elif isinstance(exception, InteractionError):
        logger.warning("Interaction failed in %s: %s", context, exception)
        return {
            "error": "interaction_error",
            "message": str(exception),
            "action": exception.action,
            "context": exception.context,
            "resolution": "Retry with visible browser mode to inspect UI state.",
        }

    elif isinstance(exception, NetworkError):
        logger.warning("Network error in %s: %s", context, exception)
        return {
            "error": "network_error",
            "message": str(exception),
            "resolution": "Check your network connection and try again.",
        }

    elif isinstance(exception, ScrapingError):
        logger.warning("Scraping error in %s: %s", context, exception)
        return {
            "error": "scraping_error",
            "message": str(exception),
            "resolution": "Failed to extract data from LinkedIn. The page structure may have changed.",
        }

    elif isinstance(exception, LinkedInScraperException):
        logger.warning("Scraper error in %s: %s", context, exception)
        return {
            "error": "linkedin_scraper_error",
            "message": str(exception),
        }

    elif isinstance(exception, LinkedInMCPError):
        logger.warning("MCP error in %s: %s", context, exception)
        return {
            "error": "linkedin_mcp_error",
            "message": str(exception),
        }

    else:
        logger.error(
            "Unexpected error in %s: %s",
            context,
            exception,
            exc_info=True,
            extra={
                "context": context,
                "exception_type": type(exception).__name__,
                "exception_message": str(exception),
            },
        )
        return {
            "error": "unknown_error",
            "message": f"Failed to execute {context}: {str(exception)}",
        }
