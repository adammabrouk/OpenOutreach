# linkedin_appium/enums.py
"""State enums for the adapter and the CRM.

``ProfileState`` is the CRM's lead state machine (formerly in
``linkedin_cli.enums``); it lives here now that this package is the LinkedIn
adapter. ``ConnectionStatus`` is what the phone can actually *observe* on a
profile; ``to_profile_state`` maps an observation onto the richer CRM state.
"""
from __future__ import annotations

from enum import Enum


class ProfileState(str, Enum):
    QUALIFIED = "Qualified"
    READY_TO_CONNECT = "Ready to Connect"
    PENDING = "Pending"
    CONNECTED = "Connected"
    COMPLETED = "Completed"
    FAILED = "Failed"


class ConnectionStatus(str, Enum):
    NOT_CONNECTED = "not_connected"   # invite affordance present (or distant degree)
    PENDING = "pending"               # invite already sent, awaiting acceptance
    CONNECTED = "connected"           # 1st-degree connection


def to_profile_state(status: ConnectionStatus) -> ProfileState:
    """Map an on-device connection observation onto a CRM ``ProfileState``.

    NOT_CONNECTED maps to READY_TO_CONNECT (the state a candidate sits in when a
    connect attempt is warranted); PENDING and CONNECTED map straight across.
    """
    return {
        ConnectionStatus.CONNECTED: ProfileState.CONNECTED,
        ConnectionStatus.PENDING: ProfileState.PENDING,
        ConnectionStatus.NOT_CONNECTED: ProfileState.READY_TO_CONNECT,
    }[status]
