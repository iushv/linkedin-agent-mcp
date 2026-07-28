"""
LinkedIn Agent MCP - Main CLI application entry point.

Implements a simplified two-phase startup:
1. Authentication Check - Verify browser profile is available
2. Server Runtime - MCP server startup with transport selection
"""

import asyncio
import logging
import subprocess
import sys
from typing import Literal

import inquirer

from linkedin_mcp_server.core import AuthenticationError, RateLimitError, is_logged_in

from linkedin_mcp_server.authentication import (
    clear_profile,
    get_authentication_source,
)
from linkedin_mcp_server.config import get_config
from linkedin_mcp_server.drivers.browser import (
    close_browser,
    get_or_create_browser,
    get_profile_dir,
    profile_exists,
    profile_lock_holder,
    set_headless,
)
from linkedin_mcp_server.exceptions import CredentialsNotFoundError
from linkedin_mcp_server.logging_config import configure_logging
from linkedin_mcp_server.server import create_mcp_server
from linkedin_mcp_server.setup import run_interactive_setup, run_profile_creation

logger = logging.getLogger(__name__)


def choose_transport_interactive() -> Literal["stdio", "streamable-http"]:
    """Prompt user for transport mode using inquirer."""
    questions = [
        inquirer.List(
            "transport",
            message="Choose mcp transport mode",
            choices=[
                ("stdio (Default CLI mode)", "stdio"),
                ("streamable-http (HTTP server mode)", "streamable-http"),
            ],
            default="stdio",
        )
    ]
    answers = inquirer.prompt(questions)

    if not answers:
        raise KeyboardInterrupt("Transport selection cancelled by user")

    return answers["transport"]


def clear_profile_and_exit() -> None:
    """Clear LinkedIn browser profile and exit."""
    config = get_config()

    configure_logging(
        log_level=config.server.log_level,
        json_format=not config.is_interactive and config.server.log_level != "DEBUG",
    )

    version = get_version()
    logger.info(f"LinkedIn Agent MCP v{version} - Profile Clear mode")

    profile_dir = get_profile_dir()

    if not profile_exists(profile_dir):
        print("ℹ️  No browser profile found")
        print("Nothing to clear.")
        sys.exit(0)

    print(f"🔑 Clear LinkedIn browser profile from {profile_dir}?")

    try:
        confirmation = (
            input("Are you sure you want to clear the profile? (y/N): ").strip().lower()
        )
        if confirmation not in ("y", "yes"):
            print("❌ Operation cancelled")
            sys.exit(0)
    except KeyboardInterrupt:
        print("\n❌ Operation cancelled")
        sys.exit(0)

    if clear_profile(profile_dir):
        print("✅ LinkedIn browser profile cleared successfully!")
    else:
        print("❌ Failed to clear profile")
        sys.exit(1)

    sys.exit(0)


def get_profile_and_exit() -> None:
    """Create profile interactively and exit."""
    config = get_config()

    configure_logging(
        log_level=config.server.log_level,
        json_format=not config.is_interactive and config.server.log_level != "DEBUG",
    )

    version = get_version()
    logger.info(f"LinkedIn Agent MCP v{version} - Session Creation mode")

    user_data_dir = config.browser.user_data_dir
    success = run_profile_creation(user_data_dir)

    sys.exit(0 if success else 1)


def profile_info_and_exit() -> None:
    """Check profile validity and display info, then exit."""
    config = get_config()

    configure_logging(
        log_level=config.server.log_level,
        json_format=not config.is_interactive and config.server.log_level != "DEBUG",
    )

    version = get_version()
    logger.info(f"LinkedIn Agent MCP v{version} - Session Info mode")

    # Check if profile directory exists first
    profile_dir = get_profile_dir()
    if not profile_exists(profile_dir):
        print(f"❌ No browser profile found at {profile_dir}")
        print("   Run with --login to create a profile")
        sys.exit(1)

    # Check if session is valid by testing login status
    async def check_session() -> bool:
        try:
            set_headless(True)  # Always check headless
            browser = await get_or_create_browser()
            valid = await is_logged_in(browser.page)
            return valid
        except AuthenticationError:
            return False
        except Exception as e:
            logger.exception(f"Unexpected error checking session: {e}")
            raise
        finally:
            await close_browser()

    try:
        valid = asyncio.run(check_session())
    except Exception as e:
        print(f"❌ Could not validate session: {e}")
        print("   Check logs and browser configuration.")
        sys.exit(1)

    if valid:
        print(f"✅ Session is valid (profile: {profile_dir})")
        sys.exit(0)
    else:
        print(f"❌ Session expired or invalid (profile: {profile_dir})")
        print("   Run with --login to re-authenticate")
        sys.exit(1)


