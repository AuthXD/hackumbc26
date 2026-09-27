"""Conservative semantic boundaries and real subprocess supervision, without model/GPU dependencies."""
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.detectors import AmbiguousScan, ColorDetector, LatestScan, parse_labels, semantic_scene
from app.engine import TeachRecorder
from app.locate_worker import LocateWorker
from app.models import SceneObject, SceneState
from app.session import Session
from .test_session import START, frame

LABELS = ("blue bottle", "brown wallet")
BOXES = [{"label": LABELS[0], "bbox": [.05, .2, .25, .4]},
         {"label": LABELS[1], "bbox": [.7, .3, .9, .5]}]


def scene(boxes=BOXES, now=0):
    return semantic_scene(boxes, LABELS, Settings().vision, now)


def test_semantic_xyxy_conversion_zones_and_identity():
    result = scene()
    assert result.objects[0].bbox == pytest.approx((.05, .2, .2, .2))
    assert [o.zone for o in result.objects] == ["A", "C"]
    assert result.objects[0].id == result.objects[0].label == "blue bottle"
    assert result.objects[0].color is None and result.objects[0].confidence is None
    result = semantic_scene([{**BOXES[0], "label": "BLUE BOTTLE"}, BOXES[1]], LABELS, Settings().vision, 1)
    assert result.objects[0].id == "blue bottle"


@pytest.mark.parametrize("text", ["bottle", "bottle,", "bottle, Bottle", "a,b,c,d,e,f,g", "bottle</c>,wallet"])
def test_invalid_or_duplicate_requested_labels(text):
    with pytest.raises(ValueError):
        parse_labels(text)


@pytest.mark.parametrize("boxes", [BOXES[:1], BOXES + [BOXES[0]],
                                     [BOXES[0], {"label": "unknown", "bbox": [.7,.3,.9,.5]}],
                                     [BOXES[0], {**BOXES[1], "bbox": [.05,.2,.25,.4]}],
                                     [{**BOXES[0], "bbox": [0,0,float('nan'),1]}, BOXES[1]]])
def test_missing_duplicate_unexpected_overlapping_or_invalid_are_ambiguous(boxes):
    with pytest.raises(AmbiguousScan):
        scene(boxes)


def test_mixed_identity_models_rejected():
    obj = scene().objects[0].model_dump()
    with pytest.raises(ValidationError):
        SceneObject(**{**obj, "color": "blue"})
    with pytest.raises(ValidationError):
        SceneObject(**{**obj, "id": "another object"})
    color = SceneObject(id="red", color="red", center=(.2,.2), bbox=(.1,.1,.2,.2), zone="A")
    with pytest.raises(ValidationError):
        SceneState(objects=[color, scene().objects[0]])


class ControlledDetector:
    def __init__(self):
        self.worker = SimpleNamespace(state="ready")
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.calls = []
        self.boxes = BOXES
        self.error = None

    def detect(self, image, now, labels=LABELS):
        self.calls.append(now)
        self.started.set()
        if not self.release.wait(3):
            raise TimeoutError("test detector did not release")
        if self.error:
            raise self.error
        return semantic_scene(self.boxes, labels, Settings().vision, now)

    def close(self):
        self.release.set()


def wait_result(scanner):
    with scanner.condition:
        assert scanner.condition.wait_for(lambda: scanner.result is not None, timeout=3)


def test_latest_request_wins_and_only_one_inference_runs():
    detector = ControlledDetector()
    detector.release.clear()
    scanner = LatestScan(detector)
    image = np.zeros((30, 30, 3), dtype=np.uint8)
    try:
        scanner.submit(image, b"first", LABELS, 1)
        assert detector.started.wait(2)
        scanner.submit(image, b"second", LABELS, 2)
        scanner.submit(image, b"third", LABELS, 3)
        detector.release.set()
        wait_result(scanner)
        assert scanner.take_result().request.jpeg == b"third"
        assert detector.calls == [1, 3]
    finally:
        scanner.close()


def test_invalidation_discards_in_flight_result():
    detector = ControlledDetector()
    detector.release.clear()
    scanner = LatestScan(detector)
    try:
        scanner.submit(np.zeros((30,30,3), np.uint8), b"old", LABELS, 1)
        assert detector.started.wait(2)
        scanner.invalidate()
        detector.release.set()
        with scanner.condition:
            assert scanner.condition.wait_for(lambda: not scanner.busy, timeout=2)
        assert scanner.take_result() is None
    finally:
        scanner.close()


@pytest.fixture
def semantic_session():
    s = Session(Settings(semantic_beta=True), persist=False)
    detector = ControlledDetector()
    s.semantic_detector = detector
    s.scanner = LatestScan(detector)
    s.configure_detector("semantic", ",".join(LABELS))
    yield s, detector
    s.close()


def hold(s, start=1, jpeg=None):
    for i in range(8):
        s.process_frame(jpeg or frame(START), now=start + i * .2)


