"""
Patchright browser management for LinkedIn scraping.

Provides async browser lifecycle management using BrowserManager with persistent
context. Implements a singleton pattern for browser reuse across tool calls with
automatic profile persistence.
"""

import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from patchright.async_api import TimeoutError as PlaywrightTimeoutError

from linkedin_mcp_server.core import (
    AuthenticationError,
    BrowserManager,
    detect_rate_limit,
    is_logged_in,
)
from linkedin_mcp_server.core.utils import backoff_with_jitter

from linkedin_mcp_server.config import get_config

logger = logging.getLogger(__name__)


# Default persistent profile directory
DEFAULT_PROFILE_DIR = Path.home() / ".linkedin-mcp" / "profile"

# Global browser instance (singleton)
_browser: BrowserManager | None = None
_headless: bool = True
_browser_binary_verified_mode: bool | None = None

_DRY_RUN_TIMEOUT_SECONDS = 15
_INSTALL_TIMEOUT_SECONDS = 120
_FEED_AUTH_TIMEOUT_FLOOR_MS = 45_000
_PLAYWRIGHT_SECTION_RE = re.compile(r"\(playwright\s+([^\s)]+)")
_INSTALL_LOCATION_RE = re.compile(r"^\s*Install location:\s*(.+?)\s*$")


def _parse_patchright_install_locations(output: str) -> dict[str, Path]:
    """Parse browser install locations from `patchright install --dry-run` output."""
    locations: dict[str, Path] = {}
    current_browser: str | None = None

    for raw_line in output.splitlines():
        section_match = _PLAYWRIGHT_SECTION_RE.search(raw_line)
        if section_match:
            current_browser = section_match.group(1).strip()
            continue

        location_match = _INSTALL_LOCATION_RE.match(raw_line)
        if location_match and current_browser:
            locations[current_browser] = Path(location_match.group(1)).expanduser()

    return locations


def _required_browsers(headless: bool) -> tuple[str, ...]:
    if headless:
        return ("chromium", "chromium-headless-shell")
    return ("chromium",)


def _format_expected_paths(
    expected_paths: dict[str, Path],
    required: tuple[str, ...],
) -> str:
    return "\n".join(
        f"- {name}: {expected_paths.get(name, Path('<unknown>'))}" for name in required
    )


def ensure_browser_binary(*, headless: bool) -> None:
    """Verify Patchright browser binaries exist and install them when missing.

    Uses `sys.executable -m patchright` commands only (no private Patchright APIs).
    """
    dry_run_cmd = [
        sys.executable,
        "-m",
        "patchright",
        "install",
        "--dry-run",
        "chromium",
    ]
    install_cmd = [sys.executable, "-m", "patchright", "install", "chromium"]

    try:
        dry_run = subprocess.run(
            dry_run_cmd,
            capture_output=True,
            text=True,
            timeout=_DRY_RUN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Patchright browser dry-run timed out. Run: "
            f"{sys.executable} -m patchright install chromium"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            "Could not verify Patchright browser binaries. Run: "
            f"{sys.executable} -m patchright install chromium"
        ) from exc

    dry_run_output = f"{dry_run.stdout or ''}\n{dry_run.stderr or ''}"
    expected_paths = _parse_patchright_install_locations(dry_run_output)
    required = _required_browsers(headless)
    missing_required = [name for name in required if name not in expected_paths]
    if missing_required:
        raise RuntimeError(
            "Could not parse expected Patchright browser install locations "
            f"for {', '.join(missing_required)}.\n"
            "Run manually: "
            f"{sys.executable} -m patchright install chromium"
        )

    missing_paths = {
        name: expected_paths[name]
        for name in required
        if not expected_paths[name].exists()
    }
    if not missing_paths:
        logger.debug(
            "Patchright browser binaries verified:\n%s",
            _format_expected_paths(expected_paths, required),
        )
        return

    logger.info(
        "Missing Patchright browser binaries detected (%s). Attempting auto-install.",
        ", ".join(missing_paths),
    )

    try:
        install_result = subprocess.run(
            install_cmd,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Patchright browser auto-install timed out.\n"
            f"Expected path(s):\n{_format_expected_paths(expected_paths, required)}\n\n"
            "Fix manually:\n"
            f"  {sys.executable} -m patchright install chromium\n"
            "Or if using uv:\n"
            "  uv run patchright install chromium"
        ) from exc

    if install_result.returncode != 0:
        stderr_preview = (install_result.stderr or "").strip()[:500]
        raise RuntimeError(
            "Patchright browser binary not found and auto-install failed.\n"
            f"Expected path(s):\n{_format_expected_paths(expected_paths, required)}\n\n"
            "Fix manually:\n"
            f"  {sys.executable} -m patchright install chromium\n"
            "Or if using uv:\n"
            "  uv run patchright install chromium\n\n"
            f"Auto-install stderr: {stderr_preview}"
        )

    still_missing = {
        name: expected_paths[name]
        for name in required
        if not expected_paths[name].exists()
    }
    if still_missing:
        raise RuntimeError(
            "Patchright auto-install completed but required browser binaries are still missing.\n"
            f"Expected path(s):\n{_format_expected_paths(expected_paths, required)}\n\n"
            "Fix manually:\n"
            f"  {sys.executable} -m patchright install chromium\n"
            "Or if using uv:\n"
            "  uv run patchright install chromium"
        )

    logger.info(
        "Patchright browser binaries installed successfully:\n%s",
        _format_expected_paths(expected_paths, required),
    )


