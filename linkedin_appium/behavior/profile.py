# linkedin_appium/behavior/profile.py
"""The learned, context-keyed governor the live adapter samples from.

A :class:`BehaviorProfile` holds one :class:`ContextModel` per :class:`Context`
plus a global fallback. Each model is a bundle of fitted distributions
(log-normal — the natural shape for human timings: positive, right-skewed) for
dwell, typing cadence, tap dwell and scroll dynamics. At run time the adapter
calls ``sample_*`` and gets a *fresh* draw with its tails clamped, so no gap is
unnaturally long (the "no blank spaces mid-word" requirement) and no two runs
repeat a recorded sequence.

The profile persists as plain JSON (fitted parameters, never raw captures), so
it can live beside the campaign data and be regenerated whenever you record more
sessions. Fit with :meth:`BehaviorProfile.fit`; load/sample in the adapter.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from statistics import median as _median

import numpy as np
from scipy import stats

from linkedin_appium.behavior.context import Context
from linkedin_appium.behavior.features import SessionFeatures

# A distribution needs at least this many observations to fit; below it we keep
# the mean as a constant (jittered a touch) rather than trust a shape estimate.
_MIN_FIT = 8
# Clamp every sample to this inner percentile band so the heavy log-normal tail
# never emits an absurd pause (or a zero-length flick).
_CLAMP_LO, _CLAMP_HI = 2.0, 98.0


@dataclass
class LogNorm:
    """A fitted (or constant-fallback) positive timing distribution, seconds."""
    sigma: float          # shape of the underlying normal
    scale: float          # exp(mu); the geometric mean
    lo: float             # clamp floor
    hi: float             # clamp ceiling
    n: int                # samples it was fit from (0 = pure default)

    @classmethod
    def fit(cls, data: list[float], default: float) -> "LogNorm":
        arr = np.asarray([d for d in data if d > 0], dtype=float)
        if arr.size < _MIN_FIT:
            # Not enough signal: a tight band around the mean (or the default).
            mean = float(arr.mean()) if arr.size else default
            return cls(sigma=0.25, scale=mean, lo=mean * 0.5, hi=mean * 2.0,
                       n=int(arr.size))
        sigma, _loc, scale = stats.lognorm.fit(arr, floc=0.0)
        return cls(sigma=float(sigma), scale=float(scale),
                   lo=float(np.percentile(arr, _CLAMP_LO)),
                   hi=float(np.percentile(arr, _CLAMP_HI)),
                   n=int(arr.size))

    def sample(self, rng: np.random.Generator) -> float:
        draw = rng.lognormal(mean=np.log(self.scale), sigma=self.sigma)
        return float(np.clip(draw, self.lo, self.hi))

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "LogNorm":
        return cls(**d)


@dataclass
class ScrollPlan:
    """A sampled fling the humanizer renders into Appium pointer moves."""
    duration: float          # seconds, finger-down to finger-up
    distance: float          # normalised path length (screen-fractions)
    peak_velocity: float     # screen-fractions/sec
    direction: str           # "down" | "up" | "left" | "right"


# Per-context defaults (seconds / fractions), used until real data exists. Seeded
# from the adapter's old hardcoded HUMAN_TYPE_* pacing and sane reading times.
_DEFAULTS = {
    "key_interval": 0.09,
    "key_boundary_interval": 0.45,
    "tap_press": 0.08,
    "dwell": 1.2,
    "scroll_duration": 0.30,
    "scroll_distance": 0.45,
    "scroll_peak_velocity": 2.5,
}


@dataclass
class ContextModel:
    key_interval: LogNorm
    key_boundary_interval: LogNorm
    tap_press: LogNorm
    dwell: LogNorm
    scroll_duration: LogNorm
    scroll_distance: LogNorm
    scroll_peak_velocity: LogNorm
    down_fraction: float = 0.85   # P(a scroll moves the feed downward)

    @classmethod
    def fit(cls, sessions: list[SessionFeatures]) -> "ContextModel":
        gestures = [g for s in sessions for g in s.gestures]
        taps = [g for g in gestures if g.kind == "tap"]
        scrolls = [g for g in gestures if g.kind == "scroll"]
        dwells = [d for s in sessions for d in s.dwells]

        # Prefer true keystrokes (IME log, with boundary flags) when present;
        # otherwise fall back to cadence recovered from keyboard-zone taps, which
        # carries the operator's real rhythm but no boundary labels.
        ime = [k for s in sessions for k in s.keystrokes]
        if ime:
            key_norm = [k.interval for k in ime if not k.after_boundary]
            key_bound = [k.interval for k in ime if k.after_boundary]
        else:
            # Tap-derived cadence has no boundary labels, so split by speed:
            # quick keystrokes (the burst) vs the longer gaps (natural pauses).
            # Normal keys then sample from the fast cluster — the operator's real
            # typing pace — instead of the skew-inflated overall mean, and the
            # pauses land only at word boundaries.
            tap_keys = [iv for s in sessions for iv in s.key_intervals]
            if tap_keys:
                cut = 1.8 * _median(tap_keys)
                fast = [k for k in tap_keys if k <= cut]
                slow = [k for k in tap_keys if k > cut]
                key_norm = fast or tap_keys
                key_bound = slow or fast or tap_keys
            else:
                key_norm = key_bound = []
        down = sum(1 for g in scrolls if g.direction == "down")

        d = _DEFAULTS
        return cls(
            key_interval=LogNorm.fit(key_norm, d["key_interval"]),
            key_boundary_interval=LogNorm.fit(key_bound, d["key_boundary_interval"]),
            tap_press=LogNorm.fit([g.duration for g in taps], d["tap_press"]),
            dwell=LogNorm.fit(dwells, d["dwell"]),
            scroll_duration=LogNorm.fit([g.duration for g in scrolls], d["scroll_duration"]),
            scroll_distance=LogNorm.fit([g.distance for g in scrolls], d["scroll_distance"]),
            scroll_peak_velocity=LogNorm.fit([g.peak_velocity for g in scrolls],
                                             d["scroll_peak_velocity"]),
            down_fraction=(down / len(scrolls)) if scrolls else 0.85,
        )

    @classmethod
    def default(cls) -> "ContextModel":
        d = _DEFAULTS
        mk = lambda v: LogNorm(0.3, v, v * 0.5, v * 2.0, 0)
        return cls(mk(d["key_interval"]), mk(d["key_boundary_interval"]),
                   mk(d["tap_press"]), mk(d["dwell"]), mk(d["scroll_duration"]),
                   mk(d["scroll_distance"]), mk(d["scroll_peak_velocity"]))

    def to_dict(self) -> dict:
        out = {k: v.to_dict() for k, v in self.__dict__.items() if isinstance(v, LogNorm)}
        out["down_fraction"] = self.down_fraction
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "ContextModel":
        kw = {k: LogNorm.from_dict(v) for k, v in d.items() if k != "down_fraction"}
        return cls(down_fraction=d.get("down_fraction", 0.85), **kw)


class BehaviorProfile:
    """Context-keyed set of models the adapter samples at run time."""

    def __init__(self, contexts: dict[Context, ContextModel],
                 fallback: ContextModel, seed: int | None = None):
        self._contexts = contexts
        self._fallback = fallback
        self._rng = np.random.default_rng(seed)

    # -- construction ---------------------------------------------------
    @classmethod
    def fit(cls, sessions: list[SessionFeatures], seed: int | None = None) -> "BehaviorProfile":
        by_ctx: dict[Context, list[SessionFeatures]] = {}
        for s in sessions:
            by_ctx.setdefault(s.context, []).append(s)
        contexts = {ctx: ContextModel.fit(group) for ctx, group in by_ctx.items()}
        fallback = ContextModel.fit(sessions) if sessions else ContextModel.default()
        return cls(contexts, fallback, seed)

    def model_for(self, context: Context | str) -> ContextModel:
        """The model for *context*, or the global fallback if none was recorded."""
        return self._contexts.get(Context.coerce(context), self._fallback)

    # -- sampling (what the humanizer calls) ----------------------------
    def sample_dwell(self, context: Context | str) -> float:
        """Idle/reading seconds to wait before the next action in *context*."""
        return self.model_for(context).dwell.sample(self._rng)

    def sample_key_delay(self, context: Context | str, *, after_boundary: bool = False) -> float:
        """Seconds to wait before the next keystroke (longer after a word boundary)."""
        m = self.model_for(context)
        dist = m.key_boundary_interval if after_boundary else m.key_interval
        return dist.sample(self._rng)

    def sample_tap_press(self, context: Context | str) -> float:
        """Finger-down dwell of a tap, seconds."""
        return self.model_for(context).tap_press.sample(self._rng)

    def sample_scroll(self, context: Context | str) -> ScrollPlan:
        """A fling to render: duration, travel, peak velocity, direction."""
        m = self.model_for(context)
        direction = "down" if self._rng.random() < m.down_fraction else "up"
        return ScrollPlan(
            duration=m.scroll_duration.sample(self._rng),
            distance=m.scroll_distance.sample(self._rng),
            peak_velocity=m.scroll_peak_velocity.sample(self._rng),
            direction=direction,
        )

    # -- persistence ----------------------------------------------------
    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "contexts": {c.value: m.to_dict() for c, m in self._contexts.items()},
            "fallback": self._fallback.to_dict(),
        }
        path.write_text(json.dumps(payload, indent=2))

    @classmethod
    def load(cls, path: Path, seed: int | None = None) -> "BehaviorProfile":
        payload = json.loads(Path(path).read_text())
        contexts = {Context(k): ContextModel.from_dict(v)
                    for k, v in payload["contexts"].items()}
        fallback = ContextModel.from_dict(payload["fallback"])
        return cls(contexts, fallback, seed)

    def summary(self) -> str:
        """One line per context: sample counts behind the key timings."""
        rows = []
        for ctx, m in sorted(self._contexts.items(), key=lambda kv: kv[0].value):
            rows.append(f"{ctx.value:8s} keys={m.key_interval.n:4d} "
                        f"scrolls={m.scroll_duration.n:4d} dwells={m.dwell.n:4d}")
        return "\n".join(rows) or "(empty profile — using defaults)"
