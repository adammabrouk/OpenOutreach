# linkedin_appium/actions/thread.py
"""Conversation primitive — read a thread's messages.

Replaces the web adapter's conversation scrape behind ``sync_conversation``. On
the Android thread screen each message is a sender/time header TextView of the
form ``"<Sender> … • HH:MM"`` followed by its body TextView(s); date separators
("VENDREDI", "SAMEDI", …) sit between messages. A message is *outgoing* when its
sender matches the authenticated user — the same ``is_outgoing`` distinction the
CRM's ChatMessage rows and the chat-summary pipeline rely on (only incoming
messages reach fact extraction).

The target conversation is reached deterministically by opening the person's
profile (by slug) and tapping Message, reusing the proven ``open_profile``.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from selenium.common.exceptions import WebDriverException
from appium.webdriver.common.appiumby import AppiumBy

from linkedin_appium.driver import find_first
from linkedin_appium.actions.profile import open_profile

logger = logging.getLogger(__name__)

# "Adam Mabrouk x  •  18:40" — sender before the bullet, HH:MM after it.
_HEADER_RE = re.compile(r"^(?P<sender>.+?)\s*[x×]?\s*[•·]\s*\d{1,2}:\d{2}\s*$")
# All-caps day/date separators to skip.
_SEPARATOR_RE = re.compile(r"^[A-ZÀ-Ÿ0-9\s]{3,}$")


@dataclass
class Message:
    sender: str
    text: str
    is_outgoing: bool


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def read_thread(driver, self_name: str) -> list[Message]:
    """Parse the currently-open conversation into ordered messages.

    *self_name* is the authenticated user's display name (from
    ``discover_self_profile``); a message whose sender matches it is outgoing.
    """
    self_first = self_name.split()[0].lower() if self_name else ""
    root = ET.fromstring(driver.page_source)
    lines = [_norm(n.get("text") or "") for n in root.iter() if (n.get("text") or "").strip()]

    messages: list[Message] = []
    cur_sender = None
    cur_outgoing = False
    body: list[str] = []

    def flush():
        if cur_sender is not None and body:
            messages.append(Message(cur_sender, "\n".join(body).strip(), cur_outgoing))

    for line in lines:
        m = _HEADER_RE.match(line)
        if m:
            flush()
            cur_sender = _norm(m.group("sender"))
            cur_outgoing = self_first and self_first in cur_sender.lower()
            body = []
            continue
        if cur_sender is None:
            continue  # pre-conversation chrome (name/headline header)
        if _SEPARATOR_RE.match(line) and len(line) <= 12:
            continue  # date separator
        if line in ("Rédiger un message…", "Compose message…"):
            continue
        body.append(line)
    flush()

    logger.info("read_thread: %d message(s), %d incoming",
                len(messages), sum(1 for x in messages if not x.is_outgoing))
    return messages


def open_thread(driver, public_identifier: str) -> None:
    """Open the message thread with a person, by opening their profile → Message."""
    open_profile(driver, public_identifier)
    btn = find_first(driver, [
        'new UiSelector().text("Message")',
        'new UiSelector().descriptionContains("Message")',
        'new UiSelector().textContains("Envoyer un message")',
    ])
    btn.click()
    time.sleep(3.0)