def _apply_browser_settings(browser: BrowserManager) -> None:
    """Apply configuration settings to browser instance."""
    config = get_config()
    browser.page.set_default_timeout(config.browser.default_timeout)


async def _goto_feed_with_retry(page: Any) -> None:
    """Navigate to /feed/ for the auth check with bounded retries."""
    timeout_ms = max(get_config().browser.default_timeout, _FEED_AUTH_TIMEOUT_FLOOR_MS)
    attempts = 3
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            await page.goto(
                "https://www.linkedin.com/feed/",
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            return
        except (PlaywrightTimeoutError, TimeoutError) as exc:
            last_error = exc
            if attempt >= attempts - 1:
                raise
            delay = await backoff_with_jitter(
                attempt,
                base_seconds=3,
                max_seconds=20,
            )
            logger.warning(
                "Retrying /feed/ auth check after %s (attempt %d/%d, slept %.2fs)",
                type(exc).__name__,
                attempt + 1,
                attempts,
                delay,
            )

    if last_error is not None:
        raise last_error


async def get_or_create_browser(
    headless: bool | None = None,
) -> BrowserManager:
    """
    Get existing browser or create and initialize a new one.

    Uses a singleton pattern to reuse the browser across tool calls.
    Uses persistent context for automatic profile persistence.

    Args:
        headless: Run browser in headless mode. Defaults to config value.

    Returns:
        Initialized BrowserManager instance

    Raises:
        AuthenticationError: If no valid authentication found
    """
    global _browser, _headless, _browser_binary_verified_mode

    if headless is not None:
        _headless = headless

    if _browser is not None:
        return _browser

    config = get_config()
    if not config.browser.chrome_path:
        requires_check = _browser_binary_verified_mode is None or (
            _headless and _browser_binary_verified_mode is False
        )
        if requires_check:
            ensure_browser_binary(headless=_headless)
            _browser_binary_verified_mode = (
                True if _headless else (_browser_binary_verified_mode or False)
            )

    user_data_dir = Path(config.browser.user_data_dir).expanduser()

    # Randomize viewport per session unless user explicitly set it
    if config.browser.randomize_viewport and not config.browser.viewport_explicitly_set:
        from linkedin_mcp_server.core.timing import viewport_dimensions

        w, h = viewport_dimensions()
        viewport = {"width": w, "height": h}
    else:
        viewport = {
            "width": config.browser.viewport_width,
            "height": config.browser.viewport_height,
        }

    # Build launch options for custom browser path
    launch_options: dict[str, str] = {}
    if config.browser.chrome_path:
        launch_options["executable_path"] = config.browser.chrome_path
        logger.info("Using custom Chrome path: %s", config.browser.chrome_path)

    # Build proxy config if set
    if config.browser.proxy_server:
        proxy_config: dict[str, str] = {"server": config.browser.proxy_server}
        if config.browser.proxy_username:
            proxy_config["username"] = config.browser.proxy_username
        if config.browser.proxy_password:
            proxy_config["password"] = config.browser.proxy_password
        launch_options["proxy"] = proxy_config  # type: ignore[assignment]
        logger.info("Using proxy: %s", config.browser.proxy_server)

    logger.info(
        "Creating new browser (headless=%s, slow_mo=%sms, viewport=%sx%s, profile=%s)",
        _headless,
        config.browser.slow_mo,
        viewport["width"],
        viewport["height"],
        user_data_dir,
    )
    browser = BrowserManager(
        user_data_dir=user_data_dir,
        headless=_headless,
        slow_mo=config.browser.slow_mo,
        user_agent=config.browser.user_agent,
        viewport=viewport,
        **launch_options,
    )
    await browser.start()

    # Navigate to LinkedIn to check authentication
    await _goto_feed_with_retry(browser.page)
    if await is_logged_in(browser.page):
        _apply_browser_settings(browser)
        _browser = browser  # Assign only after auth succeeds
        return _browser

    # Native auth failed — try the cross-platform cookie bridge.
    # On macOS→Linux, Chromium can't decrypt macOS-encrypted cookies in the
    # persistent profile. We copy the profile to a temp dir (so the original
    # isn't corrupted by Linux Chromium writing back), remove the undecryptable
    # Cookies DB, and inject auth cookies from the portable JSON file.
    cookie_path = user_data_dir.parent / "cookies.json"
    if cookie_path.exists():
        logger.info("Native auth failed, attempting cross-platform cookie bridge...")
        await browser.close()

        # Copy profile to temp dir — protects the macOS original
        temp_dir = Path(tempfile.mkdtemp(prefix="linkedin-mcp-"))
        temp_profile = temp_dir / "profile"
        shutil.copytree(user_data_dir, temp_profile)

        # Remove encrypted Cookies DB (can't be decrypted cross-platform)
        (temp_profile / "Default" / "Cookies").unlink(missing_ok=True)
        (temp_profile / "Default" / "Cookies-journal").unlink(missing_ok=True)

        browser = BrowserManager(
            user_data_dir=temp_profile,
            headless=_headless,
            slow_mo=config.browser.slow_mo,
            user_agent=config.browser.user_agent,
            viewport=viewport,
            **launch_options,
        )
        await browser.start()

        # First nav establishes session cookies (bcookie, JSESSIONID, etc.)
        await _goto_feed_with_retry(browser.page)
        # Import auth cookies (li_at, li_rm) from the portable file
        if await browser.import_cookies(cookie_path):
            await _goto_feed_with_retry(browser.page)
            if await is_logged_in(browser.page):
                logger.info("Authentication recovered via portable cookies")
                _apply_browser_settings(browser)
                _browser = browser
                return _browser

    # Auth failed — clean up and fail fast
    await browser.close()
    raise AuthenticationError(
        "No authentication found. Run with --login to create a profile."
    )


async def close_browser() -> None:
    """Close the browser and cleanup resources."""
    global _browser

    if _browser is not None:
        logger.info("Closing browser...")
        # Export cookies before closing to keep portable file fresh
        try:
            await _browser.export_cookies()
        except Exception:
            logger.debug("Cookie export on close skipped", exc_info=True)
        await _browser.close()
        _browser = None
        logger.info("Browser closed")


def get_profile_dir() -> Path:
    """Get the resolved profile directory from config."""
    config = get_config()
    return Path(config.browser.user_data_dir).expanduser()


def profile_exists(profile_dir: Path | None = None) -> bool:
    """Check if a persistent browser profile exists and is non-empty."""
    if profile_dir is None:
        profile_dir = get_profile_dir()
    return profile_dir.is_dir() and any(profile_dir.iterdir())


def set_headless(headless: bool) -> None:
    """Set headless mode for future browser creation."""
    global _headless
    _headless = headless


async def validate_session() -> bool:
    """
    Check if the current session is still valid (logged in).

    Returns:
        True if session is valid and user is logged in
    """
    browser = await get_or_create_browser()
    if await is_logged_in(browser.page):
        return True

    # Recover from stale/interstitial page state by reloading a stable feed URL.
    try:
        await browser.page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
        )
    except Exception:
        logger.debug("Session recovery navigation failed", exc_info=True)
        return False

    return await is_logged_in(browser.page)


async def ensure_authenticated() -> None:
    """
    Validate session and raise if expired.

    Raises:
        AuthenticationError: If session is expired or invalid
    """
    if not await validate_session():
        raise AuthenticationError("Session expired or invalid.")


async def check_rate_limit() -> None:
    """
    Proactively check for rate limiting.

    Should be called after navigation to detect if LinkedIn is blocking requests.

    Raises:
        RateLimitError: If rate limiting is detected
    """
    browser = await get_or_create_browser()
    await detect_rate_limit(browser.page)


def reset_browser_for_testing() -> None:
    """Reset global browser state for test isolation."""
    global _browser, _headless, _browser_binary_verified_mode
    _browser = None
    _headless = True
    _browser_binary_verified_mode = None


def reset_browser_binary_check() -> None:
    """Reset browser binary verification state for tests."""
    global _browser_binary_verified_mode
    _browser_binary_verified_mode = None
