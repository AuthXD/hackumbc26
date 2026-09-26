import itertools
import random

import pytest

from app.engine import PracticeEngine, TeachRecorder
from app.models import StepDelta

from .helpers import Clock, layout, moved, practice_through, teach

START = dict(red="A", blue="A", yellow="B", green="C")
# A judge-style 4-step procedure: three moves and a stack.
STEPS = [
    dict(red="B"),
    dict(blue="C"),
    dict(yellow="on:blue"),
    dict(green="A"),
]


def learned():
    rec = teach(START, *STEPS)
    assert rec.phase == "done"
    return rec.procedure()


def errors(events):
    return [e for e in events if e.kind == "speak" and e.priority == "error"]


# ---------------------------------------------------------------------------------------------
# Teach
# ---------------------------------------------------------------------------------------------


def test_teach_records_four_state_transitions_with_descriptions():
    proc = learned()
    assert len(proc.steps) == 4
    assert proc.tracked_ids == ["blue", "green", "red", "yellow"]
    s0 = proc.steps[0]
    assert s0.delta.changed_ids == ["red"]
    assert s0.delta.before["red"].zone == "A" and s0.delta.after["red"].zone == "B"
    assert s0.description.instruction == "Move the red object from Zone A to Zone B."
    assert proc.steps[2].description.instruction == "Stack the yellow object on the blue object."
    assert proc.steps[2].delta.postconditions["yellow"].stacked_on == "blue"


def test_teach_ignores_duplicate_stable_state():
    rec = TeachRecorder(steps_target=4, min_objects=2)
    s = layout(**START)
    rec.on_stable(s)
    assert rec.on_stable(layout(**START)) == []
    assert rec.steps == []
    rec.on_stable(layout(**moved(s, red="B")))
    rec.on_stable(layout(**moved(s, red="B")))
    assert len(rec.steps) == 1


def test_teach_ignores_state_with_hidden_object_and_refines_initial_before_first_step():
    rec = TeachRecorder(steps_target=4, min_objects=2)
    rec.on_stable(layout(red="A", blue="A", yellow="B"))  # green was hidden by a hand
    rec.on_stable(layout(**START))  # green appears: initial refined, not a step
    assert rec.tracked == ["blue", "green", "red", "yellow"] and rec.steps == []
    rec.on_stable(layout(red="B", blue="A", yellow="B"))  # green hidden again → ignored
    assert rec.steps == []


def test_returning_an_object_is_a_real_step_and_undo_is_explicit():
    rec = TeachRecorder(steps_target=4, min_objects=2)
    s = layout(**START)
    rec.on_stable(s)
    rec.on_stable(layout(**moved(s, red="C")))
    rec.on_stable(s)  # moving it back is a legitimate second step
    assert [st.delta.after["red"].zone for st in rec.steps] == ["C", "A"]
    rec.undo_last(s)
    rec.undo_last(s)
    assert rec.steps == []
    rec.on_stable(layout(**moved(s, red="B")))  # re-taught from the restored layout
    assert [st.delta.after["red"].zone for st in rec.steps] == ["B"]


def test_teach_needs_minimum_objects():
    rec = TeachRecorder(steps_target=4, min_objects=2)
    rec.on_stable(layout(red="A"))
    assert rec.phase == "capturing_initial"


# ---------------------------------------------------------------------------------------------
# Practice
# ---------------------------------------------------------------------------------------------


def test_four_correct_steps_complete_the_procedure():
    eng = PracticeEngine(learned())
    events = practice_through(eng, START, *STEPS)
    assert eng.state.status == "complete"
    assert eng.state.completed == [0, 1, 2, 3]
    assert not errors(events)
    assert any("Procedure complete" in e.text for e in events)


def test_setup_mismatch_gives_hints_then_starts():
    eng = PracticeEngine(learned())
    eng.on_stable(layout(**dict(START, red="C")))
    assert eng.state.status == "setup"
    assert "Put the red object in Zone A." in eng.state.fix_hint
    eng.on_stable(layout(**START))
    assert eng.state.status == "waiting" and eng.state.expected_step_index == 0


