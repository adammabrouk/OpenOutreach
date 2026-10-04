# linkedin_appium/actions/connect.py
"""Connect primitive — send a connection request from an open profile.

Replaces the web adapter's ``send_connection_request``. Operates on the
currently-open profile (the caller opens it first, mirroring the web flow where
status-check and connect share the profile page). Plain invite, no note — the
follow-up message is a separate primitive.

Mobile invite flow (French UI):
  1. Tap the header invite affordance ("Inviter … à rejoindre votre réseau" /
     "Se connecter").
  2. If a sheet appears, choose "Envoyer sans note" / "Envoyer" (send).
  3. LinkedIn may instead show a weekly-invite-limit sheet → surface as a limit.
  4. Confirm by re-reading status (expected PENDING).
"""
from __future__ import annotations

import logging
import time

from selenium.common.exceptions import WebDriverException
from appium.webdriver.common.appiumby import AppiumBy

from linkedin_appium.actions.status import read_status
from linkedin_appium.enums import ConnectionStatus
from linkedin_appium.exceptions import NoConnectButton, ReachedConnectionLimit

logger = logging.getLogger(__name__)

_INVITE_BUTTON = [
    'new UiSelector().descriptionContains("à rejoindre votre réseau")',
    'new UiSelector().textContains("Se connecter")',
    'new UiSelector().descriptionContains("Se connecter")',
    'new UiSelector().textContains("Connect")',
]
# The send-without-note affordance is phrased "Envoyez une invitation sans note"
# on current builds; match on the stable "sans note" fragment first, then fall
# back to a plain "Envoyer"/"Send" confirmation.
_SEND_BUTTON = [
    'new UiSelector().textContains("sans note")',
    'new UiSelector().descriptionContains("sans note")',
    'new UiSelector().textContains("without a note")',
    'new UiSelector().text("Envoyer")',
    'new UiSelector().descriptionContains("Envoyer")',
    'new UiSelector().text("Send")',
]
_LIMIT_MARKERS = ("limite hebdomadaire", "atteint la limite", "weekly invitation limit",
                  "You’ve reached", "You've reached")


def _try_click(driver, selectors, timeout: float = 4.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in selectors:
            try:
                el = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                el.click()
                return True
            except WebDriverException:
                continue
        time.sleep(0.3)
    return False


def send_connection_request(driver) -> ConnectionStatus:
    """Send an invite from the open profile; return the resulting status.

    Raises ``NoConnectButton`` if there's no invite affordance (already
    connected/pending, or a restricted profile) and ``ReachedConnectionLimit``
    when LinkedIn blocks the invite with its weekly-limit sheet.
    """
    if not _try_click(driver, _INVITE_BUTTON):
        raise NoConnectButton("no invite affordance on this profile")
    time.sleep(1.5)

    # A confirmation sheet is common but not universal; if the invite fired
    # directly, the send button simply won't be there.
    _try_click(driver, _SEND_BUTTON, timeout=3.0)
    time.sleep(2.0)

    if any(m in driver.page_source for m in _LIMIT_MARKERS):
        raise ReachedConnectionLimit("weekly invitation limit reached")

    status = read_status(driver)
    logger.info("send_connection_request → %s", status.value)
    return status
