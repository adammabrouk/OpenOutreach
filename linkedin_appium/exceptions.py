# linkedin_appium/exceptions.py
"""Adapter-level exceptions, mirroring the web adapter's ``linkedin_cli.exceptions``.

Kept name-compatible so the orchestration's existing except-clauses (connect
task: ReachedConnectionLimit / ProfileInaccessibleError / SkipProfile) map over
cleanly when the daemon is rewired onto this adapter.
"""
from __future__ import annotations


class LinkedInAppiumError(Exception):
    """Base for all adapter errors."""


class NoConnectButton(LinkedInAppiumError):
    """No invite affordance on the profile (already connected/pending/restricted)."""


class ReachedConnectionLimit(LinkedInAppiumError):
    """LinkedIn blocked the invite with its weekly-limit sheet."""


class ProfileInaccessibleError(LinkedInAppiumError):
    """The profile could not be opened/read."""


class SkipProfile(LinkedInAppiumError):
    """Recoverable per-profile skip (transient UI / unexpected state)."""


class AuthenticationError(LinkedInAppiumError):
    """The app is not logged in / the session is not authenticated.

    On mobile the phone holds a persistent login, so this should be rare — it
    signals the LinkedIn app is showing a login/logged-out screen and needs
    manual sign-in on the device.
    """


class CheckpointChallengeError(LinkedInAppiumError):
    """LinkedIn is showing a security checkpoint that needs manual clearing.

    Carries the challenge ``url`` (best-effort; may be empty on mobile) so the
    daemon can tell the user where to resolve it.
    """

    def __init__(self, message: str = "", url: str = ""):
        super().__init__(message)
        self.url = url
