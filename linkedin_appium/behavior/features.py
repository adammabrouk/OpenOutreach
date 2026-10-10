# linkedin_appium/behavior/features.py
"""Turn raw captures into device-normalised, typed motor features.

Coordinates are normalised to [0, 1] against the recording device's X/Y max
(from the ``.meta.json`` sidecar) so velocities and distances are expressed in
screen-fractions per second — comparable across phones and directly usable to
drive an Appium pointer on a differently-sized screen.

Three feature families, all later fitted *per context*:

    Gesture      taps (press dwell + landing jitter) and scrolls/flings
                 (distance, duration, peak velocity, direction)
    dwell gaps   the idle time between one gesture ending and the next
                 starting — "reading / thinking" time
    Keystroke    inter-key intervals + whether the key followed a word boundary
                 (space/punctuation), from an optional IME key log
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from linkedin_appium.behavior.context import Context
from linkedin_appium.behavior.record import parse_raw

# A gesture whose total travel stays under this fraction of the screen is a tap,
# not a scroll (finger jitter while pressing, not an intentional drag).
TAP_MAX_TRAVEL = 0.02
# The soft keyboard occupies roughly the bottom of the screen; taps landing
# below this normalised Y during a typing burst are keystrokes. We can't recover
# WHICH key (no key labels in a touch stream), but the time between consecutive
# keyboard taps is the operator's real typing cadence — usable without an IME.
KEYBOARD_ZONE_Y = 0.58
# Consecutive keyboard taps farther apart than this aren't one typing burst
# (the operator paused, switched fields, or hunted for a suggestion).
MAX_KEY_GAP = 2.0


@dataclass
class Gesture:
    kind: str                 # "tap" | "scroll"
    duration: float           # seconds, finger-down to finger-up
    distance: float           # normalised path length (sum of segment lengths)
    dx: float                 # normalised net horizontal displacement (end-start)
    dy: float                 # normalised net vertical displacement
    peak_velocity: float      # max instantaneous speed, screen-fractions/sec
    start: tuple[float, float]
    end: tuple[float, float]

    @property
    def direction(self) -> str:
        """Dominant scroll direction (``down`` = finger moves up the screen)."""
        if abs(self.dy) >= abs(self.dx):
            return "down" if self.dy < 0 else "up"
        return "left" if self.dx < 0 else "right"


@dataclass
class Keystroke:
    interval: float           # seconds since the previous keystroke
    after_boundary: bool      # previous char was a space or sentence punctuation


@dataclass
class SessionFeatures:
    context: Context
    gestures: list[Gesture] = field(default_factory=list)
    dwells: list[float] = field(default_factory=list)       # inter-gesture idle (s)
    keystrokes: list[Keystroke] = field(default_factory=list)  # from IME log, if any
    key_intervals: list[float] = field(default_factory=list)   # typing cadence from kbd taps

    @property
    def taps(self) -> list[Gesture]:
        return [g for g in self.gestures if g.kind == "tap"]

    @property
    def scrolls(self) -> list[Gesture]:
        return [g for g in self.gestures if g.kind == "scroll"]


def _gesture_from_samples(samples: list[tuple[float, int, int]],
                          max_x: int, max_y: int) -> Gesture | None:
    """Build one :class:`Gesture` from raw ``(t, x, y)`` pixel samples."""
    if len(samples) < 2:
        if not samples:
            return None
        # Single-sample touch: a tap with no measurable travel.
        t, x, y = samples[0]
        p = (x / max_x, y / max_y)
        return Gesture("tap", 0.0, 0.0, 0.0, 0.0, 0.0, p, p)

    pts = [(t, x / max_x, y / max_y) for (t, x, y) in samples]
    distance = 0.0
    peak_v = 0.0
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        distance += seg
        dt = t1 - t0
        if dt > 0:
            peak_v = max(peak_v, seg / dt)
    duration = pts[-1][0] - pts[0][0]
    start = (pts[0][1], pts[0][2])
    end = (pts[-1][1], pts[-1][2])
    kind = "tap" if distance < TAP_MAX_TRAVEL else "scroll"
    return Gesture(kind, duration, distance, end[0] - start[0], end[1] - start[1],
                   peak_v, start, end)


def _load_keystrokes(keylog: Path) -> list[Keystroke]:
    """Parse an optional IME key log (JSONL: ``{"t": <epoch>, "ch": "a"}``).

    The logging keyboard (``behavior/ime/``) emits one line per key. We keep
    only inter-key intervals and a word-boundary flag — never the typed text —
    because the profile learns *rhythm*, not content.
    """
    if not keylog.exists():
        return []
    strokes: list[Keystroke] = []
    prev_t: float | None = None
    prev_boundary = False
    for line in keylog.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        t, ch = float(rec["t"]), rec.get("ch", "")
        if prev_t is not None:
            strokes.append(Keystroke(interval=t - prev_t, after_boundary=prev_boundary))
        prev_t = t
        prev_boundary = ch in " \t\n.,!?;:"
    return strokes


def extract_session(raw_path: Path) -> SessionFeatures:
    """Load one capture (+ its meta and optional key log) into features."""
    raw_path = Path(raw_path)
    meta = json.loads(raw_path.with_suffix(".meta.json").read_text())
    context = Context(meta["context"])
    max_x, max_y = meta["max_x"], meta["max_y"]

    gestures: list[Gesture] = []
    ends: list[float] = []
    starts: list[float] = []
    for samples in parse_raw(raw_path):
        g = _gesture_from_samples(samples, max_x, max_y)
        if g is None:
            continue
        gestures.append(g)
        starts.append(samples[0][0])
        ends.append(samples[-1][0])

    dwells = [s - e for e, s in zip(ends, starts[1:]) if s - e > 0]

    # Typing cadence: gaps between consecutive keyboard-zone taps within one burst.
    key_intervals = []
    for i in range(len(gestures) - 1):
        g0, g1 = gestures[i], gestures[i + 1]
        if (g0.kind == "tap" and g1.kind == "tap"
                and g0.start[1] > KEYBOARD_ZONE_Y and g1.start[1] > KEYBOARD_ZONE_Y):
            gap = starts[i + 1] - starts[i]
            if 0 < gap < MAX_KEY_GAP:
                key_intervals.append(gap)

    keystrokes = _load_keystrokes(raw_path.with_suffix(".keys.jsonl"))
    return SessionFeatures(context, gestures, dwells, keystrokes, key_intervals)


def extract_dir(captures_dir: Path) -> list[SessionFeatures]:
    """Extract features for every ``*.getevent`` capture in a directory."""
    return [extract_session(p) for p in sorted(Path(captures_dir).glob("*.getevent"))]
