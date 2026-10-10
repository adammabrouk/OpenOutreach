# linkedin_appium/behavior/demo_connect.py
"""Watch the fingerprint drive real invites — the first live humanizer test.

For each name: search for the person, open the top hit, glance at the profile
with a couple of sampled flings, then send a connection request whose taps and
waits are sampled from the ``connect`` context of the behavior profile. The
point is to *watch the phone* and judge whether the pacing reads as human.

    # start the Appium server first (separate terminal): appium
    python -m linkedin_appium.behavior.demo_connect "Bill Gates" "Satya Nadella" \\
        --profile data/behavior_profiles/default.json

Sends real invites from the logged-in account — keep the list short. Stops early
on LinkedIn's weekly-invite limit.
"""
from __future__ import annotations

import argparse
import logging
import random
import time
from pathlib import Path

from linkedin_appium.driver import new_driver
from linkedin_appium.actions.search import search_people, open_hit
from linkedin_appium.actions.connect import send_connection_request
from linkedin_appium.exceptions import NoConnectButton, ReachedConnectionLimit
from linkedin_appium.behavior.context import Context
from linkedin_appium.behavior.humanizer import Humanizer
from linkedin_appium.behavior.profile import BehaviorProfile
from linkedin_appium.behavior.keyboard import KeyMap

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("demo_connect")


def pick_hit(hits, name):
    """Choose the intended person from the result rows.

    Taking the first row grabs whoever LinkedIn ranks top — often a 1st-degree
    contact from your own network, not the public figure you searched. Prefer an
    exact name match that you can still invite (so not already connected), and
    among those the more-distant degree (a celebrity is 2nd/3rd, a colleague 1st).
    """
    exact = [h for h in hits if h.name.strip().lower() == name.strip().lower()]
    pool = exact or hits
    pool = sorted(pool, key=lambda h: (
        not h.can_invite,                                   # invitable first
        -(int(h.degree) if h.degree.isdigit() else 0),      # then most-distant degree
    ))
    return pool[0]


def run(names: list[str], profile_path: str, keymap_path: str) -> None:
    profile = BehaviorProfile.load(profile_path)
    logger.info("loaded profile %s\n%s", profile_path, profile.summary())

    keymap = None
    if Path(keymap_path).exists():
        keymap = KeyMap.load(Path(keymap_path))
        logger.info("loaded keymap %s (%d keys) — typing via real key taps",
                    keymap_path, len(keymap.keys))
    else:
        logger.warning("no keymap at %s — typing falls back to injection "
                       "(run `python -m linkedin_appium.behavior.keyboard calibrate`)",
                       keymap_path)

    driver = new_driver()
    try:
        for name in names:
            logger.info("=== %s ===", name)
            # search_people() brings the app to the feed itself — no extra
            # ensure_feed() here, which was double-navigating and reloading.
            hits = search_people(driver, name,
                                 humanizer=Humanizer(driver, profile, Context.SEARCH, keymap))
            if not hits:
                logger.warning("no results for %r — skipping", name)
                continue
            for h in hits[:6]:
                logger.info("  hit: %-22s degree=%s invite=%s | %s", h.name, h.degree, h.can_invite, h.headline[:40])
            chosen = pick_hit(hits, name)
            logger.info("chose: %s (degree %s, invite=%s)", chosen.name, chosen.degree, chosen.can_invite)
            if not open_hit(driver, chosen):
                logger.warning("could not open %r — skipping", name)
                continue

            # Reading the profile before inviting is part of the connect regime.
            # Pinned down: you read a profile top-to-bottom, not upward.
            glance = Humanizer(driver, profile, Context.CONNECT, keymap)
            glance.dwell()
            glance.scroll(n=random.randint(1, 2), direction="down")
            glance.dwell()

            try:
                status = send_connection_request(
                    driver, humanizer=Humanizer(driver, profile, Context.CONNECT, keymap))
                logger.info("%s → %s", name, status.value)
            except NoConnectButton:
                logger.info("%s → no invite affordance (already connected/pending?)", name)
            except ReachedConnectionLimit:
                logger.warning("weekly invite limit reached — stopping")
                break

            time.sleep(1.0)
    finally:
        driver.quit()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="+", help="people to search and invite")
    parser.add_argument("--profile", default="data/behavior_profiles/default.json")
    parser.add_argument("--keymap", default="data/behavior_profiles/default.keymap.json")
    args = parser.parse_args(argv)
    run(args.names, args.profile, args.keymap)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
