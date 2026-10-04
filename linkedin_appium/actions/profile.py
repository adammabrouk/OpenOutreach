# linkedin_appium/actions/profile.py
"""Profile primitives: open by slug, read fields, resolve identity.

Replaces the web adapter's Voyager profile scrape (``Lead.get_profile``) and the
slug-based navigation the CRM relies on. Two identity directions, both verified
on-device:

* **slug → profile**: an Android VIEW deep link on the canonical profile URL,
  fired in-process via ``mobile: deepLink`` (no runtime adb dependency).
* **profile → slug**: the "Plus → Coordonnées" (More → Contact info) panel prints
  ``linkedin.com/in/<slug>`` as readable text — the ``public_identifier`` the
  whole CRM keys on.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException

from linkedin_appium.driver import APP_PACKAGE, find_first, ensure_feed

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"linkedin\.com/in/([A-Za-z0-9\-_%]+)")


@dataclass
class Profile:
    name: str
    headline: str
    location: str
    public_identifier: str = ""


def open_profile(driver, public_identifier: str) -> None:
    """Navigate to a profile by its vanity slug via an in-app deep link.

    The deep link only *routes* (rather than merely foregrounding the app) from a
    clean top-level screen, so we reset to the feed first — matching how this was
    verified on-device.
    """
    ensure_feed(driver)
    url = f"https://www.linkedin.com/in/{public_identifier}"
    logger.info("open_profile: deep-linking to %s", url)
    driver.execute_script("mobile: deepLink", {"url": url, "package": APP_PACKAGE})
    time.sleep(3.0)


def _text_of(driver, selector: str) -> str:
    try:
        return driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, selector).text or ""
    except WebDriverException:
        return ""


def read_profile(driver) -> Profile:
    """Read the visible fields off the currently-open profile screen.

    The profile header lays the fields out as consecutive TextViews: name, then
    ``· <degree>``, headline, company·school, location. We read them positionally
    from the accessibility tree (same approach as the search list parser).
    """
    from xml.etree import ElementTree as ET

    root = ET.fromstring(driver.page_source)
    texts = [n.get("text", "") for n in root.iter() if (n.get("text") or "").strip()]
    texts = [t for t in texts if t.strip() != "·"]

    name = texts[0] if texts else ""
    # Headline is the first line after the degree marker that isn't the degree.
    headline = ""
    location = ""
    for t in texts[1:]:
        if re.match(r"^[·•]?\s*[123]", t):
            continue
        if not headline:
            headline = t
            continue
        if "France" in t or re.search(r",\s", t):  # crude location heuristic
            location = t
            break
    return Profile(name=name, headline=headline, location=location)


def read_full_profile(driver, public_identifier: str = "") -> dict:
    """Return a rich profile dict for the CRM/ML pipeline from the open profile.

    Shape matches what ``build_profile_text`` and ``create_enriched_lead`` expect
    (flat dict: ``public_identifier``, ``headline``, ``location_name``,
    ``summary``, ``positions``, ``educations``). ``urn`` is intentionally absent —
    the Android app never exposes it and nothing downstream needs it any more
    (conversations are reached by slug, not URN).

    TODO(phone-validate): ``summary`` (About) and ``positions``/``educations``
    (Experience/Education) require scrolling the profile and parsing those
    sections; the selectors for them still need an on-device pass. For now the
    embedding text is built from the header (headline + location), which we *have*
    validated — weaker but functional. See ``_read_about`` / ``_read_experience``.
    """
    header = read_profile(driver)
    return {
        "public_identifier": public_identifier,
        "name": header.name,
        "headline": header.headline,
        "location_name": header.location,
        "summary": _read_about(driver),
        "positions": _read_experience(driver),
        "educations": [],
    }


def _read_about(driver) -> str:
    """About/summary text. TODO(phone-validate): scroll to the About section."""
    return ""


def _read_experience(driver) -> list[dict]:
    """Experience positions. TODO(phone-validate): scroll to Experience section.

    Each item should be {title, company_name, location, description}.
    """
    return []


def resolve_public_identifier(driver) -> str:
    """From an open profile, open More → Contact info and read the slug.

    Returns the ``public_identifier`` (e.g. ``michaelleclercq``) or "" if the
    contact panel has no public URL. Leaves the app on the contact sheet; callers
    that need the main profile press Back or re-open.
    """
    more = find_first(driver, [
        'new UiSelector().description("Plus")',
        'new UiSelector().descriptionContains("Plus")',
        'new UiSelector().text("Plus")',
    ])
    more.click()
    time.sleep(1.2)

    contact = find_first(driver, [
        'new UiSelector().text("Coordonnées")',
        'new UiSelector().textContains("Coordonn")',
        'new UiSelector().text("Contact info")',
        'new UiSelector().textContains("Contact")',
    ])
    contact.click()
    time.sleep(1.5)

    m = _SLUG_RE.search(driver.page_source)
    slug = m.group(1) if m else ""
    logger.info("resolve_public_identifier: %r", slug)
    return slug