def accept_scan(s, now):
    s.command("scan")
    wait_result(s.scanner)
    return s.process_frame(frame(START), now=now)


def test_manual_only_and_ambiguous_does_not_teach(semantic_session):
    s, detector = semantic_session
    s.command("teach")
    hold(s)
    assert detector.calls == []
    assert s.recorder.initial is None
    accept_scan(s, 3)
    assert s.recorder.initial is not None
    detector.boxes = BOXES[:1]
    snap = accept_scan(s, 4)
    assert snap["detector"]["scanState"] == "ambiguous"
    assert snap["scene"] is None
    assert not s.recorder.steps


def test_no_advance_after_failure_and_explicit_color_switch_preserves_procedure(semantic_session):
    s, detector = semantic_session
    recorder = TeachRecorder(1, 2)
    recorder.on_stable(scene())
    recorder.on_stable(scene([{**BOXES[0], "bbox": [.4,.2,.6,.4]}, BOXES[1]], 1))
    s.procedure = recorder.finish()
    original = s.procedure.to_json()
    s.command("practice")
    hold(s)
    accept_scan(s, 3)
    assert s.practice.state.status == "waiting"
    detector.error = TimeoutError("test timeout")
    snap = accept_scan(s, 4)
    assert snap["detector"]["scanState"] == "error"
    assert s.practice.state.expected_step_index == 0
    s.configure_detector("color")
    assert s.detector_kind == "semantic"
    s.command("pause")
    s.configure_detector("color")
    assert s.procedure.to_json() == original
    snap = s.process_frame(frame(START), now=5)
    assert {o["id"] for o in snap["scene"]["objects"]} == set(START)
    s.command("practice")
    assert s.mode == "idle"  # cannot practice semantic identities using Color mode


def test_forbidden_mid_teaching_reconfiguration(semantic_session):
    s, _ = semantic_session
    s.command("teach")
    s.configure_detector("color")
    assert s.detector_kind == "semantic"
    s.configure_detector("semantic", "cup, phone")
    assert s.semantic_labels == LABELS


def test_motion_discards_boxes_and_does_not_grade(semantic_session):
    s, detector = semantic_session
    s.command("teach")
    hold(s)
    accept_scan(s, 3)
    moved = frame({**START, "red": "C"})
    snap = s.process_frame(moved, now=3.2)
    assert snap["scene"] is None
    assert snap["tracker"]["status"] == "moving"
    hold(s, 4, moved)
    assert len(detector.calls) == 1
    assert not s.recorder.steps


def test_semantic_beta_requires_explicit_configuration():
    s = Session(Settings(semantic_beta=False), persist=False)
    try:
        s.configure_detector("semantic", ",".join(LABELS))
        assert s.detector_kind == "color"
        assert s.semantic_detector.worker.state == "unloaded"
    finally:
        s.close()


def test_saved_semantic_identity_round_trips(semantic_session, tmp_path, monkeypatch):
    from app import session
    s, _ = semantic_session
    monkeypatch.setattr(session, "PROCEDURE_FILE", tmp_path / "procedure.json")
    monkeypatch.setattr(session, "KEYFRAME_DIR", tmp_path / "keyframes")
    monkeypatch.setattr(session, "CALIBRATION_FILE", tmp_path / "calibration.json")
    s.command("teach")
    hold(s)
    accept_scan(s, 3)
    s.persist = True
    s.command("pause")
    restored = Session(Settings(semantic_beta=True))
    try:
        assert restored.detector_kind == "semantic"
        assert set(restored.semantic_labels) == set(LABELS)
        assert all(o.color is None and o.kind == "semantic" for o in restored.procedure.initial_state.objects)
    finally:
        restored.close()


@pytest.mark.parametrize("stage", ["startup", "request"])
def test_worker_deadlines_terminate_process(stage, tmp_path):
    program = "import time; time.sleep(30)"
    if stage == "request":
        program = 'import time; print(\'{"type":"ready"}\', flush=True); time.sleep(30)'
    cfg = Settings(locate_startup_timeout=.1 if stage == "startup" else 2, locate_request_timeout=.1)
    worker = LocateWorker(cfg, command=[sys.executable, "-u", "-c", program])
    try:
        with pytest.raises(TimeoutError):
            worker.predict(tmp_path / "unused.png", LABELS)
        assert worker.state == "error" and worker.process is None
    finally:
        worker.close()


def test_worker_crash_and_model_loading_are_lazy(tmp_path):
    worker = LocateWorker(Settings(), command=[sys.executable, "-c", "raise SystemExit(7)"])
    try:
        assert worker.process is None and worker.state == "unloaded"
        with pytest.raises(RuntimeError, match="worker stopped"):
            worker.predict(tmp_path / "unused.png", LABELS)
        assert worker.state == "error" and worker.process is None
    finally:
        worker.close()
