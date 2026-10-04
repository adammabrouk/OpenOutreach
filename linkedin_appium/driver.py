# linkedin_appium/driver.py
"""Appium session + low-level element helpers for the LinkedIn Android app.

This is the Python port of the proven Node/WebdriverIO hello-world
(../appium-pipeline/linkedin.test.js). It talks to the same language-agnostic
Appium server on :4723, so the daemon (Python) can drive the phone in-process
instead of shelling out to Node.

Everything above this module (search, connect, message, thread) is built on the
two primitives here: ``new_driver()`` and ``find_first()``.
"""
from __future__ import annotations

import logging
import random
import time

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException

logger = logging.getLogger(__name__)

APP_PACKAGE = "com.linkedin.android"
APPIUM_URL = "http://127.0.0.1:4723"

# Human-typing rhythm (seconds). Ports the web adapter's HUMAN_TYPE_* pacing:
# most keystrokes land in a tight band, with the occasional longer "thinking"
# pause, and a beat before the whole burst starts — so typed text doesn't appear
# instantaneously the way setValue/send_keys does (a bot tell).
HUMAN_TYPE_MIN = 0.04
HUMAN_TYPE_MAX = 0.16
HUMAN_TYPE_PAUSE_CHANCE = 0.06   # chance of a longer pause after a character
HUMAN_TYPE_PAUSE_MIN = 0.3
HUMAN_TYPE_PAUSE_MAX = 0.9


def new_driver(appium_url: str = APPIUM_URL):
    """Connect to Appium and bring LinkedIn to the foreground.

    Mirrors the Node capabilities exactly: noReset keeps you logged in between
    runs, appWaitActivity '*' accepts whatever screen opens first, and the
    MIUI/Xiaomi hidden-API tolerance avoids a spurious failure on the phone.
    """
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.app_package = APP_PACKAGE
    options.app_wait_activity = "*"
    options.no_reset = True
    options.new_command_timeout = 120
    options.set_capability("appium:ignoreHiddenApiPolicyError", True)

    logger.info("Connecting to Appium at %s ...", appium_url)
    driver = webdriver.Remote(appium_url, options=options)
    try:
        driver.activate_app(APP_PACKAGE)
    except WebDriverException:
        pass
    time.sleep(1.5)
    return driver


def find_first(driver, selectors, timeout: float = 6.0):
    """Return the first element (by UiAutomator selector string) that exists.

    Sweeps the whole candidate list every ~300ms until one hits or the budget
    runs out — the same fast-poll strategy as the Node ``findFirst`` (a
    per-selector wait would make each miss cost the full timeout).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in selectors:
            try:
                el = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                if el is not None:
                    return el
            except WebDriverException:
                continue
        time.sleep(0.3)
    raise LookupError("None of these selectors matched:\n  " + "\n  ".join(selectors))


# The bottom-nav Home tab is present only on the app's main screens, never on
# sub-pages (search input, results, a profile), so it's a reliable "we're on a
# top-level screen" anchor — and tapping it lands us on the feed.
_HOME_TAB_SELECTORS = [
    'new UiSelector().descriptionContains("Accueil")',
    'new UiSelector().descriptionContains("Home")',
]
# The feed's search box carries this full descriptive label; the results screen
# does NOT, so it's a specific "we're actually on the feed" check.
_FEED_SEARCH_SELECTORS = [
    'new UiSelector().descriptionContains("Chercher des personnes")',
    'new UiSelector().descriptionContains("Search for people")',
    'new UiSelector().descriptionContains("Rechercher")',
]


def _matches(driver, selectors) -> bool:
    for sel in selectors:
        try:
            if driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel):
                return True
        except WebDriverException:
            continue
    return False


def ensure_feed(driver, max_back: int = 6):
    """Bring the app to the home feed deterministically.

    Strategy, cheapest first: (1) already on the feed → done; (2) a Home nav tab
    is visible → tap it; (3) press Back out of the current sub-page a few times
    to surface the nav bar, tapping Home as soon as it appears; (4) last resort,
    restart the app. Every feed-rooted primitive calls this so it never depends
    on where the previous run left the app (noReset persists the last screen).
    """
    if _matches(driver, _FEED_SEARCH_SELECTORS):
        return
    for attempt in range(max_back + 1):
        if _matches(driver, _HOME_TAB_SELECTORS):
            driver.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR, _HOME_TAB_SELECTORS[0]
            ).click()
            time.sleep(1.2)
            if _matches(driver, _FEED_SEARCH_SELECTORS):
                return
        if attempt < max_back:
            driver.back()
            time.sleep(0.8)
    logger.info("Feed not reachable — restarting app")
    driver.terminate_app(APP_PACKAGE)
    time.sleep(1.0)
    driver.activate_app(APP_PACKAGE)
    time.sleep(3.0)
    if not _matches(driver, _FEED_SEARCH_SELECTORS):
        raise LookupError("Could not reach the LinkedIn home feed")


def human_type(element, text: str) -> None:
    """Type *text* into *element* one character at a time with human jitter.

    Sends keystrokes individually so LinkedIn sees an organic typing cadence
    rather than a single instantaneous field set. Falls back to nothing special
    for whitespace — the pacing alone reads naturally.
    """
    time.sleep(random.uniform(HUMAN_TYPE_PAUSE_MIN, HUMAN_TYPE_PAUSE_MAX))
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(HUMAN_TYPE_MIN, HUMAN_TYPE_MAX))
        if random.random() < HUMAN_TYPE_PAUSE_CHANCE:
            time.sleep(random.uniform(HUMAN_TYPE_PAUSE_MIN, HUMAN_TYPE_PAUSE_MAX))


def dump_screen(driver, path: str) -> None:
    """Save the current screen's XML layout for selector debugging."""
    with open(path, "w") as fh:
        fh.write(driver.page_source)
    logger.info("Saved screen layout to %s", path)
