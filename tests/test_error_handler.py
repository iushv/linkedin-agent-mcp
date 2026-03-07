from linkedin_mcp_server.core.exceptions import (
    AuthenticationError,
    ConcurrencyError,
    ElementNotFoundError,
    InteractionError,
    ProfileNotFoundError,
    RateLimitError,
    SelectorError,
)

from linkedin_mcp_server.error_handler import handle_tool_error
from linkedin_mcp_server.exceptions import (
    CredentialsNotFoundError,
    SessionExpiredError,
)


def test_handles_session_expired():
    result = handle_tool_error(SessionExpiredError(), "test_tool")
    assert result["error"] == "session_expired"
    assert "message" in result
    assert "resolution" in result


def test_handles_credentials_not_found():
    result = handle_tool_error(CredentialsNotFoundError("no creds"), "test_tool")
    assert result["error"] == "authentication_not_found"


def test_handles_generic_exception():
    result = handle_tool_error(ValueError("oops"), "test_tool")
    assert result["error"] == "unknown_error"
    assert "oops" in result["message"]


def test_handles_rate_limit_with_suggested_wait():
    """Test RateLimitError with custom suggested_wait_time attribute."""
    error = RateLimitError("Rate limited")
    error.suggested_wait_time = 600
    result = handle_tool_error(error, "test_tool")
    assert result["error"] == "rate_limit"
    assert result["suggested_wait_seconds"] == 600
    assert "600" in result["resolution"]


def test_handles_rate_limit_default_wait():
    """Test RateLimitError without suggested_wait_time uses default 300."""
    error = RateLimitError("Rate limited")
    result = handle_tool_error(error, "test_tool")
    assert result["error"] == "rate_limit"
    assert result["suggested_wait_seconds"] == 300
    assert "300" in result["resolution"]


# --- P7: Added exception types ---


def test_handles_authentication_error():
    result = handle_tool_error(AuthenticationError("auth failed"), "test_tool")
    assert result["error"] == "authentication_failed"


def test_handles_quota_exceeded_with_details():
    from linkedin_mcp_server.core.exceptions import QuotaExceededError

    error = QuotaExceededError("quota hit", tool_name="create_post", limit=10, used=10)
    result = handle_tool_error(error, "test_tool")
    assert result["error"] == "quota_exceeded"
    assert result["tool_name"] == "create_post"
    assert result["limit"] == 10
    assert result["used"] == 10


def test_handles_concurrency_error():
    result = handle_tool_error(ConcurrencyError("busy"), "test_tool")
    assert result["error"] == "concurrency_error"


def test_handles_profile_not_found():
    result = handle_tool_error(ProfileNotFoundError("no profile"), "test_tool")
    assert result["error"] == "profile_not_found"


def test_handles_element_not_found():
    result = handle_tool_error(ElementNotFoundError("no element"), "test_tool")
    assert result["error"] == "element_not_found"


def test_handles_selector_error():
    error = SelectorError(
        "selector fail",
        chain_name="test_chain",
        tried_strategies=["css:.a", "role:button"],
        url="https://linkedin.com/in/user/",
    )
    result = handle_tool_error(error, "test_tool")
    assert result["error"] == "selector_error"
    assert "telemetry" in result


def test_handles_interaction_error():
    error = InteractionError(
        "click failed", action="click_element", context={"selector": ".btn"}
    )
    result = handle_tool_error(error, "test_tool")
    assert result["error"] == "interaction_error"
    assert result["action"] == "click_element"
    assert result["context"] == {"selector": ".btn"}
