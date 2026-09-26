"""Deterministic, template-based wording for placements, steps, and corrections."""

from __future__ import annotations

from .config import settings
from .models import Placement, StepDelta, StepText


def zone_label(zone: str | None) -> str:
    if zone is None:
        return "outside the zones"
    return next((z.label for z in settings.vision.zones if z.id == zone), f"Zone {zone}")


def obj(obj_id: str) -> str:
    return f"the {obj_id} object"


def where(p: Placement) -> str:
    """'in Zone B' / 'on the blue object' / 'outside the zones'."""
    if p.stacked_on:
        return f"on {obj(p.stacked_on)}"
    return "outside the zones" if p.zone is None else f"in {zone_label(p.zone)}"


def to_where(p: Placement) -> str:
    """'to Zone B' / 'onto the blue object' / 'out of the zones'."""
    if p.stacked_on:
        return f"onto {obj(p.stacked_on)}"
    return "out of the zones" if p.zone is None else f"to {zone_label(p.zone)}"


def _is_carried(b: Placement | None, a: Placement | None) -> bool:
    """Only the zone changed because the object rode along on top of something that moved."""
    return bool(b and a and b.stacked_on and b.stacked_on == a.stacked_on)


def describe_change(obj_id: str, b: Placement | None, a: Placement | None) -> tuple[str, str]:
    """(title, instruction) for one object's change."""
    name = obj(obj_id)
    color = obj_id.capitalize()
    if a is None:
        return f"Remove {obj_id}", f"Take {name} away."
    if b is None:
        return f"Add {obj_id}", f"Place {name} {where(a)}."
    if a.stacked_on and a.stacked_on != b.stacked_on:
        return f"Stack {obj_id} on {a.stacked_on}", f"Stack {name} on {obj(a.stacked_on)}."
    if b.stacked_on and not a.stacked_on:
        return (
            f"Unstack {obj_id}",
            f"Take {name} off {obj(b.stacked_on)} and put it {where(a)}.",
        )
    if a.zone is None:
        return f"Remove {obj_id} from {b.zone}", f"Move {name} out of {zone_label(b.zone)}."
    if b.zone is None:
        return f"Place {obj_id} in {a.zone}", f"Place {name} in {zone_label(a.zone)}."
    return f"{color}: {b.zone} → {a.zone}", f"Move {name} from {zone_label(b.zone)} to {zone_label(a.zone)}."


def describe_delta(delta: StepDelta) -> StepText:
    primary = [i for i in delta.changed_ids if not _is_carried(delta.before.get(i), delta.after.get(i))]
    carried = [i for i in delta.changed_ids if i not in primary]
    if not primary:  # shouldn't happen, but never return an empty description
        primary, carried = delta.changed_ids, []
    parts = [describe_change(i, delta.before.get(i), delta.after.get(i)) for i in primary]
    title = " + ".join(t for t, _ in parts)
    instruction = " ".join(s for _, s in parts)
    if carried:
        riders = " and ".join(obj(i) for i in carried)
        instruction = instruction.rstrip(".") + f", carrying {riders} on top."
    return StepText(title=title, instruction=instruction)


def describe_observed(delta: StepDelta) -> str:
    """Past-tense summary of what actually changed, e.g. 'The green object moved from Zone A to Zone C.'"""
    sentences = []
    for i in delta.changed_ids:
        b, a = delta.before.get(i), delta.after.get(i)
        if _is_carried(b, a):
            continue
        if a is None:
            sentences.append(f"{obj(i).capitalize()} disappeared.")
        elif b is not None and a.stacked_on and a.stacked_on != b.stacked_on:
            sentences.append(f"{obj(i).capitalize()} was stacked on {obj(a.stacked_on)}.")
        elif b is not None and b.stacked_on and not a.stacked_on:
            sentences.append(f"{obj(i).capitalize()} was taken off {obj(b.stacked_on)} and put {where(a)}.")
        elif b is not None:
            sentences.append(f"{obj(i).capitalize()} moved from {zone_label(b.zone)} to {zone_label(a.zone)}.")
        else:
            sentences.append(f"{obj(i).capitalize()} appeared {where(a)}.")
    return " ".join(sentences) or "Nothing changed."


def put_back(obj_id: str, target: Placement) -> str:
    return f"Put {obj(obj_id)} back {where(target)}."
