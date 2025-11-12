import argparse
import asyncio
from typing import Optional

from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
    Browser,
    Page,
)

LOGIN_URL = "https://www.hrhotlink.com/home.asp?xu=WQL40829&xt=046F526A7A7772"
USERNAME = "mgoins"
PASSWORD = "4Marshall"


async def wait_for_training_link(page: Page, timeout_ms: int = 7000) -> bool:
    locators = [
        page.get_by_role("link", name="Training"),
        page.locator("a:has-text('Training')"),
        page.locator("text=Training"),
    ]

    for locator in locators:
        try:
            await locator.first.wait_for(timeout=timeout_ms)
            return True
        except PlaywrightTimeoutError:
            continue

    return False


async def submit_login(page: Page, max_attempts: int = 3) -> None:
    last_error: Optional[str] = None

    for attempt in range(1, max_attempts + 1):
        print(f"Attempting login (attempt {attempt}/{max_attempts})...")

        await page.fill("#login_username", USERNAME)
        await page.fill("#login_password", PASSWORD)

        await page.get_by_role("button", name="Log In").click()

        logged_in = await wait_for_training_link(page)
        if logged_in:
            print("Login successful.")
            return

        last_error = f"Training link not visible after attempt {attempt}"
        print(f"Login attempt {attempt} did not succeed; retrying...")
        await page.wait_for_timeout(1500)

    raise RuntimeError(last_error or "Unable to log in after multiple attempts.")


async def navigate_to_required_training(page: Page) -> None:
    print("Navigating to Training...")
    try:
        await page.get_by_role("link", name="Training").first.click()
    except PlaywrightTimeoutError:
        await page.locator("a:has-text('Training')").first.click()

    await page.wait_for_load_state("networkidle")

    print("Navigating to Required Training...")
    # Try the most accessible locator first (link role)
    try:
        await page.get_by_role("link", name="Required Training").first.click()
    except PlaywrightTimeoutError:
        # Fallback to text locator if ARIA role lookup fails
        await page.locator("text=Required Training").first.click()

    await page.wait_for_load_state("networkidle")
    print("Arrived on Required Training page (or navigation triggered).")


async def run(headless: bool, slow_mo: int) -> None:
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page: Page = await browser.new_page()

        print(f"Opening login page: {LOGIN_URL}")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        await submit_login(page)
        await navigate_to_required_training(page)

        print("Automation steps completed. Leaving the browser open for review...")
        await page.wait_for_timeout(5000)
        await browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automate HR Hotlink login and navigation flow.")
    parser.add_argument("--headless", action="store_true", help="Run the browser in headless mode.")
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        metavar="MS",
        help="Slow down Playwright actions by the given milliseconds.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(headless=args.headless, slow_mo=args.slow_mo))
