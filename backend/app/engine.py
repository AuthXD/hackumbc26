"""Teach recorder and practice checker. Pure logic over committed (stable) SceneStates.

Nothing here looks at pixels or calls an AI model: every pass/fail decision is a comparison of
discrete placements, so the same inputs always give the same verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .describe import describe_delta, describe_observed, obj, put_back, to_where, where
from .models import (
    Arrangement,
    LearnedStep,
    Placement,
    PracticeState,
    Procedure,
    SceneState,
    StepDelta,
    arrangements_equal,
)


@dataclass
class Event:
    kind: Literal["speak", "step_learned"]
    text: str = ""
    priority: Literal["info", "success", "error"] = "info"
    step_index: int | None = None

    def to_json(self) -> dict:
        return {"kind": self.kind, "text": self.text, "priority": self.priority}


def speak(text: str, priority: Literal["info", "success", "error"] = "info") -> Event:
    return Event("speak", text, priority)


def placement_matches(observed: Placement | None, expected: Placement | None) -> bool:
    """Postcondition check for one object.

    A stacked object only has to be on the right base: its zone follows the base, which may
    legitimately differ from teaching time if an earlier step is still pending.
    """
    if observed is None or expected is None:
        return observed is expected
    if expected.stacked_on:
        return observed.stacked_on == expected.stacked_on
    return observed.stacked_on is None and observed.zone == expected.zone


# ---------------------------------------------------------------------------------------------
# Teach
# ---------------------------------------------------------------------------------------------


class TeachRecorder:
    """Builds a Procedure from a stream of stable states during one demonstration."""

    def __init__(self, steps_target: int, min_objects: int) -> None:
        self.steps_target = steps_target
        self.min_objects = min_objects
        self.phase: Literal["capturing_initial", "recording", "done"] = "capturing_initial"
        self.initial: SceneState | None = None
        self.initial_image: str | None = None
        self.tracked: list[str] = []
        self.steps: list[LearnedStep] = []
        self._baseline: SceneState | None = None
        self._baseline_image: str | None = None
        self.message = "Arrange the starting layout and hold still."

    @property
    def tracked_set(self) -> set[str]:
        return set(self.tracked)

    def on_stable(self, scene: SceneState, image: str | None = None) -> list[Event]:
        ids = scene.visible_ids()
        if self.phase == "capturing_initial":
            if len(ids) < self.min_objects:
                self.message = f"Need at least {self.min_objects} objects in view to start."
                return []
            self._set_initial(scene, image)
            self.phase = "recording"
            return [speak(f"Starting layout captured with {len(ids)} objects. Show step 1.")]

        if self.phase != "recording":
            return []

        if not self.steps and ids > self.tracked_set:
            # An object was hidden when the layout was first captured; take the fuller view.
            self._set_initial(scene, image)
            return []
        if ids != self.tracked_set:
            return []  # an object is missing: occlusion, not a step

        before = self._baseline.arrangement(self.tracked_set)
        after = scene.arrangement(self.tracked_set)
        delta = StepDelta.between(before, after)
        if delta.is_empty():
            return []

        step = LearnedStep(
            index=len(self.steps),
            before_state=self._baseline,
            after_state=scene,
            delta=delta,
            before_image=self._baseline_image,
            after_image=image,
            description=describe_delta(delta),
        )
        self.steps.append(step)
        self._baseline, self._baseline_image = scene, image
        events = [
            Event("step_learned", step_index=step.index),
            speak(f"Step {step.index + 1}: {step.description.instruction}", "success"),
        ]
        if len(self.steps) >= self.steps_target:
            self.phase = "done"
            self.message = f"Learned all {self.steps_target} steps."
            events.append(speak("Procedure learned. Ready for practice.", "success"))
        else:
            self.message = f"Learned step {len(self.steps)}. Show step {len(self.steps) + 1}."
        return events

    def _set_initial(self, scene: SceneState, image: str | None) -> None:
        self.initial, self.initial_image = scene, image
        self._baseline, self._baseline_image = scene, image
        self.tracked = sorted(scene.visible_ids())
        self.message = f"Starting layout captured ({', '.join(self.tracked)}). Perform step 1."

    def undo_last(self, current: SceneState | None) -> list[Event]:
        """Drop the last learned step. The teacher then restores the layout and shows it again.

        Explicit (a button), not inferred: a procedure may legitimately return an object to where it
        was, so "the layout went back" cannot be read as "undo".
        """
        if not self.steps:
            return []
        removed = self.steps.pop()
        self._baseline, self._baseline_image = removed.before_state, removed.before_image
        self.phase = "recording"
        n = removed.index + 1
        self.message = f"Step {n} removed. Put things back as they were before step {n}, then show it again."
        return [speak(f"Step {n} removed. Reset the table to how it was before step {n}.")]

    def finish(self) -> Procedure | None:
        if self.initial is None or not self.steps:
            return None
        self.phase = "done"
        return self.procedure()

    def procedure(self) -> Procedure | None:
        if self.initial is None:
            return None
        return Procedure(initial_state=self.initial, tracked_ids=self.tracked, steps=list(self.steps))

    def to_json(self) -> dict:
        return {
            "phase": self.phase,
            "stepsRecorded": len(self.steps),
            "target": self.steps_target,
            "message": self.message,
            "trackedIds": self.tracked,
        }


# ---------------------------------------------------------------------------------------------
# Practice
# ---------------------------------------------------------------------------------------------


def _step_label(step: LearnedStep) -> str:
    return f"Step {step.index + 1}: {step.description.instruction}"


class PracticeEngine:
    """Aligns each new stable state against the learned sequence."""

    def __init__(self, procedure: Procedure) -> None:
        self.proc = procedure
        self.tracked = set(procedure.tracked_ids)
        self.state = PracticeState(
            status="setup",
            headline="Set up the starting layout",
            expected_description=self._setup_expected(),
        )

    # -- helpers ----------------------------------------------------------------------------

    @property
    def n_steps(self) -> int:
        return len(self.proc.steps)

    def _setup_expected(self) -> str:
        start = self.proc.arrangement_before(0)
        return " ".join(f"{obj(i).capitalize()} {where(p)}." for i, p in sorted(start.items()))

    def _set(self, **fields) -> None:
        self.state = self.state.model_copy(update=fields)

    # -- main entry point -------------------------------------------------------------------

    def on_stable(self, scene: SceneState) -> list[Event]:
        if scene.visible_ids() != self.tracked:
            return []  # an object is hidden: wait
        current = scene.arrangement(self.tracked)
        self._set(observed_state=scene)

        if self.state.status == "complete":
            return []
        if self.state.status == "setup":
            return self._check_setup(current)

        k = self.state.expected_step_index
        step = self.proc.steps[k]
        base = self.proc.arrangement_before(k)
        observed = StepDelta.between(base, current)

        if observed.is_empty():
            if self.state.status == "error":
                self._set(
                    status="waiting",
                    error_type=None,
                    headline=f"Back on track — do step {k + 1}",
                    observed_description="The mistake was undone.",
                    fix_hint="",
                    expected_description=_step_label(step),
                )
                return [speak(f"Good, that's undone. Now, {step.description.instruction}", "info")]
            return []

        return self._judge(k, step, base, current, observed)

    def _check_setup(self, current: Arrangement) -> list[Event]:
        start = self.proc.arrangement_before(0)
        if arrangements_equal(current, start):
            first = self.proc.steps[0]
            self._set(
                status="waiting",
                expected_step_index=0,
                headline=f"Step 1 of {self.n_steps}",
                expected_description=_step_label(first),
                observed_description="Starting layout matches.",
                fix_hint="",
                error_type=None,
            )
            return [speak(f"Starting layout looks right. Step 1: {first.description.instruction}")]
        wrong = [i for i in sorted(start) if not placement_matches(current.get(i), start[i])]
        self._set(
            observed_description=" ".join(f"{obj(i).capitalize()} is {where(current[i])}." for i in wrong),
            fix_hint=" ".join(f"Put {obj(i)} {where(start[i])}." for i in wrong),
        )
        return []

    def _judge(
        self, k: int, step: LearnedStep, base: Arrangement, current: Arrangement, observed: StepDelta
    ) -> list[Event]:
        expected = step.delta
        exp_ids = set(expected.changed_ids)
        correct = {i for i in exp_ids if placement_matches(current.get(i), expected.after.get(i))}
        untouched = {i for i in exp_ids if i not in observed.changed_ids}
        wrong_placed = sorted(exp_ids - correct - untouched)
        unexpected = sorted(i for i in observed.changed_ids if i not in exp_ids)

        # ---- success -----------------------------------------------------------------------
        if correct == exp_ids and not unexpected:
            return self._advance(k, step)

        # ---- which later step, if any, explains the unexpected changes? --------------------
        later = [
            j for j in range(k + 1, self.n_steps)
            if any(
                i in self.proc.steps[j].delta.after
                and placement_matches(current.get(i), self.proc.steps[j].delta.after[i])
                for i in unexpected
            )
        ]
        undo = [put_back(i, base[i]) for i in unexpected]

        if later:
            j = later[0]
            ahead = self.proc.steps[j]
            if j == k + 1:
                error_type, headline = "skipped_step", f"Skipped step {k + 1}"
            else:
                error_type, headline = "out_of_order", f"Step {j + 1} is out of order"
            observed_text = f"You did step {j + 1} — {ahead.description.instruction}"
            fix = " ".join(undo) + f" Then do step {k + 1}."
        elif wrong_placed:
            error_type, headline = "wrong_placement", f"Step {k + 1}: wrong spot"
            observed_text = describe_observed(StepDelta.between(base, current))
            fix = " ".join(undo + [f"Move {obj(i)} {to_where(expected.after[i])}." for i in wrong_placed])
        elif untouched == exp_ids:
            error_type, headline = "wrong_object", f"Step {k + 1}: wrong object"
            observed_text = describe_observed(observed)
            fix = " ".join(undo) + f" Then: {step.description.instruction}"
        elif correct and untouched and not unexpected:
            error_type, headline = "incomplete_step", f"Step {k + 1} is not finished"
            observed_text = describe_observed(observed)
            fix = " ".join(f"Move {obj(i)} {to_where(expected.after[i])}." for i in sorted(untouched))
        else:
            error_type, headline = "extra_change", "Something else moved"
            observed_text = describe_observed(StepDelta(
                changed_ids=unexpected,
                before={i: base[i] for i in unexpected if i in base},
                after={i: current[i] for i in unexpected if i in current},
            ))
            fix = " ".join(undo)

        same_error = self.state.status == "error" and self.state.observed_description == observed_text
        self._set(
            status="error",
            error_type=error_type,
            headline=headline,
            expected_description=_step_label(step),
            observed_description=observed_text,
            fix_hint=fix.strip(),
        )
        if same_error:
            return []
        return [speak(
            f"{headline}. Expected: {step.description.instruction} Observed: {observed_text}. {fix.strip()}",
            "error",
        )]

    def _advance(self, k: int, step: LearnedStep) -> list[Event]:
        completed = sorted(set(self.state.completed) | {k})
        if k + 1 >= self.n_steps:
            self._set(
                status="complete",
                expected_step_index=self.n_steps,
                completed=completed,
                error_type=None,
                headline="Procedure complete",
                expected_description="",
                observed_description=f"All {self.n_steps} steps done in the right order.",
                fix_hint="",
            )
            return [speak(f"Step {k + 1} complete. Procedure complete. Nice work!", "success")]
        nxt = self.proc.steps[k + 1]
        self._set(
            status="step_complete",
            expected_step_index=k + 1,
            completed=completed,
            error_type=None,
            headline=f"Step {k + 1} done — now step {k + 2}",
            expected_description=_step_label(nxt),
            observed_description=step.description.instruction.rstrip(".") + " ✓",
            fix_hint="",
        )
        return [speak(f"Step {k + 1} complete. Next: {nxt.description.instruction}", "success")]
