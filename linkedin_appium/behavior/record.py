# linkedin_appium/behavior/record.py
"""Capture real human touch input on Android over adb — no root, no Appium.

While recording, *your finger* is the input: Appium is not attached (it can't
observe gestures it didn't issue). We instead read the kernel's raw touchscreen
event stream with ``adb shell getevent -lt <device>``, which carries every
sample with a microsecond timestamp — enough to recover scroll velocity, fling
distance, tap dwell and inter-gesture reading pauses faithfully.

Each recording is tagged with the :class:`Context` you were acting in, so the
fitter can keep search gestures apart from message-reply gestures. One context
per capture file keeps labelling trivial; run the recorder once per activity.

Usage (on a machine with the phone attached via adb)::

    # 1. find the touchscreen device + its coordinate range
    python -m linkedin_appium.behavior.record devices

    # 2. record a session — act naturally in the LinkedIn app, Ctrl-C to stop
    python -m linkedin_appium.behavior.record capture \\
        --context search --device /dev/input/event3

Writes ``<out>/<context>-<timestamp>.getevent`` (raw stream) and a
``.meta.json`` sidecar holding the context and the device's X/Y max, so
coordinates can be normalised to [0,1] and compared across phones.

Non-rooted note: reading events via ``getevent`` works over adb on most stock
devices. If ``devices`` shows no ``ABS_MT_POSITION_X`` under the touchscreen,
your vendor has restricted it and you'll need the Appium-instrumented fallback.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from linkedin_appium.behavior.context import Context

# A finger-up on the multitouch protocol: ABS_MT_TRACKING_ID set to -1.
_TRACKING_UP = "ffffffff"
# getevent -lp line: "    ABS_MT_POSITION_X   : value 0, min 0, max 1080, ..."
_RANGE_RE = re.compile(r"(ABS_MT_POSITION_[XY]).*?max\s+(\d+)")


def _adb(args: list[str], serial: str | None = None) -> list[str]:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    return cmd + args


@dataclass
class TouchDevice:
    path: str          # e.g. /dev/input/event3
    name: str
    max_x: int
    max_y: int


def touch_devices(serial: str | None = None) -> list[TouchDevice]:
    """Enumerate input devices that expose absolute multitouch coordinates.

    Parses ``getevent -lp`` (a one-shot capability dump). A device with both
    ``ABS_MT_POSITION_X`` and ``_Y`` ranges is a touchscreen; its ``max`` values
    are the coordinate space we normalise against.
    """
    out = subprocess.run(
        _adb(["shell", "getevent", "-lp"], serial),
        capture_output=True, text=True, check=True,
    ).stdout
    devices: list[TouchDevice] = []
    path = name = ""
    ranges: dict[str, int] = {}
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("add device"):
            # Flush the previous block if it was a touchscreen.
            if "ABS_MT_POSITION_X" in ranges and "ABS_MT_POSITION_Y" in ranges:
                devices.append(TouchDevice(path, name,
                                           ranges["ABS_MT_POSITION_X"],
                                           ranges["ABS_MT_POSITION_Y"]))
            path = stripped.split(":", 1)[-1].strip()
            name, ranges = "", {}
        elif stripped.startswith("name:"):
            name = stripped.split(":", 1)[-1].strip().strip('"')
        else:
            m = _RANGE_RE.search(stripped)
            if m:
                ranges[m.group(1)] = int(m.group(2))
    if "ABS_MT_POSITION_X" in ranges and "ABS_MT_POSITION_Y" in ranges:
        devices.append(TouchDevice(path, name,
                                   ranges["ABS_MT_POSITION_X"],
                                   ranges["ABS_MT_POSITION_Y"]))
    return devices


def record_session(context: Context | str, device: str, out_dir: Path,
                   serial: str | None = None) -> Path:
    """Stream raw touch events to disk until interrupted; return the raw path.

    The companion ``.meta.json`` records the context and device geometry so the
    feature extractor can normalise coordinates without the phone present.
    """
    context = Context.coerce(context)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    geometry = next((d for d in touch_devices(serial) if d.path == device), None)
    if geometry is None:
        raise SystemExit(
            f"{device} is not a touchscreen with absolute coordinates.\n"
            f"Run `python -m linkedin_appium.behavior.record devices` to list candidates."
        )

    stamp = time.strftime("%Y%m%d-%H%M%S")
    raw_path = out_dir / f"{context.value}-{stamp}.getevent"
    meta_path = raw_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps({
        "context": context.value,
        "device": device,
        "device_name": geometry.name,
        "max_x": geometry.max_x,
        "max_y": geometry.max_y,
        "started_at": stamp,
    }, indent=2))

    print(f"Recording {context.value!r} from {device} "
          f"({geometry.name}, {geometry.max_x}x{geometry.max_y}).")
    print("Act naturally in the LinkedIn app. Press Ctrl-C to stop.\n")

    proc = subprocess.Popen(
        _adb(["shell", "getevent", "-lt", device], serial),
        stdout=subprocess.PIPE, text=True, bufsize=1,
    )
    lines = 0
    try:
        with raw_path.open("w") as fh:
            assert proc.stdout is not None
            for line in proc.stdout:
                fh.write(line)
                lines += 1
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
    print(f"\nSaved {lines} events -> {raw_path}")
    return raw_path


def parse_raw(raw_path: Path) -> list[list[tuple[float, int, int]]]:
    """Reconstruct gestures from a ``getevent -lt`` capture.

    Returns a list of gestures, each a list of ``(timestamp, x, y)`` samples in
    *device pixels* (normalisation happens in features.py, which also reads the
    meta sidecar). A gesture spans finger-down to finger-up: a sample is
    committed on each ``SYN_REPORT``, and the gesture closes when
    ``ABS_MT_TRACKING_ID`` goes to -1 (or ``BTN_TOUCH`` reports up).
    """
    gestures: list[list[tuple[float, int, int]]] = []
    cur: list[tuple[float, int, int]] = []
    x = y = None
    for line in Path(raw_path).read_text().splitlines():
        # "[   12345.678901] EV_ABS  ABS_MT_POSITION_X  000004d2"
        body = line.replace("[", " ").replace("]", " ").split()
        if len(body) < 4:
            continue
        try:
            t = float(body[0])
        except ValueError:
            continue
        code, val = body[2], body[3]
        if code == "ABS_MT_POSITION_X":
            x = int(val, 16)
        elif code == "ABS_MT_POSITION_Y":
            y = int(val, 16)
        elif code == "ABS_MT_TRACKING_ID":
            if val == _TRACKING_UP and cur:
                gestures.append(cur)
                cur = []
                # Clear coords so the trailing finger-up SYN_REPORT (which still
                # carries the last position) can't spawn a phantom sample that
                # merges into the next gesture and swallows the inter-gesture gap.
                x = y = None
        elif code == "BTN_TOUCH" and val in ("00000000", "UP") and cur:
            gestures.append(cur)
            cur = []
            x = y = None
        elif code == "SYN_REPORT" and x is not None and y is not None:
            cur.append((t, x, y))
    if cur:
        gestures.append(cur)
    return gestures


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    dev = sub.add_parser("devices", help="list touchscreen input devices")
    dev.add_argument("--serial", default=None, help="adb device serial (if multiple)")
    cap = sub.add_parser("capture", help="record a session for one context")
    cap.add_argument("--context", required=True,
                     choices=[c.value for c in Context])
    cap.add_argument("--device", required=True, help="e.g. /dev/input/event3")
    cap.add_argument("--out", default="data/captures")
    cap.add_argument("--serial", default=None, help="adb device serial (if multiple)")
    args = parser.parse_args(argv)

    if args.cmd == "devices":
        found = touch_devices(args.serial)
        if not found:
            print("No touchscreen with absolute coordinates found — getevent may "
                  "be restricted on this device.", file=sys.stderr)
            return 1
        for d in found:
            print(f"{d.path}\t{d.name}\t{d.max_x}x{d.max_y}")
        return 0

    record_session(args.context, args.device, Path(args.out), args.serial)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
