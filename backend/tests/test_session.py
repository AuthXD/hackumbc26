"""End-to-end through Session with real JPEG frames: vision → tracker → teach/practice."""

import cv2

from app.session import Session

from .synthetic import SKIN, blank, draw_block, zone_center

SLOTS = {"red": 0, "blue": 1, "yellow": 2, "green": 3}


def frame(layout: dict[str, str], hand_at: tuple[int, int] | None = None) -> bytes:
    img = blank()
    for color, zone in layout.items():
        draw_block(img, color, zone_center(zone, SLOTS[color]))
    if hand_at:
        cv2.ellipse(img, hand_at, (70, 55), 0, 0, 360, SKIN, -1)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return buf.tobytes()


class Driver:
    def __init__(self) -> None:
        self.s = Session(persist=False)
        self.t = 5000.0
        self.last: dict = {}

    def show(self, jpeg: bytes, seconds: float) -> dict:
        end = self.t + seconds
        while self.t < end:
            self.last = self.s.process_frame(jpeg, now=self.t)
            self.t += 0.2
        return self.last

    def move(self, layout: dict, **changes: str) -> dict:
        """Hand reaches in (motion + occlusion), then the new layout holds still."""
        new = {**layout, **changes}
        obj = next(iter(changes))
        self.show(frame(layout, hand_at=zone_center(layout[obj], SLOTS[obj])), 0.6)
        self.show(frame(new, hand_at=zone_center(new[obj], SLOTS[obj])), 0.6)
        self.show(frame(new), 1.6)
        return new


START = {"red": "A", "blue": "A", "yellow": "B", "green": "C"}
STEPS = [{"red": "B"}, {"green": "A"}, {"blue": "C"}, {"yellow": "C"}]


def teach(d: Driver) -> dict:
    d.s.command("teach")
    d.show(frame(START), 1.6)
    layout = START
    for step in STEPS:
        layout = d.move(layout, **step)
    return layout


def test_full_demo_through_real_frames():
    d = Driver()
    teach(d)
    snap = d.last
    assert snap["mode"] == "idle"
    steps = snap["procedure"]["steps"]
    assert [s["description"]["instruction"] for s in steps] == [
        "Move the red object from Zone A to Zone B.",
        "Move the green object from Zone C to Zone A.",
        "Move the blue object from Zone A to Zone C.",
        "Move the yellow object from Zone B to Zone C.",
    ]
    assert all(s["beforeImage"] and s["afterImage"] for s in steps)

    # Practice: step 1 right, then skip step 2, get told, undo, finish correctly.
    d.s.command("practice")
    d.show(frame(START), 1.6)
    assert d.last["practice"]["status"] == "waiting"
    layout = d.move(START, red="B")
    assert d.last["practice"]["status"] == "step_complete"
    wrong = d.move(layout, blue="C")  # skips "green → A"
    p = d.last["practice"]
    assert p["status"] == "error" and p["errorType"] == "skipped_step"
    assert p["expectedStepIndex"] == 1
    assert p["fixHint"].startswith("Put the blue object back in Zone A.")
    layout = d.move(wrong, blue="A")  # undo
    assert d.last["practice"]["status"] == "waiting"
    for step in STEPS[1:]:
        layout = d.move(layout, **step)
    assert d.last["practice"]["status"] == "complete"
    assert d.last["practice"]["completed"] == [0, 1, 2, 3]


def test_hand_over_the_table_never_becomes_a_step():
    d = Driver()
    d.s.command("teach")
    d.show(frame(START), 1.6)
    # A hand hovers still over zone B for a long time without moving anything.
    d.show(frame(START, hand_at=zone_center("B", 2)), 3.0)
    d.show(frame(START), 2.0)
    assert d.last["teach"]["stepsRecorded"] == 0


def test_reset_clears_procedure_and_allows_new_teaching():
    d = Driver()
    teach(d)
    assert d.last["procedure"]
    snap = d.s.command("reset")
    assert snap["procedure"] is None and snap["mode"] == "idle"
    d.s.command("teach")
    d.show(frame(START), 1.6)
    d.move(START, yellow="A")
    snap = d.s.command("finish")
    assert [s["description"]["title"] for s in snap["procedure"]["steps"]] == ["Yellow: B → A"]


def test_practice_without_procedure_is_refused():
    d = Driver()
    snap = d.s.command("practice")
    assert snap["mode"] == "idle"
    assert "Teach" in snap["notice"]
