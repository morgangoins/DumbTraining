import argparse
import asyncio
import contextlib
import re
import time
from typing import Optional

from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
    Browser,
    Page,
)

LOGIN_URL = "https://www.hrhotlink.com/home.asp?xu=WQL40829&xt=046F526A7A7772"
USERNAME = "mgoins"
PASSWORD = "4Marshall,"

TWO_FACTOR_INPUT_SELECTORS = [
    "#Code",
    "#TwoFactorCode",
    "input[name='TwoFactorCode']",
    "input[name='twoFactorCode']",
    "input[name='Code']",
    "input[name='VerificationCode']",
    "input[name='verificationCode']",
    "input[placeholder*='code' i]",
    "input[aria-label*='code' i]",
]

TWO_FACTOR_SUBMIT_SELECTORS = [
    "button:has-text('Continue')",
    "button:has-text('Verify')",
    "button:has-text('Submit')",
    "input[type='submit']",
]

TWO_FACTOR_ERROR_SELECTORS = [
    "text=/invalid\\s+(verification|two[- ]?factor)\\s+code/i",
    "text=/incorrect\\s+(verification|two[- ]?factor)\\s+code/i",
    "text=/verification\\s+code\\s+is\\s+required/i",
    "text=/two[- ]?factor\\s+(verification|authentication)\\s+failed/i",
]


def iter_page_frames(page: Page):
    seen = set()
    main = page.main_frame
    if main:
        seen.add(main)
        yield main
    for frame in page.frames:
        if frame in seen:
            continue
        yield frame


async def training_link_present(page: Page) -> bool:
    locators_factories = [
        lambda frame: frame.get_by_role("link", name="Training"),
        lambda frame: frame.locator("a:has-text('Training')"),
        lambda frame: frame.locator("text=Training"),
    ]

    for frame in iter_page_frames(page):
        for factory in locators_factories:
            locator = factory(frame)
            try:
                if await locator.count():
                    return True
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue

    return False


async def wait_for_training_link(page: Page, timeout_ms: int = 7000) -> bool:
    poll_interval = 0.4
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        if await training_link_present(page):
            print("Detected 'Training' link; assuming post-login state reached.")
            return True
        await asyncio.sleep(poll_interval)

    return False


async def wait_for_two_factor_transition(
    page: Page, input_selector: str, timeout_ms: int = 17000
) -> bool:
    poll_interval = 0.4
    deadline = time.monotonic() + timeout_ms / 1000
    initial_url = page.url
    navigation_logged = False

    while time.monotonic() < deadline:
        if await training_link_present(page):
            print("Detected 'Training' link after submitting two-factor code; verification succeeded.")
            return True

        current_url = page.url
        if current_url != initial_url and not navigation_logged:
            print(f"Navigation changed from '{initial_url}' to '{current_url}' after submitting 2FA code.")
            navigation_logged = True

        input_locator = page.locator(input_selector)
        try:
            input_count = await input_locator.count()
        except PlaywrightTimeoutError:
            input_count = 0
        except Exception:
            input_count = 0

        if input_count == 0:
            print("Two-factor input removed from DOM; assuming verification succeeded.")
            return True

        try:
            if not await input_locator.first.is_visible():
                print("Two-factor input hidden from view; assuming verification succeeded.")
                return True
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass

        for error_selector in TWO_FACTOR_ERROR_SELECTORS:
            error_locator = page.locator(error_selector)
            try:
                if await error_locator.count():
                    try:
                        if await error_locator.first.is_visible():
                            message = (await error_locator.first.inner_text()).strip()
                            if message:
                                print(f"Detected two-factor error message: '{message}'")
                            else:
                                print("Detected a visible two-factor error indicator on the page.")
                            return False
                    except PlaywrightTimeoutError:
                        continue
                    except Exception:
                        print("Detected a two-factor error indicator on the page.")
                        return False
            except PlaywrightTimeoutError:
                continue

        await asyncio.sleep(poll_interval)

    print("No post-login indicators detected after submitting two-factor code.")
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

            if await wait_for_two_factor_transition(page, selector, timeout_ms=20000):
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


