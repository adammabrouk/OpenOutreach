# linkedin/browser/session.py
from __future__ import annotations

import logging
import random
import time
from functools import cached_property

from linkedin.conf import MIN_DELAY, MAX_DELAY

logger = logging.getLogger(__name__)


def random_sleep(min_val, max_val):
    delay = random.uniform(min_val, max_val)
    logger.debug(f"Pause: {delay:.2f}s")
    time.sleep(delay)


class AccountSession:
    """Owns the Appium driver that automates the LinkedIn Android app.

    The phone holds a persistent login (the app's own session survives across
    Appium sessions via ``noReset``), so unlike the old Playwright session there
    are no cookies to persist and no login to drive — ``ensure_driver`` just
    attaches to the running app and brings it to the foreground.
    """

    def __init__(self, linkedin_profile):
        self.linkedin_profile = linkedin_profile
        self.django_user = linkedin_profile.user

        # Active campaign — set by the daemon before each lane execution
        self.campaign = None

        # Appium driver — created on first access or after a crash
        self.driver = None

    @cached_property
    def campaigns(self):
        """All campaigns this user belongs to (cached)."""
        from linkedin.models import Campaign
        return list(Campaign.objects.filter(users=self.django_user))

    def ensure_driver(self):
        """Attach the Appium driver + foreground the app. Call before automating."""
        from linkedin_appium.driver import new_driver, APP_PACKAGE
        from selenium.common.exceptions import WebDriverException

        if self.driver is None:
            logger.debug("Launching Appium driver for %s", self)
            self.driver = new_driver()
            return self.driver

        # Reuse the live driver, re-foregrounding the app if it drifted.
        try:
            self.driver.activate_app(APP_PACKAGE)
        except WebDriverException:
            logger.warning("Appium driver stale for %s — relaunching", self)
            self.close()
            self.driver = new_driver()
        return self.driver

    @cached_property
    def self_profile(self) -> dict:
        """Authenticated user's profile, discovered once per session.

        Returns the dict shape the CRM expects (``public_identifier``, ``name``,
        ``first_name``, ``headline``); ``urn`` is absent on mobile (the app never
        exposes it) and nothing downstream needs it any more.
        """
        from linkedin_appium.actions.self_profile import discover_self_profile
        from linkedin.db.leads import register_self_lead

        self.ensure_driver()
        profile = discover_self_profile(self.driver)
        register_self_lead(self, profile)
        return profile

    def wait(self, min_delay=MIN_DELAY, max_delay=MAX_DELAY):
        random_sleep(min_delay, max_delay)

    def reauthenticate(self):
        """Restart the app/driver.

        The phone owns the login, so there's nothing to re-authenticate in the
        web sense — a restart clears any transient bad UI state. If the app comes
        back logged out, the user must sign in on the device manually.
        """
        logger.warning("Restarting Appium driver for %s", self)
        self.close()
        self.ensure_driver()

    def close(self):
        if self.driver is not None:
            try:
                self.driver.quit()
                logger.info("Appium driver closed (%s)", self)
            except Exception as e:
                logger.debug("Error closing driver: %s", e)
            finally:
                self.driver = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return self.linkedin_profile.linkedin_username
