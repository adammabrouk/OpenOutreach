# linkedin_appium/behavior/probe_keyboard.py
"""Dump the on-screen keyboard's key elements, so typing can tap real keys.

Real human typing is taps on individual keys, not an injected setText. To build
that the humanizer must locate each key on screen; this probe reveals how your
keyboard exposes its keys (content-desc vs text, and their screen bounds).

    # Appium server running, phone on adb, LinkedIn open:
    python -m linkedin_appium.behavior.probe_keyboard

It opens People search, focuses the field to raise the keyboard, and prints
every element in the bottom half of the screen (class / content-desc / text /
bounds). Paste the output back and the key map gets built from it.
"""
from __future__ import annotations

import time
from xml.etree import ElementTree as ET

from appium.webdriver.common.appiumby import AppiumBy  # noqa: F401 (parity w/ actions)

from linkedin_appium.driver import new_driver, ensure_feed, find_first


def _y_top(bounds: str) -> int | None:
    # bounds look like "[x1,y1][x2,y2]"
    try:
        return int(bounds.split("][")[0].split(",")[1])
    except (IndexError, ValueError):
        return None


def run() -> None:
    driver = new_driver()
    try:
        ensure_feed(driver)
        find_first(driver, [
            'new UiSelector().descriptionContains("Chercher")',
            'new UiSelector().textContains("Chercher")',
            'new UiSelector().descriptionContains("Search")',
            'new UiSelector().textContains("Search")',
        ]).click()
        time.sleep(1.0)
        field = find_first(driver, ['new UiSelector().className("android.widget.EditText")'])
        field.click()
        time.sleep(1.5)  # let the keyboard animate in

        size = driver.get_window_size()
        h = size["height"]
        print(f"# screen {size}")
        print("# bounds\tclass\tcontent-desc\ttext")
        root = ET.fromstring(driver.page_source)
        rows = 0
        for n in root.iter():
            b = n.get("bounds") or ""
            y = _y_top(b)
            if y is None or y < h * 0.5:
                continue
            desc, txt, cls = n.get("content-desc") or "", n.get("text") or "", n.get("class") or ""
            if desc or txt:
                print(f"{b}\t{cls}\tdesc={desc!r}\ttext={txt!r}")
                rows += 1
        print(f"# {rows} candidate key/element(s) in bottom half")
    finally:
        driver.quit()


if __name__ == "__main__":
    run()