def test_skipped_step_is_reported_and_does_not_advance():
    eng = PracticeEngine(learned())
    practice_through(eng, START, STEPS[0])  # step 1 ok
    s1 = layout(**moved(layout(**START), **STEPS[0]))
    events = eng.on_stable(layout(**moved(s1, **STEPS[2])))  # yellow onto blue, skipping "blue → C"
    assert eng.state.status == "error"
    assert eng.state.error_type == "skipped_step"
    assert eng.state.expected_step_index == 1
    assert "Move the blue object from Zone A to Zone C" in eng.state.expected_description
    assert "step 3" in eng.state.observed_description
    assert "Put the yellow object back in Zone B." in eng.state.fix_hint
    assert len(errors(events)) == 1


def test_out_of_order_step_far_ahead():
    eng = PracticeEngine(learned())
    eng.on_stable(layout(**START))
    eng.on_stable(layout(**moved(layout(**START), **STEPS[3])))  # green → A first
    assert eng.state.error_type == "out_of_order"
    assert eng.state.headline == "Step 4 is out of order"
    assert eng.state.expected_step_index == 0


def test_wrong_object_moving():
    eng = PracticeEngine(learned())
    eng.on_stable(layout(**START))
    eng.on_stable(layout(**moved(layout(**START), yellow="C")))  # nobody moves yellow → C
    assert eng.state.error_type == "wrong_object"
    assert "yellow object moved from Zone B to Zone C" in eng.state.observed_description
    assert "Put the yellow object back in Zone B." in eng.state.fix_hint


def test_correct_object_to_wrong_zone_then_fixed():
    eng = PracticeEngine(learned())
    eng.on_stable(layout(**START))
    eng.on_stable(layout(**moved(layout(**START), red="C")))
    assert eng.state.error_type == "wrong_placement"
    assert eng.state.fix_hint == "Move the red object to Zone B."
    assert eng.state.expected_step_index == 0
    eng.on_stable(layout(**moved(layout(**START), red="B")))  # move it straight to the right zone
    assert eng.state.status == "step_complete" and eng.state.expected_step_index == 1


def test_undo_error_then_complete_expected_step_and_continue():
    eng = PracticeEngine(learned())
    start = layout(**START)
    eng.on_stable(start)
    eng.on_stable(layout(**moved(start, green="A")))  # mistake
    assert eng.state.status == "error"
    events = eng.on_stable(start)  # undone
    assert eng.state.status == "waiting" and eng.state.error_type is None
    assert any("undone" in e.text for e in events)
    rest = practice_through(eng, START, *STEPS)
    assert eng.state.status == "complete"
    assert not errors(rest)


def test_error_is_not_cleared_by_doing_expected_step_on_top_of_mistake():
    eng = PracticeEngine(learned())
    start = layout(**START)
    eng.on_stable(start)
    eng.on_stable(layout(**moved(start, green="A")))
    eng.on_stable(layout(**moved(start, green="A", red="B")))  # did step 1 without undoing
    assert eng.state.status == "error" and eng.state.expected_step_index == 0


def test_repeated_identical_error_is_spoken_once():
    eng = PracticeEngine(learned())
    start = layout(**START)
    eng.on_stable(start)
    first = eng.on_stable(layout(**moved(start, yellow="C")))
    again = eng.on_stable(layout(**moved(start, yellow="C")))
    assert len(errors(first)) == 1 and errors(again) == []


def test_stack_step_counts_even_if_base_zone_differs_only_by_carry():
    # Step 1 stacks red on blue, step 2 moves blue (carrying red) to C.
    rec = teach(dict(red="A", blue="B"), dict(red="on:blue"), dict(blue="C"))
    proc = rec.procedure()
    assert proc.steps[1].delta.changed_ids == ["blue", "red"]
    assert "carrying the red object on top" in proc.steps[1].description.instruction
    eng = PracticeEngine(proc)
    practice_through(eng, dict(red="A", blue="B"), dict(red="on:blue"), dict(blue="C"))
    assert eng.state.status == "complete"


def test_unstack_step():
    rec = teach(dict(red="on:blue", blue="A"), dict(red="C"))
    assert rec.steps[0].description.instruction == "Take the red object off the blue object and put it in Zone C."


