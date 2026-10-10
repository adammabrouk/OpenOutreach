# linkedin_appium/behavior/fit.py
"""Fit a BehaviorProfile from recorded captures.

    python -m linkedin_appium.behavior.fit \\
        --captures data/captures --out data/behavior_profile.json

Reads every ``*.getevent`` capture in the directory, groups them by the context
each was recorded under, fits per-context distributions, and writes the profile
JSON the adapter loads at run time. Re-run it whenever you record more sessions.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from linkedin_appium.behavior.features import extract_dir
from linkedin_appium.behavior.profile import BehaviorProfile


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--captures", default="data/captures")
    parser.add_argument("--out", default="data/behavior_profile.json")
    args = parser.parse_args(argv)

    sessions = extract_dir(Path(args.captures))
    if not sessions:
        print(f"No captures found in {args.captures} — record some first.")
        return 1
    profile = BehaviorProfile.fit(sessions)
    profile.save(Path(args.out))
    print(f"Fit {len(sessions)} session(s) -> {args.out}\n")
    print(profile.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
