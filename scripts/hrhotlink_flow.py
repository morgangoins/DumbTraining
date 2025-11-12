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

TWO_FACTOR_INPUT_SELECTORS = [
    "#TwoFactorCode",
    "input[name='TwoFactorCode']",
    "input[name='twoFactorCode']",
    "input[name='VerificationCode']",
    "input[name='verificationCode']",
    "input[placeholder*='code' i]",
    "input[aria-label*='code' i]",
]

TWO_FACTOR_SUBMIT_SELECTORS = [
    "button:has-text('Verify')",
    "button:has-text('Submit')",
    "button:has-text('Continue')",
    "input[type='submit']",
]


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


async def prompt_for_two_factor_code(login_attempt: int, code_attempt: int) -> str:
    loop = asyncio.get_running_loop()
    prompt = f"Enter 2FA code (login attempt {login_attempt}, code try {code_attempt}): "
    return (await loop.run_in_executor(None, lambda: input(prompt))).strip()


async def maybe_handle_two_factor(page: Page, login_attempt: int) -> bool:
    for selector in TWO_FACTOR_INPUT_SELECTORS:
        field = page.locator(selector)
        try:
            await field.first.wait_for(timeout=2500)
        except PlaywrightTimeoutError:
            continue

        print(f"Two-factor challenge detected using selector '{selector}'.")

        for code_attempt in range(1, 4):
            code = await prompt_for_two_factor_code(login_attempt, code_attempt)
            if not code:
                print("No code entered; aborting this attempt.")
                return False

            await field.first.fill(code)

            submitted = False
            for submit_selector in TWO_FACTOR_SUBMIT_SELECTORS:
                submit_button = page.locator(submit_selector)
                if await submit_button.count():
                    await submit_button.first.click()
                    submitted = True
                    break

            if not submitted:
                print("No obvious submit button found for 2FA; waiting for page to react.")

            if await wait_for_training_link(page, timeout_ms=12000):
                print("Two-factor verification succeeded.")
                return True

            print("Two-factor verification did not succeed; retrying code entry...")

        print("Maximum 2FA attempts reached for this login try.")
        return False

    return False


async def submit_login(page: Page, max_attempts: int = 3) -> None:
    last_error: Optional[str] = None

    for attempt in range(1, max_attempts + 1):
        print(f"Attempting login (attempt {attempt}/{max_attempts})...")

        await page.fill("#login_username", USERNAME)
        await page.fill("#login_password", PASSWORD)

        await page.get_by_role("button", name="Log In").click()

        if await maybe_handle_two_factor(page, attempt):
            print("Login successful.")
            return

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
