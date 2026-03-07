"""Custom exceptions for LinkedIn scraping operations."""


class LinkedInScraperException(Exception):
    """Base exception for LinkedIn scraper."""

    pass


class AuthenticationError(LinkedInScraperException):
    """Raised when authentication fails."""

    pass


class RateLimitError(LinkedInScraperException):
    """Raised when rate limiting is detected."""

    def __init__(self, message: str, suggested_wait_time: int = 300):
        super().__init__(message)
        self.suggested_wait_time = suggested_wait_time


class ElementNotFoundError(LinkedInScraperException):
    """Raised when an expected element is not found."""

    pass


class ProfileNotFoundError(LinkedInScraperException):
    """Raised when a profile/page returns 404."""

    pass


class NetworkError(LinkedInScraperException):
    """Raised when network-related issues occur."""

    pass


class ScrapingError(LinkedInScraperException):
    """Raised when scraping fails for various reasons."""

    pass


class InteractionError(LinkedInScraperException):
    """Raised when browser interactions fail."""

    def __init__(
        self,
        message: str,
        action: str | None = None,
        context: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.action = action
        self.context = context or {}


class SelectorError(InteractionError):
    """Raised when no selector strategy can locate an element."""

    def __init__(
        self,
        message: str,
        chain_name: str,
        tried_strategies: list[str],
        url: str | None = None,
        context: dict[str, object] | None = None,
    ):
        merged_context = {
            "chain_name": chain_name,
            "tried_strategies": tried_strategies,
            "url": url,
        }
        if context:
            merged_context.update(context)
        super().__init__(message, action="resolve_selector", context=merged_context)
        self.chain_name = chain_name
        self.tried_strategies = tried_strategies
        self.url = url


class ConcurrencyError(LinkedInScraperException):
    """Raised when a write action cannot acquire exclusive access."""

    pass


class QuotaExceededError(LinkedInScraperException):
    """Raised when a tool exceeds configured quota limits."""

    def __init__(self, message: str, tool_name: str, limit: int, used: int):
        super().__init__(message)
        self.tool_name = tool_name
        self.limit = limit
        self.used = used
