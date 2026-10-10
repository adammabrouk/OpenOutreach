# linkedin_appium/behavior — record human usage, learn per-context motor patterns.
"""Behavior profiling for the Android LinkedIn adapter.

The adapter decides *what* to do (search a person, open a profile, reply to a
message). This package decides *how and when* each action is executed, so the
aggregate statistics of the live automation match the human whose sessions were
recorded — without ever replaying a captured session verbatim.

Pipeline:

    record.py   adb `getevent` capture (finger input; Appium NOT attached) +
                optional keystroke log  ->  raw session files on disk
    features.py raw events -> typed Gesture/Keystroke features (device-normalized)
    profile.py  features -> BehaviorProfile: per-Context fitted distributions you
                SAMPLE from at run time (fresh every call, tails clamped)

Nothing here is global: a profile holds an independent model per ``Context``,
because the motor signature of fast people-search flings is nothing like the
slow, bursty typing of composing a reply. A context with no recorded data falls
back to the profile's global model, so the adapter degrades gracefully.
"""
from linkedin_appium.behavior.context import Context
from linkedin_appium.behavior.features import Gesture, Keystroke, SessionFeatures
from linkedin_appium.behavior.profile import BehaviorProfile

__all__ = [
    "Context",
    "Gesture",
    "Keystroke",
    "SessionFeatures",
    "BehaviorProfile",
]
