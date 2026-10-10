# linkedin_appium/behavior/context.py
"""The activity taxonomy a BehaviorProfile is keyed by.

Each ``Context`` is a distinct *motor regime* — a situation in which a human's
scroll speed, dwell time and typing cadence are internally consistent but
differ from the other regimes. Searching for someone is fast and repetitive;
replying to a message is slow and bursty with long reading pauses. The profile
fits and samples these independently, so one never contaminates the other.

Values are plain strings so a recording can be tagged on the command line
(``--context search``) and so a profile serialises to readable JSON. The set is
intentionally open: add a member here and the record/fit/sample path picks it up
with no other change. Map each adapter action module to the context it runs in:

    actions/search.py   -> SEARCH   (type query, fling result list)
    actions/profile.py  -> PROFILE  (scroll a profile, read)
    actions/message.py  -> MESSAGE  (compose + send a reply)
    actions/thread.py   -> THREAD   (scan the inbox / a conversation)
    actions/connect.py  -> CONNECT  (tap invite, maybe a short note)
    feed browsing        -> FEED
"""
from __future__ import annotations

from enum import Enum


class Context(str, Enum):
    SEARCH = "search"       # typing a query, flinging through people results
    FEED = "feed"           # browsing / scrolling the home feed
    PROFILE = "profile"     # reading a single profile (scroll + dwell)
    MESSAGE = "message"     # composing and sending a reply (typing-heavy)
    THREAD = "thread"       # scanning the inbox or a conversation
    CONNECT = "connect"     # tapping invite, optionally typing a short note

    @classmethod
    def coerce(cls, value: "Context | str") -> "Context":
        """Accept either an enum member or its string value."""
        return value if isinstance(value, cls) else cls(value)
