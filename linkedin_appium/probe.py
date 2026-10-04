# linkedin_appium/probe.py
"""Stepwise primitive probe — test one LinkedIn-on-Appium primitive at a time.

Run against a plugged-in, unlocked phone with LinkedIn installed + logged in,
and the Appium server up (../appium-pipeline/run.sh already starts it):

    .venv/bin/python -m linkedin_appium.probe hello
    .venv/bin/python -m linkedin_appium.probe search "growth marketing"

Each primitive gets its own subcommand so we can build + verify the adapter one
verb at a time before wiring any orchestration back on top.
"""
from __future__ import annotations

import logging
import sys

import time

from appium.webdriver.common.appiumby import AppiumBy

from linkedin_appium.driver import new_driver, dump_screen, find_first, ensure_feed

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("probe")

SCREEN_DUMP = "/tmp/li-appium-screen.xml"


def probe_hello(driver, args) -> None:
    """Step 0 — foundation: connect, foreground LinkedIn, prove the session."""
    log.info("→ Current package : %s", driver.current_package)
    log.info("→ Current activity: %s", driver.current_activity)
    dump_screen(driver, SCREEN_DUMP)
    log.info("✅ Session works. Screen layout dumped to %s", SCREEN_DUMP)


def probe_search(driver, args) -> None:
    """Step 1 — People search primitive; prints structured result rows."""
    from linkedin_appium.actions.search import search_people

    query = args[0] if args else "growth marketing"
    hits = search_people(driver, query)
    if not hits:
        dump_screen(driver, SCREEN_DUMP)
        log.warning("No people parsed — layout dumped to %s", SCREEN_DUMP)
        return
    for i, h in enumerate(hits, 1):
        inv = "invite✓" if h.can_invite else "       "
        log.info("  %2d. [%s·%se] %s — %s (%s)", i, inv, h.degree, h.name, h.headline, h.location)
    log.info("✅ Parsed %d people.", len(hits))


def probe_profile(driver, args) -> None:
    """Step 2 (exploratory) — open the first search hit and dump the profile."""
    from linkedin_appium.actions.search import search_people

    query = args[0] if args else "growth marketing"
    hits = search_people(driver, query)
    if not hits:
        log.warning("No hits to open."); return
    target = hits[0]
    log.info("→ Opening profile: %s", target.name)

    row = find_first(driver, [
        f'new UiSelector().descriptionContains("{target.name}")',
        f'new UiSelector().textContains("{target.name}")',
    ])
    row.click()
    time.sleep(3.0)

    log.info("→ Activity: %s", driver.current_activity)
    # Surface any deep-link / URN / identifier hints the profile screen carries.
    src = driver.page_source
    import re
    for pat in [r'linkedin://[^\s"<]+', r'urn:li:[^\s"<]+', r'/in/[A-Za-z0-9\-]+']:
        found = sorted(set(re.findall(pat, src)))
        if found:
            log.info("   identity hint %s: %s", pat, found[:5])
    dump_screen(driver, SCREEN_DUMP)
    log.info("✅ Profile opened. Layout dumped to %s", SCREEN_DUMP)


def probe_open(driver, args) -> None:
    """Step 2 — open a profile by slug, read fields, resolve slug back."""
    from linkedin_appium.actions.profile import open_profile, read_profile, resolve_public_identifier

    slug = args[0] if args else "michaelleclercq"
    open_profile(driver, slug)
    prof = read_profile(driver)
    log.info("→ name     : %s", prof.name)
    log.info("→ headline : %s", prof.headline)
    log.info("→ location : %s", prof.location)
    resolved = resolve_public_identifier(driver)
    ok = resolved == slug
    log.info("→ slug     : opened=%r resolved=%r %s", slug, resolved, "✓" if ok else "✗ MISMATCH")
    log.info("✅ Profile primitive round-trip complete.")


def probe_status(driver, args) -> None:
    """Step 3 — open a profile by slug and classify connection status."""
    from linkedin_appium.actions.profile import open_profile
    from linkedin_appium.actions.status import read_status

    slug = args[0] if args else "michaelleclercq"
    open_profile(driver, slug)
    status = read_status(driver)
    log.info("✅ %s → %s", slug, status.value)


def probe_connect(driver, args) -> None:
    """Step 4 — send a REAL invite to the top invitable search hit; verify PENDING."""
    from linkedin_appium.actions.search import search_people
    from linkedin_appium.actions.profile import resolve_public_identifier
    from linkedin_appium.actions.status import read_status
    from linkedin_appium.actions.connect import send_connection_request
    from linkedin_appium.enums import ConnectionStatus

    query = args[0] if args else "growth marketing"
    hits = search_people(driver, query)
    target = next((h for h in hits if h.can_invite), None)
    if target is None:
        log.warning("No invitable hit found."); return
    log.info("→ Target: %s (%s)", target.name, target.headline)

    row = find_first(driver, [
        f'new UiSelector().descriptionContains("{target.name}")',
        f'new UiSelector().textContains("{target.name}")',
    ])
    row.click()
    time.sleep(3.0)

    before = read_status(driver)
    log.info("→ status before: %s", before.value)
    if before != ConnectionStatus.NOT_CONNECTED:
        log.warning("Target isn't NOT_CONNECTED (%s) — aborting send.", before.value)
        return

    after = send_connection_request(driver)
    log.info("→ status after : %s", after.value)
    ok = after == ConnectionStatus.PENDING
    log.info("%s Connect %s (you can withdraw the invite from the profile).",
             "✅" if ok else "⚠️", "sent, now PENDING" if ok else f"result={after.value}")


def probe_whoami(driver, args) -> None:
    """Step 7 — discover the authenticated user's own profile."""
    from linkedin_appium.actions.self_profile import discover_self_profile

    prof = discover_self_profile(driver)
    log.info("→ name     : %s", prof.name)
    log.info("→ headline : %s", prof.headline)
    log.info("→ slug     : %s", prof.public_identifier)
    log.info("✅ whoami complete.")


PROBES = {
    "hello": probe_hello,
    "search": probe_search,
    "profile": probe_profile,
    "open": probe_open,
    "status": probe_status,
    "connect": probe_connect,
    "whoami": probe_whoami,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in PROBES:
        log.error("usage: probe <%s> [args...]", "|".join(PROBES))
        return 2

    name = sys.argv[1]
    args = sys.argv[2:]
    driver = new_driver()
    try:
        PROBES[name](driver, args)
        return 0
    except Exception as exc:  # noqa: BLE001 — probe: surface + dump for debugging
        log.error("❌ probe %r failed: %s", name, exc)
        try:
            dump_screen(driver, SCREEN_DUMP)
            log.error("   Saved screen layout to %s for selector debugging.", SCREEN_DUMP)
        except Exception:
            pass
        return 1
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
