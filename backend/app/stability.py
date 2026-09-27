"""Turns a noisy per-frame SceneState stream into discrete, committed "stable" states.

A new stable state is emitted only when:
  * every required object is visible (a hand covering an object → "occluded", not a change),
  * the frame is not moving (hands at work → "moving"),
  * the discrete arrangement has stayed identical for `stable_ms` and `min_frames`,
  * it differs from the last emitted stable state (no duplicates).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .config import StabilityConfig
from .models import SceneState, arrangement_key

TrackerStatus = Literal["stable", "settling", "moving", "occluded", "empty", "untracked"]


@dataclass
class TrackerResult:
    status: TrackerStatus
    motion: float
    stable_for_ms: float = 0.0
    missing: list[str] = field(default_factory=list)
    new_stable: SceneState | None = None  # set exactly once per newly committed arrangement

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "motion": round(self.motion, 2),
            "stableForMs": round(self.stable_for_ms),
            "missing": self.missing,
        }


class StabilityTracker:
    def __init__(self, cfg: StabilityConfig) -> None:
        self.cfg = cfg
        self.required: set[str] | None = None
        self._last_emitted: tuple | None = None
        self._clear_candidate()

    def _clear_candidate(self) -> None:
        self._candidate: tuple | None = None
        self._since = 0.0
        self._frames = 0

    def reset(self, required: set[str] | None = None) -> None:
        """Start fresh; the next stable arrangement is emitted even if it equals the previous one."""
        self.required = set(required) if required else None
        self._last_emitted = None
        self._clear_candidate()

    def interrupt(self) -> None:
        """Abandon the current settling window (camera moved / tracking lost) but keep what was committed."""
        self._clear_candidate()

    def update(self, scene: SceneState, motion: float, now: float) -> TrackerResult:
        visible = scene.visible_ids()
        if self.required:
            missing = sorted(self.required - visible)
            if missing:
                self._clear_candidate()
                return TrackerResult("occluded", motion, missing=missing)
        if not visible:
            self._clear_candidate()
            return TrackerResult("empty", motion)
        if self.cfg.motion_gate_enabled and motion > self.cfg.motion_threshold:
            self._clear_candidate()
            return TrackerResult("moving", motion)

        key = arrangement_key(scene.arrangement(self.required))
        if key != self._candidate:
            self._candidate, self._since, self._frames = key, now, 1
        else:
            self._frames += 1

        stable_for = (now - self._since) * 1000.0
        if stable_for < self.cfg.stable_ms or self._frames < self.cfg.min_frames:
            return TrackerResult("settling", motion, stable_for_ms=stable_for)

        new_state = None
        if key != self._last_emitted:
            self._last_emitted = key
            new_state = scene.model_copy(update={"stable_since": self._since})
        return TrackerResult("stable", motion, stable_for_ms=stable_for, new_stable=new_state)
