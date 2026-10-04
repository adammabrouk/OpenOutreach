# linkedin_appium/url_utils.py
"""Slug ↔ canonical-URL helpers (ports ``linkedin_cli.url_utils``).

The CRM stores both a ``public_identifier`` (vanity slug) and a canonical
``linkedin_url``; these keep the two in sync. Unchanged from the web adapter —
the canonical profile URL is also what the profile deep link is built from.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

_IN_PATH = re.compile(r"/in/([^/?#]+)")


def public_id_to_url(public_identifier: str) -> str:
    """``michaelleclercq`` → ``https://www.linkedin.com/in/michaelleclercq``."""
    return f"https://www.linkedin.com/in/{public_identifier}"


def url_to_public_id(url: str) -> str | None:
    """Extract the vanity slug from a LinkedIn profile URL, or None.

    Handles trailing slashes, query strings, and percent-encoding.
    """
    if not url:
        return None
    m = _IN_PATH.search(url)
    if not m:
        return None
    return unquote(m.group(1)).strip("/") or None
