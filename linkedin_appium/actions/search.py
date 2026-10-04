# linkedin_appium/actions/search.py
"""People search primitive (Android LinkedIn app via Appium).

Ports ``linkedin/pipeline/search.py`` to the phone. The web adapter used Voyager
and got a ``public_identifier`` (vanity slug) per hit; the mobile results tree
does *not* expose the slug, so a search hit here is identified by display name +
connection degree, and the slug (if we ever need it) is resolved later by
opening the profile. Results are read by parsing the accessibility tree
(``page_source``) — reliable for list screens, unlike per-element polling.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from appium.webdriver.common.appiumby import AppiumBy

from linkedin_appium.driver import find_first, ensure_feed, human_type

logger = logging.getLogger(__name__)

# "Name • 2e" / "Name • 2nd" — trailing token is the connection degree. The
# optional "Vérifié"/"Verified" badge sits between the name and the bullet on
# verified accounts, so it's stripped out of the captured name.
_NAME_DEGREE = re.compile(
    r"^(?P<name>.+?)\s*(?:Vérifié|Verified)?\s*[•·]\s*(?P<degree>1|2|3)(?:e|er|nd|rd|th|\+)?\s*$"
)


@dataclass
class SearchHit:
    name: str
    degree: str           # "1", "2", or "3"
    headline: str
    location: str
    can_invite: bool      # inline "Invite to connect" button present in the row


def _text(node) -> str:
    return node.get("text") or ""


def _desc(node) -> str:
    return node.get("content-desc") or ""


def _parse_people(page_source: str) -> list[SearchHit]:
    """Extract person rows from the People-results accessibility tree.

    A row is anchored by a TextView whose text is ``"Name • <degree>"``; the two
    following sibling TextViews are the headline and location, and an
    ``Inviter …`` descendant marks an available inline connect button.
    """
    root = ET.fromstring(page_source)
    hits: list[SearchHit] = []
    seen: set[str] = set()

    # Walk parents so we can read a matched node's siblings.
    parent = {child: p for p in root.iter() for child in p}

    for node in root.iter():
        # The name+degree anchor may live in either `text` (most rows) or
        # `content-desc` (verified accounts render the name only in the desc).
        m = _NAME_DEGREE.match(_text(node)) or _NAME_DEGREE.match(_desc(node))
        if not m:
            continue
        name = m.group("name").strip()
        degree = m.group("degree")
        if name in seen:
            continue
        seen.add(name)

        siblings = list(parent.get(node, []))
        texts = [_text(s) for s in siblings if _text(s)]
        # texts[0] is the "Name • degree" line itself.
        headline = texts[1] if len(texts) > 1 else ""
        location = texts[2] if len(texts) > 2 else ""

        # The connect button lives a couple levels up in the row container.
        row = parent.get(parent.get(node)) if parent.get(node) is not None else None
        can_invite = False
        if row is not None:
            can_invite = any(
                _desc(n).startswith("Inviter") or "à rejoindre votre réseau" in _desc(n)
                for n in row.iter()
            )

        hits.append(SearchHit(name=name, degree=degree, headline=headline,
                              location=location, can_invite=can_invite))
    return hits


def search_people(driver, query: str) -> list[SearchHit]:
    """Run a People search for *query* and return the visible result rows."""
    ensure_feed(driver)

    logger.info("search: opening search for %r", query)
    entry = find_first(driver, [
        'new UiSelector().descriptionContains("Chercher")',
        'new UiSelector().textContains("Chercher")',
        'new UiSelector().descriptionContains("Search")',
        'new UiSelector().textContains("Search")',
    ])
    entry.click()
    time.sleep(1.0)

    field = find_first(driver, [
        'new UiSelector().className("android.widget.EditText")',
    ])
    field.click()
    human_type(field, query)
    time.sleep(0.5)
    driver.press_keycode(66)  # IME search / enter
    time.sleep(2.5)

    people_filter = find_first(driver, [
        'new UiSelector().descriptionContains("Filtrer par Personnes")',
        'new UiSelector().descriptionContains("Personnes")',
        'new UiSelector().textContains("Personnes")',
        'new UiSelector().descriptionContains("People")',
        'new UiSelector().textContains("People")',
    ])
    people_filter.click()
    time.sleep(2.5)

    hits = _parse_people(driver.page_source)
    logger.info("search: %d people hit(s) for %r", len(hits), query)
    return hits


def open_hit(driver, hit: SearchHit) -> bool:
    """Open a search hit's profile by tapping its result row (by display name).

    Returns True if the row was found and tapped. Mobile search rows carry no
    slug, so opening the profile is the only way to resolve identity — the caller
    then reads/records it and presses Back to return to the results list.
    """
    from selenium.common.exceptions import WebDriverException

    for sel in (
        f'new UiSelector().descriptionContains("{hit.name}")',
        f'new UiSelector().textContains("{hit.name}")',
    ):
        try:
            driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel).click()
            time.sleep(3.0)
            return True
        except WebDriverException:
            continue
    logger.warning("open_hit: could not find row for %r", hit.name)
    return False


def back_to_results(driver) -> None:
    """Return to the People-results list after visiting a hit's profile."""
    driver.back()
    time.sleep(1.5)
