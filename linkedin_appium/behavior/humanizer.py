# linkedin_appium/behavior/humanizer.py
"""Drive Appium actions with values sampled from a BehaviorProfile.

This is the governor that sits between the adapter's intent ("tap invite",
"scroll the profile") and Appium's primitives. Instead of the hardcoded
``time.sleep(2.0)`` and instantaneous ``element.click()`` the actions use today,
every dwell, tap-press and fling is drawn — fresh, per call — from the recorded
operator's distribution for the current :class:`Context`. No sequence is ever
replayed; the live automation just carries the same statistical signature.

Taps and flings go through the W3C pointer API (not ``input swipe``) so a fling
reproduces a real velocity profile: slow-fast-slow easing with the peak speed in
the middle, matched to the sampled duration and distance. A tap lands with a
little positional jitter inside the target and a sampled finger-down dwell.
"""
from __future__ import annotations

import logging
import math
import random
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.actions import interaction
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput

from linkedin_appium.behavior.context import Context
from linkedin_appium.behavior.profile import BehaviorProfile

logger = logging.getLogger(__name__)

_WORD_BOUNDARY = set(" \t\n.,!?;:")
_FLING_STEPS = 14          # intermediate points per fling — enough for smooth easing
_TYPO_PROB = 1 / 50        # per-key chance of a fat-finger slip that gets corrected
# A real flick's finger-contact is short (the operator's median is ~0.24s);
# beyond this it reads as a slow drag with no inertial scroll. We keep the
# sampled distance but cap contact time so flicks stay quick and the release
# stays fast enough to trigger Android's fling.
_MAX_FLING_CONTACT = 0.30


