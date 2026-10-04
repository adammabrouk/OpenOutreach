# linkedin_appium/actions/status.py
"""Connection-status primitive — read the relationship state off a profile.

Replaces the web adapter's ``get_connection_status``. Signals on the Android
profile header (French UI), in priority order:

* **Pending**  — "En attente" / "Invitation en attente" / "Annuler l'invitation".
* **Not connected** — an invite affordance ("Inviter … à rejoindre votre réseau",
  "Se connecter"), or a 2nd/3rd-degree marker.
* **Connected** — 1st-degree marker ("· 1er") with no invite affordance.

Note a "Message" button is present even for non-connections on mobile, so it is
*not* a reliable connected signal on its own — degree + invite state is.
"""
from __future__ import annotations

import logging
import re

from linkedin_appium.enums import ConnectionStatus

logger = logging.getLogger(__name__)

_PENDING_MARKERS = ("En attente", "Invitation en attente", "Annuler l’invitation",
                    "Annuler l'invitation", "Pending", "Withdraw")
_INVITE_MARKERS = ("à rejoindre votre réseau", "Se connecter", "Connexion", "Connect")
_DEGREE_RE = re.compile(r"[·•]\s*(1)(?:er)?\b")


def read_status(driver) -> ConnectionStatus:
    """Classify the currently-open profile's connection state."""
    src = driver.page_source

    if any(m in src for m in _PENDING_MARKERS):
        logger.info("read_status: PENDING")
        return ConnectionStatus.PENDING

    if any(m in src for m in _INVITE_MARKERS):
        logger.info("read_status: NOT_CONNECTED (invite affordance present)")
        return ConnectionStatus.NOT_CONNECTED

    if _DEGREE_RE.search(src):
        logger.info("read_status: CONNECTED (1st degree, no invite)")
        return ConnectionStatus.CONNECTED

    logger.info("read_status: NOT_CONNECTED (default)")
    return ConnectionStatus.NOT_CONNECTED
