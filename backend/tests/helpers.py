"""Build synthetic SceneStates from a compact spec, e.g. layout(red="A", blue="B", green="on:blue")."""

from __future__ import annotations

from app.config import StabilityConfig
from app.engine import PracticeEngine, TeachRecorder
from app.models import SceneObject, SceneState
from app.stability import StabilityTracker

ZONE_X = {"A": 0.17, "B": 0.5, "C": 0.83, None: 0.5}


def layout(t: float = 0.0, **spec: str | None) -> SceneState:
    """spec values: a zone id ("A"/"B"/"C"), None for outside every zone, or "on:<id>" for stacked."""
    objs = []
    for i, (oid, where) in enumerate(spec.items()):
        stacked = where[3:] if isinstance(where, str) and where.startswith("on:") else None
        zone = None if stacked else where
        objs.append(SceneObject(
            id=oid, color=oid, center=(ZONE_X[zone], 0.2 + 0.15 * i), bbox=(0, 0, 0.05, 0.05),
            zone=zone, stacked_on=stacked, confidence=0.95,
        ))
    # Resolve stacked zones like the vision layer does.
    by_id = {o.id: o for o in objs}
    for o in objs:
        base = o
        while base.stacked_on:
            base = by_id[base.stacked_on]
        o.zone = base.zone
    return SceneState(objects=objs, captured_at=t)


def moved(state: SceneState, **changes: str | None) -> dict:
    spec = {o.id: (f"on:{o.stacked_on}" if o.stacked_on else o.zone) for o in state.objects}
    spec.update(changes)
    return spec


def teach(initial: dict, *changes: dict) -> TeachRecorder:
    """Teach from an initial spec and a sequence of per-step change dicts."""
    rec = TeachRecorder(steps_target=len(changes), min_objects=2)
    state = layout(**initial)
    rec.on_stable(state)
    for ch in changes:
        state = layout(**moved(state, **ch))
        rec.on_stable(state)
    return rec


def practice_through(engine: PracticeEngine, initial: dict, *changes: dict) -> list:
    """Feed the initial layout and then cumulative changes; returns all events."""
    events = []
    state = layout(**initial)
    events += engine.on_stable(state)
    for ch in changes:
        state = layout(**moved(state, **ch))
        events += engine.on_stable(state)
    return events


class Clock:
    """Feeds frames through a real StabilityTracker at a fixed frame rate."""

    def __init__(self, fps: float = 5.0, cfg: StabilityConfig | None = None) -> None:
        self.t = 1000.0
        self.dt = 1.0 / fps
        self.tracker = StabilityTracker(cfg or StabilityConfig())

    def hold(self, state_spec: dict, seconds: float, motion: float = 0.0) -> list[SceneState]:
        """Show the same arrangement for `seconds`; returns newly committed stable states."""
        out = []
        end = self.t + seconds
        while self.t < end:
            res = self.tracker.update(layout(self.t, **state_spec), motion, self.t)
            if res.new_stable:
                out.append(res.new_stable)
            self.t += self.dt
        return out
