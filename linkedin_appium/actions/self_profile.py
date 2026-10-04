# linkedin_appium/actions/self_profile.py
"""Self-discovery primitive (whoami) — replaces ``discover_self_profile``.

The authenticated user's name + headline are printed on the feed profile
drawer's header button (``…_mirror_component_profile_container``), whose
content-desc is ``"Name, Headline, Location, Company"``. Reading it there is
enough for the only thing orchestration needs from self-discovery: the seller's
name for the follow-up identity binding (``seller_name_from``). The self slug is
not needed (the self_lead is disqualified), so we don't navigate into the
profile — tapping the same button would open it if that ever changes.
"""
from __future__ import annotations

import logging
import time

from appium.webdriver.common.appiumby import AppiumBy

from linkedin_appium.driver import find_first, ensure_feed

logger = logging.getLogger(__name__)

_MENU_BUTTON = [
    'new UiSelector().descriptionContains("accéder à mon profil")',
    'new UiSelector().descriptionContains("Mon profil")',
    'new UiSelector().descriptionContains("your profile")',
    'new UiSelector().descriptionContains("menu")',
]
_DRAWER_HEADER = [
    'new UiSelector().resourceIdMatches(".*mirror_component_profile_container.*")',
    'new UiSelector().resourceIdMatches(".*profile_container.*")',
]


def discover_self_profile(driver) -> dict:
    """Open the profile drawer and return the user's own profile as a dict.

    Shape: ``{public_identifier, name, first_name, headline, location}``. The
    self slug is best-effort (``""`` for now); nothing critical depends on it —
    it only keys the disqualified self-Lead, which ``register_self_lead`` skips
    when the slug is missing.

    The open-drawer-and-read is retried a few times: on a daemon cold start the
    app can be on a non-feed interstitial when the first task runs, so the drawer
    tap misses. Retrying (re-``ensure_feed`` + re-tap) absorbs that transient
    instead of failing the task and marking the deal FAILED — this is the single
    chokepoint every follow-up passes through (``session.self_profile``).
    """
    header = None
    desc = ""
    for attempt in range(3):
        try:
            ensure_feed(driver)
            find_first(driver, _MENU_BUTTON).click()
            time.sleep(2.0)
            header = find_first(driver, _DRAWER_HEADER)
            desc = header.get_attribute("content-desc") or ""
            break
        except LookupError:
            logger.info("discover_self_profile: drawer miss (attempt %d/3)", attempt + 1)
            driver.back()  # back out of whatever opened, then retry from the feed
            time.sleep(1.0)
    else:
        raise LookupError("discover_self_profile: profile drawer never appeared after 3 attempts")

    parts = [p.strip() for p in desc.split(",") if p.strip()]
    name = parts[0] if parts else ""
    headline = parts[1] if len(parts) > 1 else ""
    location = parts[2] if len(parts) > 2 else ""

    driver.back()  # close the drawer, leave the app on the feed
    time.sleep(0.8)

    profile = {
        "public_identifier": "",  # TODO(phone-validate): resolve own slug via drawer→profile
        "name": name,
        "first_name": name.split()[0] if name else "",
        "headline": headline,
        "location": location,
    }
    logger.info("discover_self_profile: %s — %s", name, headline)
    return profile


def seller_name_from(driver) -> str:
    """First name of the authenticated user (for the follow-up identity binding)."""
    return discover_self_profile(driver)["first_name"]
