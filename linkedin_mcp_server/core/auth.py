"""Authentication functions for LinkedIn."""

import asyncio
import logging
import random

from patchright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .exceptions import AuthenticationError
from .timing import navigation_delay

logger = logging.getLogger(__name__)

# Pool of benign sites for warm-up — a random subset is visited each session
_WARMUP_SITES = [
    "https://www.google.com",
    "https://www.wikipedia.org",
    "https://www.github.com",
    "https://news.ycombinator.com",
    "https://www.reddit.com",
]

# Visit between 2 and 4 sites per warm-up
_WARMUP_MIN = 2
_WARMUP_MAX = 4


async def warm_up_browser(page: Page) -> None:
    """Visit a random subset of benign sites with human-like timing."""
    count = random.randint(_WARMUP_MIN, min(_WARMUP_MAX, len(_WARMUP_SITES)))
    sites = random.sample(_WARMUP_SITES, count)

    logger.info("Warming up browser by visiting %d sites...", len(sites))

    failures = 0
    for site in sites:
        try:
            await page.goto(site, wait_until="domcontentloaded", timeout=10000)
            await asyncio.sleep(navigation_delay())
            logger.debug("Visited %s", site)
        except Exception as e:
            failures += 1
            logger.debug("Could not visit %s: %s", site, e)
            continue

    if failures == len(sites):
        logger.warning("Browser warm-up failed: none of %d sites reachable", len(sites))
    else:
        logger.info("Browser warm-up complete")


async def is_logged_in(page: Page) -> bool:
    """Check if currently logged in to LinkedIn.

    Uses a three-tier strategy:
    1. Fail-fast on auth blocker URLs
    2. Check for navigation elements (primary)
    3. URL-based fallback for authenticated-only pages
    """
    try:
        current_url = page.url

        # Step 1: Fail-fast on auth blockers
        auth_blockers = [
            "/login",
            "/authwall",
            "/checkpoint",
            "/challenge",
            "/uas/login",
            "/uas/consumer-email-challenge",
        ]
        if any(pattern in current_url for pattern in auth_blockers):
            return False

        # Step 2: Selector check (PRIMARY)
        old_selectors = '.global-nav__primary-link, [data-control-name="nav.settings"]'
        old_count = await page.locator(old_selectors).count()

        new_selectors = 'nav a[href*="/feed"], nav button:has-text("Home"), nav a[href*="/mynetwork"]'
        new_count = await page.locator(new_selectors).count()

        has_nav_elements = old_count > 0 or new_count > 0

        # Step 3: URL fallback
        authenticated_only_pages = [
            "/feed",
            "/mynetwork",
            "/messaging",
            "/notifications",
        ]
        is_authenticated_page = any(
            pattern in current_url for pattern in authenticated_only_pages
        )

        return has_nav_elements or is_authenticated_page
    except PlaywrightTimeoutError:
        logger.warning(
            "Timeout checking login status on %s — treating as not logged in",
            page.url,
        )
        return False
    except Exception:
        logger.error("Unexpected error checking login status", exc_info=True)
        raise


async def wait_for_manual_login(page: Page, timeout: int = 300000) -> None:
    """Wait for user to manually complete login.

    Args:
        page: Patchright page object
        timeout: Timeout in milliseconds (default: 5 minutes)

    Raises:
        AuthenticationError: If timeout or login not completed
    """
    logger.info(
        "Please complete the login process manually in the browser. "
        "Waiting up to 5 minutes..."
    )

    start_time = asyncio.get_event_loop().time()

    while True:
        if await is_logged_in(page):
            logger.info("Manual login completed successfully")
            return

        elapsed = (asyncio.get_event_loop().time() - start_time) * 1000
        if elapsed > timeout:
            raise AuthenticationError(
                "Manual login timeout. Please try again and complete login faster."
            )

        await asyncio.sleep(1)
