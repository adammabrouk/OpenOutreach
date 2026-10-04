# linkedin_appium/actions/message.py
"""Message primitive — send a text into an open conversation.

Replaces the web adapter's ``send_raw_message``. Operates on an already-open
thread (caller uses ``thread.open_thread(slug)`` to get there), types into the
compose box, and taps send. Returns True on success.
"""
from __future__ import annotations

import logging
import time

from selenium.common.exceptions import WebDriverException
from appium.webdriver.common.appiumby import AppiumBy

from linkedin_appium.driver import find_first, human_type

logger = logging.getLogger(__name__)

_COMPOSE_BOX = [
    'new UiSelector().text("Rédiger un message…")',
    'new UiSelector().textContains("Rédiger un message")',
    'new UiSelector().textContains("Write a message")',
    'new UiSelector().className("android.widget.EditText")',
]
# Tapping "Message" on someone who isn't a real 1st-degree connection (a stale
# CONNECTED deal, or an invite that was never actually accepted) opens LinkedIn's
# Premium/InMail upsell instead of a composer — there is no compose box to find.
# We detect that modal so the caller can skip the deal gracefully rather than
# crashing the task on a LookupError and retrying the same dead end forever.
_UPSELL_MODAL = [
    'new UiSelector().resourceIdMatches(".*upsell_modal_content.*")',
    'new UiSelector().resourceIdMatches(".*upsell.*")',
]


def _upsell_present(driver) -> bool:
    from linkedin_appium.driver import _matches  # local: same-module private helper
    return _matches(driver, _UPSELL_MODAL)
_SEND_BUTTON = [
    'new UiSelector().description("Envoyer")',
    'new UiSelector().text("Envoyer")',
    'new UiSelector().descriptionContains("Envoyer")',
    'new UiSelector().description("Send")',
    'new UiSelector().descriptionContains("Send")',
]


def send_message(driver, text: str) -> bool:
    """Type *text* into the open thread's compose box and send it.

    Returns False (not raises) when the composer never appears because LinkedIn
    showed the Premium/InMail upsell — i.e. this person can't be messaged for
    free. The caller treats a False like any send failure (re-queue for connect)
    instead of failing the task.
    """
    try:
        box = find_first(driver, _COMPOSE_BOX)
    except LookupError:
        if _upsell_present(driver):
            logger.warning("send_message: Premium upsell shown — not a messageable connection")
            driver.back()  # dismiss the modal, leave the app in a clean state
            time.sleep(0.6)
            return False
        raise
    box.click()
    time.sleep(0.6)
    human_type(box, text)
    time.sleep(0.8)

    for sel in _SEND_BUTTON:
        try:
            driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel).click()
            time.sleep(2.0)
            logger.info("send_message: sent (%d chars)", len(text))
            return True
        except WebDriverException:
            continue
    logger.warning("send_message: no send button found")
    return False
