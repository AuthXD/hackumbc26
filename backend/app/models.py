"""Domain types shared by vision, teaching, and practice.

The key idea: pixels are reduced to a discrete *arrangement* — for every tracked object, which
zone it is in and what it is stacked on. Learned steps and practice checks compare arrangements,
never raw images, which keeps pass/fail deterministic.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    def to_json(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")


class Placement(CamelModel):
    """Where one object is, in the terms the procedure cares about."""

    zone: str | None  # zone id ("A", "B", "C") or None when outside every zone
    stacked_on: str | None = None  # id of the object directly underneath, if any

    def key(self) -> tuple[str | None, str | None]:
        return (self.zone, self.stacked_on)


Arrangement = dict[str, Placement]


class SceneObject(CamelModel):
    id: str
    kind: Literal["color", "semantic"] = "color"
    color: str | None
    label: str | None = None
    center: tuple[float, float]  # normalized frame coordinates
    bbox: tuple[float, float, float, float]  # x, y, w, h normalized
    zone: str | None
    visible: bool = True
    stacked_on: str | None = None
    confidence: float | None = 1.0

    @model_validator(mode="after")
    def consistent_identity(self):
        if self.kind == "color":
            if not self.color or self.label is not None or self.id != self.color or self.confidence is None:
                raise ValueError("Color identity must match color, without a semantic label")
        elif not self.label or self.id != self.label or self.color is not None or self.confidence is not None or self.stacked_on:
            raise ValueError("Semantic identity requires its exact label, no color, confidence or stacking")
        return self

    def placement(self) -> Placement:
        return Placement(zone=self.zone, stacked_on=self.stacked_on)


class SceneState(CamelModel):
    objects: list[SceneObject] = Field(default_factory=list)
    captured_at: float = 0.0
    stable_since: float | None = None

    @model_validator(mode="after")
    def consistent_objects(self):
        if len({o.id for o in self.objects}) != len(self.objects) or len({o.kind for o in self.objects}) > 1:
            raise ValueError("A scene must have unique identities from one detector")
        return self

    def arrangement(self, only: set[str] | None = None) -> Arrangement:
        return {
            o.id: o.placement()
            for o in self.objects
            if o.visible and (only is None or o.id in only)
        }

    def visible_ids(self) -> set[str]:
        return {o.id for o in self.objects if o.visible}


def arrangement_key(arr: Arrangement) -> tuple:
    return tuple(sorted((k, v.key()) for k, v in arr.items()))


def arrangements_equal(a: Arrangement, b: Arrangement) -> bool:
    return arrangement_key(a) == arrangement_key(b)


class StepDelta(CamelModel):
    """What changed between two stable arrangements.

    `after` doubles as the step's postconditions: the step is satisfied when every changed object
    has exactly its `after` placement (and nothing else moved).
    """

    changed_ids: list[str]
    before: dict[str, Placement]
    after: dict[str, Placement]

    @property
    def postconditions(self) -> dict[str, Placement]:
        return self.after

    @staticmethod
    def between(before: Arrangement, after: Arrangement) -> "StepDelta":
        ids = sorted(set(before) | set(after))
        changed = [
            i for i in ids
            if (before.get(i).key() if i in before else None) != (after.get(i).key() if i in after else None)
        ]
        return StepDelta(
            changed_ids=changed,
            before={i: before[i] for i in changed if i in before},
            after={i: after[i] for i in changed if i in after},
        )

    def is_empty(self) -> bool:
        return not self.changed_ids


class StepText(CamelModel):
    title: str
    instruction: str


class LearnedStep(CamelModel):
    index: int
    before_state: SceneState
    after_state: SceneState
    delta: StepDelta
    before_image: str | None = None  # URL path served by the backend
    after_image: str | None = None
    description: StepText  # deterministic, always present
    ai_description: StepText | None = None  # optional Gemini wording; never used for pass/fail


class Procedure(CamelModel):
    initial_state: SceneState
    tracked_ids: list[str]
    steps: list[LearnedStep] = Field(default_factory=list)

    @property
    def detector_kind(self) -> Literal["color", "semantic"]:
        return self.initial_state.objects[0].kind if self.initial_state.objects else "color"

    def arrangement_before(self, index: int) -> Arrangement:
        """Expected arrangement before step `index` (== after step index-1)."""
        tracked = set(self.tracked_ids)
        if index == 0:
            return self.initial_state.arrangement(tracked)
        return self.steps[index - 1].after_state.arrangement(tracked)


PracticeStatus = Literal["setup", "waiting", "step_complete", "error", "complete"]
ErrorType = Literal[
    "skipped_step",
    "out_of_order",
    "wrong_object",
    "wrong_placement",
    "incomplete_step",
    "extra_change",
]


class PracticeState(CamelModel):
    expected_step_index: int = 0
    status: PracticeStatus = "setup"
    error_type: ErrorType | None = None
    headline: str = ""
    expected_description: str = ""
    observed_description: str = ""
    fix_hint: str = ""
    completed: list[int] = Field(default_factory=list)
    observed_state: SceneState | None = None
