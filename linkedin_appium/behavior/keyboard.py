# linkedin_appium/behavior/keyboard.py
"""Learn the keyboard's key positions from a capture of you typing the alphabet.

Android's system keyboard is invisible to Appium, so keys can't be found — only
tapped by coordinate — and the layout is unknown (AZERTY vs QWERTY). Rather than
have a bot sweep-and-probe the keyboard (fragile: stale elements, autocorrect),
*you* type a known sequence once while `getevent` records your taps. Each
keyboard-zone tap, in order, is mapped to the character you typed. We read only
tap *positions*, so autocorrect changing the text on screen doesn't matter.

Coordinates are stored normalised to [0, 1] (like all captured features), so the
map is independent of screen resolution; the humanizer scales them to pixels.

    # 1. On the phone: open LinkedIn, tap Search, tap the text box so the
    #    keyboard is up. Then run this and follow the printed instructions:
    python -m linkedin_appium.behavior.keyboard calibrate --device /dev/input/event2
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from linkedin_appium.behavior.features import extract_session
from linkedin_appium.behavior.record import record_session
from linkedin_appium.behavior.context import Context

# Keys sit below this normalised Y; the suggestion strip above it is excluded so
# an accidental suggestion tap can't shift the whole mapping by one.
_KEY_ZONE_Y = 0.68
# What you type during calibration, in this exact order; the final tap is the
# backspace key (needed for the typo-correction behavior).
_PRINTABLE = list("abcdefghijklmnopqrstuvwxyz") + [" ", ",", "."]
_SEQUENCE_HELP = ("a b c d e f g h i j k l m n o p q r s t u v w x y z  "
                  "[SPACE]  ,  .  [BACKSPACE]")


@dataclass
class KeyMap:
    keys: dict[str, tuple[float, float]]     # char -> (x, y) normalised [0,1], incl. " "
    backspace: tuple[float, float] | None    # normalised
    screen: tuple[int, int] | None           # recording device touch range, for reference

    def get(self, ch: str) -> tuple[float, float] | None:
        return self.keys.get(ch) or self.keys.get(ch.lower())

    def neighbor(self, ch: str) -> tuple[float, float] | None:
        """Nearest other letter key — a realistic fat-finger mis-tap target."""
        p = self.get(ch)
        if p is None:
            return None
        best, best_d = None, float("inf")
        for c, q in self.keys.items():
            if q == p or not c.isalpha():
                continue
            d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
            if d < best_d:
                best, best_d = q, d
        return best

    def to_dict(self) -> dict:
        return {"keys": {k: list(v) for k, v in self.keys.items()},
                "backspace": list(self.backspace) if self.backspace else None,
                "screen": list(self.screen) if self.screen else None}

    @classmethod
    def from_dict(cls, d: dict) -> "KeyMap":
        return cls(keys={k: tuple(v) for k, v in d["keys"].items()},
                   backspace=tuple(d["backspace"]) if d.get("backspace") else None,
                   screen=tuple(d["screen"]) if d.get("screen") else None)

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "KeyMap":
        return cls.from_dict(json.loads(Path(path).read_text()))


def keymap_from_capture(raw_path: Path) -> KeyMap:
    """Map an alphabet-typing capture into a :class:`KeyMap`.

    Takes the keyboard-zone taps in the order they happened and zips them with
    the known typed sequence (letters, space, comma, period, then backspace).
    """
    session = extract_session(raw_path)
    meta = json.loads(Path(raw_path).with_suffix(".meta.json").read_text())
    kb_taps = [t for t in session.taps if t.start[1] > _KEY_ZONE_Y]

    expected = len(_PRINTABLE) + 1  # + backspace
    if len(kb_taps) != expected:
        print(f"WARNING: expected {expected} key taps, saw {len(kb_taps)}. "
              f"Mapping what aligns — re-record if letters look wrong.\n"
              f"  (type cleanly, one key at a time, don't tap the suggestion bar)")

    keys: dict[str, tuple[float, float]] = {}
    for i, ch in enumerate(_PRINTABLE):
        if i < len(kb_taps):
            keys[ch] = kb_taps[i].start
    backspace = kb_taps[len(_PRINTABLE)].start if len(kb_taps) > len(_PRINTABLE) else None

    km = KeyMap(keys=keys, backspace=backspace,
                screen=(meta.get("max_x"), meta.get("max_y")))
    letters = "".join(sorted(c for c in keys if c.isalpha()))
    missing = set("abcdefghijklmnopqrstuvwxyz") - set(letters)
    print(f"mapped {len(keys)} keys ({letters or 'none'}), "
          f"space={' ' in keys}, backspace={backspace is not None}")
    if missing:
        print(f"WARNING: missing {sorted(missing)} — re-record the alphabet")
    return km


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cal = sub.add_parser("calibrate", help="learn the keyboard by typing the alphabet")
    cal.add_argument("--device", help="touchscreen device, e.g. /dev/input/event2")
    cal.add_argument("--capture", help="map an existing alphabet capture instead of recording")
    cal.add_argument("--out", default="data/behavior_profiles/default.keymap.json")
    cal.add_argument("--serial", default=None)
    args = parser.parse_args(argv)

    if args.capture:
        raw = Path(args.capture)
    else:
        if not args.device:
            parser.error("pass --device (from `record devices`) or --capture <file>")
        print("\n=== Keyboard calibration ===")
        print("On the phone: open LinkedIn, tap Search, tap the text box so the")
        print("keyboard is showing. Then type this EXACT sequence, one key at a time,")
        print("WITHOUT tapping any suggestion above the keyboard:\n")
        print("   " + _SEQUENCE_HELP + "\n")
        print("Press Ctrl-C here when you've finished the backspace.\n")
        raw = record_session(Context.SEARCH, args.device,
                             Path("data/captures/_calib"), args.serial)

    km = keymap_from_capture(raw)
    km.save(Path(args.out))
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