def _diagnose_browser_binaries(headless: bool) -> tuple[bool, list[str]]:
    """Report expected vs on-disk Patchright browser binaries."""
    from linkedin_mcp_server.drivers.browser import (
        _parse_patchright_install_locations,
        _required_browsers,
    )

    lines: list[str] = []
    try:
        dry_run = subprocess.run(
            [sys.executable, "-m", "patchright", "install", "--dry-run", "chromium"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return False, [f"  ❌ could not run patchright dry-run: {exc}"]

    locations = _parse_patchright_install_locations(
        f"{dry_run.stdout or ''}\n{dry_run.stderr or ''}"
    )
    required = _required_browsers(headless)
    healthy = True

    for name in required:
        path = locations.get(name)
        if path is None:
            healthy = False
            lines.append(f"  ❌ {name}: expected path unknown (could not parse)")
        elif path.exists():
            lines.append(f"  ✅ {name}: {path}")
        else:
            healthy = False
            lines.append(f"  ❌ {name}: MISSING at {path}")

    if not healthy:
        lines.append(
            "     → the browser cache was deleted or is out of sync. Fix with:\n"
            f"       {sys.executable} -m patchright install chromium"
        )
    return healthy, lines


def doctor_and_exit() -> None:
    """Print environment diagnostics and exit non-zero if anything is wrong."""
    config = get_config()

    configure_logging(log_level="ERROR", json_format=False)

    version = get_version()
    profile_dir = get_profile_dir()
    healthy = True

    print(f"🩺 LinkedIn Agent MCP v{version} — diagnostics")
    print("=" * 48)

    print("\nVersions")
    print(f"  package:    {version}")
    try:
        from importlib.metadata import version as _pkg_version

        print(f"  patchright: {_pkg_version('patchright')}")
    except Exception:
        healthy = False
        print("  patchright: ❌ not installed")
    print(f"  python:     {sys.version.split()[0]}")
    print(f"  executable: {sys.executable}")

    print("\nBrowser binaries")
    if config.browser.chrome_path:
        print(f"  using custom chrome_path: {config.browser.chrome_path}")
    else:
        binaries_ok, lines = _diagnose_browser_binaries(config.browser.headless)
        healthy = healthy and binaries_ok
        for line in lines:
            print(line)

    print("\nProfile")
    print(f"  path: {profile_dir}")
    if not profile_exists(profile_dir):
        healthy = False
        print("  ❌ no profile found — run with --login")
    else:
        print("  ✅ profile present")
        holder = profile_lock_holder(profile_dir)
        if holder is None:
            print("  ✅ no competing process holds the profile")
        else:
            healthy = False
            print(
                f"  ❌ profile is locked by PID {holder} — another LinkedIn MCP "
                "instance is running.\n"
                "     → close it, or run this instance with --user-data-dir <copy>"
            )

    print("\nSession")
    if not profile_exists(profile_dir):
        print("  ⏭  skipped (no profile)")
    else:

        async def _check() -> bool:
            try:
                set_headless(True)
                browser = await get_or_create_browser()
                return await is_logged_in(browser.page)
            finally:
                await close_browser()

        try:
            if asyncio.run(_check()):
                print("  ✅ session is valid")
            else:
                healthy = False
                print("  ❌ session expired — run with --login")
        except Exception as exc:
            healthy = False
            print(f"  ❌ could not validate session: {exc}")

    print("\n" + "=" * 48)
    print("✅ All checks passed" if healthy else "❌ Problems found (see above)")
    sys.exit(0 if healthy else 1)


def ensure_authentication_ready() -> None:
    """
    Phase 1: Ensure authentication is ready.

    Checks for existing browser profile.
    If not found, runs interactive setup in interactive mode.

    Raises:
        CredentialsNotFoundError: If authentication setup fails
    """
    config = get_config()

    # Check for existing profile
    try:
        get_authentication_source()
        return

    except CredentialsNotFoundError:
        pass

    # No authentication found - try interactive setup if possible
    if not config.is_interactive:
        raise CredentialsNotFoundError(
            "No LinkedIn profile found.\n"
            "Options:\n"
            "  1. Run with --login to create a profile\n"
            "  2. Run with --no-headless to login interactively"
        )

    # Run interactive setup
    logger.info("No authentication found, starting interactive setup...")
    success = run_interactive_setup()

    if not success:
        raise CredentialsNotFoundError("Interactive setup was cancelled or failed")


def get_version() -> str:
    """Get version from installed metadata with a source fallback."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        for package_name in ("linkedin-scraper-mcp", "linkedin-mcp-server"):
            try:
                return version(package_name)
            except PackageNotFoundError:
                continue
    except Exception:
        pass

    try:
        import os
        import tomllib

        pyproject_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "pyproject.toml"
        )
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
            return data["project"]["version"]
    except Exception:
        return "unknown"


def main() -> None:
    """Main application entry point."""
    config = get_config()

    # Configure logging
    configure_logging(
        log_level=config.server.log_level,
        json_format=not config.is_interactive and config.server.log_level != "DEBUG",
    )

    version = get_version()

    # Print banner in interactive mode
    if config.is_interactive:
        print(f"🔗 LinkedIn Agent MCP v{version} 🔗")
        print("=" * 40)

    logger.info(f"LinkedIn Agent MCP v{version}")

    # Set headless mode from config
    set_headless(config.browser.headless)

    # Handle --logout flag
    if config.server.logout:
        clear_profile_and_exit()

    # Handle --login flag
    if config.server.login:
        get_profile_and_exit()

    # Handle --doctor flag
    if config.server.doctor:
        doctor_and_exit()

    # Handle --status flag
    if config.server.status:
        profile_info_and_exit()

    logger.debug(f"Server configuration: {config}")

    # Phase 1: Ensure Authentication is Ready
    try:
        ensure_authentication_ready()
        if config.is_interactive:
            print("✅ Authentication ready")
        logger.info("Authentication ready")

    except CredentialsNotFoundError as e:
        logger.error(f"Authentication setup failed: {e}")
        if config.is_interactive:
            print("\n❌ Authentication required")
            print(str(e))
        sys.exit(1)

    except KeyboardInterrupt:
        if config.is_interactive:
            print("\n\n👋 Setup cancelled by user")
        sys.exit(0)

    except (AuthenticationError, RateLimitError) as e:
        logger.error(f"LinkedIn error during setup: {e}")
        if config.is_interactive:
            print(f"\n❌ {str(e)}")
        sys.exit(1)

    except Exception as e:
        logger.exception(f"Unexpected error during authentication setup: {e}")
        if config.is_interactive:
            print(f"\n❌ Setup failed: {e}")
        sys.exit(1)

    # Phase 2: Server Runtime
    try:
        transport = config.server.transport

        # Prompt for transport in interactive mode if not explicitly set
        if config.is_interactive and not config.server.transport_explicitly_set:
            print("\n🚀 Server ready! Choose transport mode:")
            transport = choose_transport_interactive()

        # Create and run the MCP server
        mcp = create_mcp_server()

        if transport == "streamable-http":
            mcp.run(
                transport=transport,
                host=config.server.host,
                port=config.server.port,
                path=config.server.path,
            )
        else:
            mcp.run(transport=transport)

    except KeyboardInterrupt:
        exit_gracefully(0)

    except Exception as e:
        logger.exception(f"Server runtime error: {e}")
        if config.is_interactive:
            print(f"\n❌ Server error: {e}")
        exit_gracefully(1)


def exit_gracefully(exit_code: int = 0) -> None:
    """Exit the application gracefully with browser cleanup."""
    try:
        asyncio.run(close_browser())
    except Exception:
        pass  # Best effort cleanup
    sys.exit(exit_code)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        exit_gracefully(0)
    except Exception as e:
        logger.exception(
            f"Error running MCP server: {e}",
            extra={"exception_type": type(e).__name__, "exception_message": str(e)},
        )
        exit_gracefully(1)