async def click_first_available(page: Page, description: str, locator_factories, timeout_ms: int = 7000) -> None:
    poll_interval = 0.4
    deadline = time.monotonic() + timeout_ms / 1000

    def iter_frames():
        seen = set()
        main_frame = page.main_frame
        if main_frame:
            seen.add(main_frame)
            yield main_frame
        for frame in page.frames:
            if frame in seen:
                continue
            yield frame

    while time.monotonic() < deadline:
        for frame in iter_frames():
            for factory in locator_factories:
                locator = factory(frame)
                try:
                    await locator.first.click(timeout=500)
                    print(f"Clicked {description}.")
                    return
                except PlaywrightTimeoutError:
                    continue
                except Exception as exc:
                    print(f"Locator attempt for {description} failed: {exc}")
                    continue
        await asyncio.sleep(poll_interval)

    raise RuntimeError(f"Unable to locate {description} within {timeout_ms} ms.")


async def click_with_optional_popup(
    page: Page,
    description: str,
    locator_factories,
    click_timeout_ms: int = 7000,
    popup_timeout_ms: int = 8000,
) -> Page:
    popup_task: Optional[asyncio.Task[Page]] = asyncio.create_task(page.wait_for_event("popup"))

    try:
        await click_first_available(page, description, locator_factories, timeout_ms=click_timeout_ms)
    except Exception:
        if popup_task:
            popup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await popup_task
        raise

    if not popup_task:
        return page

    try:
        new_page = await asyncio.wait_for(popup_task, popup_timeout_ms / 1000)
    except asyncio.TimeoutError:
        popup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await popup_task
        return page
    except Exception as exc:
        if not isinstance(exc, PlaywrightTimeoutError):
            print(f"Waiting for popup after clicking {description} raised: {exc}")
        return page
    else:
        try:
            await new_page.wait_for_load_state("domcontentloaded", timeout=popup_timeout_ms)
        except PlaywrightTimeoutError:
            pass
        print(f"{description} opened a new page; switching context.")
        return new_page


async def navigate_to_anti_harassment_training(page: Page) -> None:
    print("Navigating to Training menu...")
    training_locators = [
        lambda frame: frame.get_by_role("link", name=re.compile("Training", re.IGNORECASE)),
        lambda frame: frame.locator("a:has-text('Training')"),
        lambda frame: frame.locator("text=Training"),
        lambda frame: frame.locator("text=/Training/i"),
    ]
    active_page = await click_with_optional_popup(
        page,
        "Training link",
        training_locators,
        click_timeout_ms=15000,
        popup_timeout_ms=12000,
    )

    try:
        await active_page.wait_for_load_state("networkidle", timeout=7000)
    except PlaywrightTimeoutError:
        pass
    await active_page.wait_for_timeout(1000)

    print("Opening Anti-Harassment Employee Training module...")
    anti_harassment_locators = [
        lambda frame: frame.get_by_role("link", name=re.compile("Anti-Harassment Employee Training", re.IGNORECASE)),
        lambda frame: frame.locator("a:has-text('Anti-Harassment Employee Training')"),
        lambda frame: frame.locator("text=Anti-Harassment Employee Training"),
        lambda frame: frame.locator("text=/Anti-Harassment\\s+Employee\\s+Training/i"),
        lambda frame: frame.locator("text=/Anti-Harassment/i"),
        lambda frame: frame.locator("button:has-text('Anti-Harassment Employee Training')"),
    ]
    active_page = await click_with_optional_popup(
        active_page,
        "'Anti-Harassment Employee Training' option",
        anti_harassment_locators,
        timeout_ms=15000,
        popup_timeout_ms=15000,
    )

    try:
        await active_page.wait_for_load_state("networkidle", timeout=7000)
    except PlaywrightTimeoutError:
        pass
    print("Anti-Harassment Employee Training should now be open.")
    return active_page


async def run(headless: bool, slow_mo: int) -> None:
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page: Page = await browser.new_page()

        print(f"Opening login page: {LOGIN_URL}")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        await submit_login(page)
        active_page = await navigate_to_anti_harassment_training(page)

        print("Automation steps completed. Leaving the browser open for review...")
        await active_page.wait_for_timeout(5000)
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