# ---------------------------------------------------------------------------------------------
# Tracker + engine: timing, motion, occlusion
# ---------------------------------------------------------------------------------------------


def test_temporary_occlusion_produces_waiting_not_error():
    clock = Clock()
    eng = PracticeEngine(learned())
    clock.tracker.reset(required=set(eng.tracked))
    for s in clock.hold(START, 1.2):
        eng.on_stable(s)
    assert eng.state.status == "waiting"
    hidden = {k: v for k, v in START.items() if k != "red"}  # a hand covers red
    assert clock.hold(hidden, 1.5) == []
    assert clock.tracker.update(layout(**hidden), 0.0, clock.t).status == "occluded"
    for s in clock.hold(START, 1.2):  # hand leaves; same layout → duplicate, no new state
        eng.on_stable(s)
    assert eng.state.status == "waiting"


def test_motion_and_short_transitions_never_commit():
    clock = Clock()
    assert len(clock.hold(START, 1.0)) == 1
    assert clock.hold(dict(START, red=None), 0.4) == []  # object in transit between zones, briefly
    assert clock.hold(dict(START, red="B"), 2.0, motion=12.0) == []  # hand still working
    committed = clock.hold(dict(START, red="B"), 1.0)
    assert len(committed) == 1 and committed[0].arrangement()["red"].zone == "B"


def test_duplicate_stable_state_emitted_once():
    clock = Clock()
    assert len(clock.hold(START, 3.0)) == 1
    assert clock.hold(START, 3.0) == []


def test_stable_requires_700ms():
    clock = Clock()
    assert clock.hold(START, 0.6) == []
    assert len(clock.hold(START, 0.3)) == 1


# ---------------------------------------------------------------------------------------------
# Reset + any judge-chosen order
# ---------------------------------------------------------------------------------------------


def test_reset_and_teach_a_different_procedure():
    first = learned()
    order2 = [dict(green="B"), dict(red="on:green"), dict(yellow="A"), dict(blue="B")]
    second = teach(START, *order2).procedure()
    assert [s.description.title for s in second.steps] != [s.description.title for s in first.steps]
    eng = PracticeEngine(second)
    practice_through(eng, START, *order2)
    assert eng.state.status == "complete"
    # Following the old procedure against the new one fails on the first step.
    eng2 = PracticeEngine(second)
    practice_through(eng2, START, STEPS[0])
    assert eng2.state.status == "error" and eng2.state.expected_step_index == 0


ZONES = ["A", "B", "C"]


def random_procedure(rng: random.Random):
    """Four valid moves: each step moves an object to a different zone than it is in."""
    ids = ["red", "blue", "yellow", "green"]
    start = {i: rng.choice(ZONES) for i in ids}
    cur = dict(start)
    steps = []
    for _ in range(4):
        oid = rng.choice(ids)
        dest = rng.choice([z for z in ZONES if z != cur[oid]])
        cur[oid] = dest
        steps.append({oid: dest})
    return start, steps


@pytest.mark.parametrize("seed", range(25))
def test_any_judge_order_is_learned_and_checked(seed):
    rng = random.Random(seed)
    start, steps = random_procedure(rng)
    proc = teach(start, *steps).procedure()
    assert len(proc.steps) == 4
    ok = PracticeEngine(proc)
    practice_through(ok, start, *steps)
    assert ok.state.status == "complete", (start, steps)

    # Swapping two steps that touch different objects must be caught at the first swapped step.
    for a, b in itertools.combinations(range(4), 2):
        if b == a + 1 and set(steps[a]) != set(steps[b]):
            swapped = steps[:a] + [steps[b], steps[a]] + steps[b + 1:]
            eng = PracticeEngine(proc)
            practice_through(eng, start, *swapped[: a + 1])
            assert eng.state.status == "error", (start, steps, a)
            assert eng.state.expected_step_index == a
            break


def test_step_delta_between():
    d = StepDelta.between(layout(red="A", blue="B").arrangement(), layout(red="C", blue="B").arrangement())
    assert d.changed_ids == ["red"] and d.after["red"].zone == "C"