class Humanizer:
    """Wraps a driver + profile, bound to one :class:`Context` at a time."""

    def __init__(self, driver, profile: BehaviorProfile, context: Context | str,
                 keymap=None):
        self.driver = driver
        self.profile = profile
        self.context = Context.coerce(context)
        self.keymap = keymap           # KeyMap enables real key-tapping in type()
        size = driver.get_window_size()
        self._w, self._h = size["width"], size["height"]

    def for_context(self, context: Context | str) -> "Humanizer":
        """A sibling humanizer on the same driver bound to another context."""
        return Humanizer(self.driver, self.profile, context, self.keymap)

    # -- waiting --------------------------------------------------------
    def dwell(self) -> None:
        """Pause for a sampled reading/thinking beat in this context."""
        time.sleep(self.profile.sample_dwell(self.context))

    # -- tapping --------------------------------------------------------
    def _pointer_tap(self, x: float, y: float, press: float) -> None:
        finger = PointerInput(interaction.POINTER_TOUCH, "finger")
        a = ActionBuilder(self.driver, mouse=finger)
        a.pointer_action.move_to_location(int(x), int(y))
        a.pointer_action.pointer_down()
        a.pointer_action.pause(press)
        a.pointer_action.pointer_up()
        a.perform()

    def tap(self, element) -> None:
        """Tap *element* with a sampled press dwell and sub-target jitter."""
        press = self.profile.sample_tap_press(self.context)
        try:
            r = element.rect
            cx = r["x"] + r["width"] / 2 + random.uniform(-0.2, 0.2) * r["width"]
            cy = r["y"] + r["height"] / 2 + random.uniform(-0.2, 0.2) * r["height"]
            self._pointer_tap(cx, cy, press)
        except WebDriverException:
            element.click()   # fall back if the pointer gesture is refused

    def tap_xy(self, x: float, y: float) -> None:
        """Tap a raw screen coordinate with a sampled press dwell + small jitter."""
        press = self.profile.sample_tap_press(self.context)
        self._pointer_tap(x + random.uniform(-6, 6), y + random.uniform(-6, 6), press)

    def tap_selectors(self, selectors, timeout: float = 4.0) -> bool:
        """Find the first matching selector and humanized-tap it.

        Same fast-poll contract as the actions' ``_try_click`` — returns True on
        a tap, False if nothing matched within the budget.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            for sel in selectors:
                try:
                    el = self.driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
                    self.tap(el)
                    return True
                except WebDriverException:
                    continue
            time.sleep(0.3)
        return False

    # -- typing ---------------------------------------------------------
    def type(self, element, text: str) -> None:
        """Type *text* at the operator's cadence for this context.

        With a calibrated :class:`KeyMap`, taps each letter's real key position —
        actual finger taps on the keyboard, with a ~1/50 fat-finger slip (tap a
        neighbour key, backspace, tap the right one). Without one, falls back to
        progressive text injection (growing prefix, since UiAutomator2's send_keys
        replaces rather than appends).
        """
        delay = lambda b=False: self.profile.sample_key_delay(self.context, after_boundary=b)
        time.sleep(delay(True))

        if self.keymap is None:
            for i in range(1, len(text) + 1):
                element.send_keys(text[:i])
                time.sleep(delay(text[i - 1] in _WORD_BOUNDARY))
            return

        # KeyMap coords are normalised [0,1]; scale to this screen's pixels.
        px = lambda p: (p[0] * self._w, p[1] * self._h)
        prev_boundary = False
        for ch in text:
            pos = self.keymap.get(ch)
            if pos is None:
                continue   # unmapped glyph; the caller's read-back + retype catches it
            if random.random() < _TYPO_PROB and self.keymap.backspace is not None:
                nb = self.keymap.neighbor(ch)
                if nb is not None:
                    self.tap_xy(*px(nb))
                    time.sleep(delay())
                    self.tap_xy(*px(self.keymap.backspace))
                    time.sleep(delay())
            self.tap_xy(*px(pos))
            time.sleep(delay(prev_boundary))
            prev_boundary = ch in _WORD_BOUNDARY

    # -- scrolling ------------------------------------------------------
    def scroll(self, n: int = 1, direction: str | None = None) -> None:
        """Perform *n* sampled flings, dwelling between them.

        The sampled gesture dynamics (duration, distance, velocity) always come
        from the profile; *direction* can be pinned (e.g. ``"down"`` for reading a
        profile top-to-bottom) so the sampled — possibly up-biased — direction
        doesn't send the view the wrong way.
        """
        for i in range(n):
            plan = self.profile.sample_scroll(self.context)
            if direction is not None:
                plan.direction = direction
            self._fling(plan)
            if i < n - 1:
                self.dwell()

    def _fling(self, plan) -> None:
        """Render a ScrollPlan as an eased W3C pointer drag."""
        margin = 0.12
        contact = min(plan.duration, _MAX_FLING_CONTACT)
        if plan.direction in ("down", "up"):
            span = min(plan.distance, 1 - 2 * margin) * self._h
            x = self._w * 0.5 + random.uniform(-0.05, 0.05) * self._w
            if plan.direction == "down":          # content down => finger up
                y0, y1 = self._h * (1 - margin), self._h * (1 - margin) - span
            else:
                y0, y1 = self._h * margin, self._h * margin + span
            self._pointer_drag(x, y0, x, y1, contact)
        else:
            span = min(plan.distance, 1 - 2 * margin) * self._w
            y = self._h * 0.5
            if plan.direction == "left":
                x0, x1 = self._w * (1 - margin), self._w * (1 - margin) - span
            else:
                x0, x1 = self._w * margin, self._w * margin + span
            self._pointer_drag(x0, y, x1, y, contact)

    def _pointer_drag(self, x0, y0, x1, y1, duration) -> None:
        finger = PointerInput(interaction.POINTER_TOUCH, "finger")
        a = ActionBuilder(self.driver, mouse=finger)
        a.pointer_action.move_to_location(int(x0), int(y0))
        a.pointer_action.pointer_down()
        # Ease-IN (position ~ frac^2): the finger accelerates and is moving
        # FASTEST at the instant it lifts. Android only starts an inertial fling
        # when the release velocity clears its threshold, so this is what makes a
        # swipe carry momentum and cover real distance instead of dragging slowly
        # to a dead stop (a raised-cosine curve decelerates to zero at release and
        # kills the fling — the "scroll felt too slow" bug).
        dt = max(duration, 0.05) / _FLING_STEPS
        for i in range(1, _FLING_STEPS + 1):
            frac = (i / _FLING_STEPS) ** 2
            a.pointer_action.pause(dt)
            a.pointer_action.move_to_location(int(x0 + (x1 - x0) * frac),
                                              int(y0 + (y1 - y0) * frac))
        a.pointer_action.pointer_up()
        a.perform()
