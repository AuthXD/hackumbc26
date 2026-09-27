"""Mat stabilization inside the live session: both detectors see the canonical mat, nothing unsafe passes."""

import threading

import numpy as np
import pytest

from app.config import Settings
from app.detectors import LatestScan, semantic_scene
from app.engine import TeachRecorder
from app.session import Session
from app.setups import JsonSetupRepository

from .synthetic_mat import base_homography, frame, jpeg, motion, normalized_landmarks, world
from .test_semantic import BOXES, LABELS, ControlledDetector, wait_result

H0 = base_homography()
COLOR_WORLD = world({"red": (0.5, 0.2), "blue": (0.5, 0.8)})
PLAIN_WORLD = world()


class RecordingDetector(ControlledDetector):
    """Remembers the exact image each scan was given."""

    def __init__(self):
        super().__init__()
        self.images = []

    def detect(self, image, now, labels=LABELS, allow_missing=False):
        self.images.append(image.copy())
        return super().detect(image, now, labels, allow_missing)


def feed(s, world_img, H, frames=6, t0=10.0, source="phone"):
    snap = None
    for i in range(frames):
        snap = s.process_frame(jpeg(frame(world_img, H)), t0 + i * 0.2, source)
    return snap


def calibrate(s, world_img, H=H0, t0=0.0):
    s.process_frame(jpeg(frame(world_img, H)), t0, "phone")
    snap = s.calibrate_mat(normalized_landmarks(H))
    assert snap["notice"].startswith("Mat calibrated"), snap["notice"]
    return snap


def semantic_session(**kw):
    s = Session(Settings(semantic_beta=True), persist=False, **kw)
    detector = RecordingDetector()
    s.semantic_detector, s.scanner = detector, LatestScan(detector)
    s.configure_detector("semantic", ",".join(LABELS))
    return s, detector


def scan(s, world_img, H, t):
    s.command("scan")
    wait_result(s.scanner)
    return s.process_frame(jpeg(frame(world_img, H)), t, "phone")


# -- color path -------------------------------------------------------------------------------------


def test_color_detection_uses_canonical_mat_coordinates_despite_camera_motion():
    s = Session(persist=False)
    calibrate(s, COLOR_WORLD)
    for move in (motion(), motion(tx=30, ty=-25, rot=5), motion(scale=0.92, px=0.0001)):
        snap = feed(s, COLOR_WORLD, move @ H0, frames=8, t0=20)
        assert snap["mat"]["state"] == "tracking" and snap["mat"]["trustworthy"], snap["mat"]["message"]
        assert [z["id"] for z in snap["zones"]] == ["A", "B", "C"]
        assert all(z["x"] == pytest.approx(0.1) for z in snap["zones"])  # canonical rows, not camera columns
        objects = {o["id"]: o for o in snap["scene"]["objects"]}
        assert set(objects) == {"red", "blue"}  # the colored stickers sit in the masked band
        assert (objects["red"]["zone"], objects["blue"]["zone"]) == ("A", "C")
        assert objects["red"]["center"] == pytest.approx([0.5, 0.2], abs=0.02)
        assert snap["mat"]["viewSeq"] > 0 and s.mat_view_jpeg is not None


def test_simulator_bypass_is_unchanged_even_with_a_calibrated_phone():
    s = Session(persist=False)
    calibrate(s, COLOR_WORLD)
    snap = s.process_frame(jpeg(frame(COLOR_WORLD, H0)), 50, "sim")
    assert snap["mat"]["state"] == "off"
    assert [z["y"] for z in snap["zones"]] == [0.12, 0.12, 0.12]  # classic side-by-side zones


def test_color_tracking_loss_blocks_commits_until_steady_again():
    s = Session(persist=False)
    calibrate(s, COLOR_WORLD)
    feed(s, COLOR_WORLD, H0, frames=8)
    s.command("teach")
    snap = feed(s, COLOR_WORLD, H0, frames=8, t0=20)
    assert snap["teach"]["phase"] == "recording"
    lost = s.process_frame(jpeg(np.zeros((1138, 640, 3), np.uint8)), 30, "phone")
    assert lost["mat"]["state"] == "lost" and lost["scene"] is None
    assert lost["tracker"]["status"] == "untracked"
    moved_world = world({"red": (0.5, 0.5), "blue": (0.5, 0.8)})
    snap = s.process_frame(jpeg(frame(moved_world, H0)), 30.2, "phone")  # first frame back: not trusted yet
    assert not snap["mat"]["trustworthy"] and s.recorder.steps == []


# -- semantic path --------------------------------------------------------------------------------------


def test_semantic_scans_receive_the_masked_canonical_mat_and_canonical_zones():
    s, detector = semantic_session()
    try:
        calibrate(s, PLAIN_WORLD)
        feed(s, PLAIN_WORLD, H0, frames=8)
        snap = scan(s, PLAIN_WORLD, H0, 12)
        assert snap["detector"]["scanState"] == "valid", snap["detector"]["message"]
        image = detector.images[-1]
        cal = s.mat.calibration("phone")
        assert image.shape[:2] == (cal.canonical_height, cal.canonical_width)
        band = round(cal.canonical_height * 0.1)
        assert image[:band].max() == 0 and image[-band:].max() == 0  # landmark band blacked out
        zones = {o["id"]: o["zone"] for o in snap["scene"]["objects"]}
        assert zones == {"blue bottle": "A", "brown wallet": "B"}  # re-zoned in canonical coordinates
    finally:
        s.close()


def test_object_on_the_landmark_band_is_ignored():
    s, detector = semantic_session()
    try:
        calibrate(s, PLAIN_WORLD)
        feed(s, PLAIN_WORLD, H0, frames=8)
        detector.boxes = [{"label": LABELS[0], "bbox": [0.0, 0.2, 0.08, 0.4]}, BOXES[1]]  # centre x = 0.04
        snap = scan(s, PLAIN_WORLD, H0, 12)
        assert snap["detector"]["scanState"] == "ambiguous" and "edge band" in snap["detector"]["message"]
        assert snap["scene"] is None
    finally:
        s.close()


def wait_idle(scanner):
    with scanner.condition:
        assert scanner.condition.wait_for(lambda: not scanner.busy and scanner.pending is None, timeout=3)


def test_fast_camera_motion_cancels_an_in_flight_scan():
    s, detector = semantic_session()
    try:
        calibrate(s, PLAIN_WORLD)
        feed(s, PLAIN_WORLD, H0, frames=8)
        detector.release.clear()
        s.command("scan")
        assert detector.started.wait(2)
        snap = s.process_frame(jpeg(frame(PLAIN_WORLD, motion(tx=8) @ H0)), 11.6, "phone")  # a quick move
        assert not snap["mat"]["trustworthy"] and snap["detector"]["scanState"] == "idle"
        detector.release.set()
        wait_idle(s.scanner)
        snap = feed(s, PLAIN_WORLD, motion(tx=8) @ H0, frames=6, t0=11.8)
        assert snap["scene"] is None and snap["detector"]["scanState"] != "valid"  # the old result never lands
    finally:
        s.close()


def test_slow_camera_creep_during_a_scan_voids_the_result():
    """Every frame looks steady and trusted, but the camera drifted > 1% of the diagonal during inference."""
    s, detector = semantic_session()
    try:
        calibrate(s, PLAIN_WORLD)
        feed(s, PLAIN_WORLD, H0, frames=8)
        detector.release.clear()
        s.command("scan")
        assert detector.started.wait(2)
        for i in range(1, 16):  # 1.5 px per frame: under the steadiness limit, 22 px in total
            snap = s.process_frame(jpeg(frame(PLAIN_WORLD, motion(tx=1.5 * i) @ H0)), 11.4 + i * 0.2, "phone")
            assert snap["mat"]["state"] in ("tracking", "unsteady")
        assert snap["mat"]["trustworthy"]
        detector.release.set()
        wait_result(s.scanner)
        snap = s.process_frame(jpeg(frame(PLAIN_WORLD, motion(tx=22.5) @ H0)), 14.6, "phone")
        assert snap["scene"] is None and snap["detector"]["scanState"] != "valid"
        assert "camera moved" in snap["detector"]["message"]
    finally:
        s.close()


def test_tracking_loss_stales_a_passing_setup_check_and_blocks_new_verdicts(tmp_path):
    s, detector = semantic_session(setup_repository=JsonSetupRepository(tmp_path / "setups"))
    try:
        s.set_workspace("setup")
        calibrate(s, PLAIN_WORLD)
        feed(s, PLAIN_WORLD, H0, frames=8)
        scan(s, PLAIN_WORLD, H0, 12)
        assert s.capture_setup("Lab Bench")["notice"].startswith("Saved setup")
        s.check_setup()
        wait_result(s.scanner)
        snap = s.process_frame(jpeg(frame(PLAIN_WORLD, H0)), 13, "phone")
        assert snap["setup"]["result"]["status"] == "complete" and not snap["setup"]["resultStale"]

        lost = s.process_frame(jpeg(np.zeros((1138, 640, 3), np.uint8)), 14, "phone")
        assert lost["setup"]["resultStale"] is True  # the pass is no longer current
        assert lost["setup"]["canCheck"] is False and lost["detector"]["canScan"] is False
        assert lost["mat"]["state"] == "lost"
    finally:
        s.close()


def test_detector_error_gives_no_verdict_on_the_canonical_path():
    s, detector = semantic_session()
    try:
        calibrate(s, PLAIN_WORLD)
        feed(s, PLAIN_WORLD, H0, frames=8)
        detector.error = TimeoutError("worker timed out")
        snap = scan(s, PLAIN_WORLD, H0, 12)
        assert snap["detector"]["scanState"] == "error" and snap["scene"] is None
    finally:
        s.close()


# -- calibration safety --------------------------------------------------------------------------------


def test_calibration_never_touches_procedure_or_setups(tmp_path, monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(session_module, "PROCEDURE_FILE", tmp_path / "procedure.json")
    monkeypatch.setattr(session_module, "KEYFRAME_DIR", tmp_path / "keyframes")
    monkeypatch.setattr(session_module, "CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(session_module, "MAT_DIR", tmp_path / "mat")
    setups = JsonSetupRepository(tmp_path / "setups")
    s = Session(Settings(), persist=True, setup_repository=setups)
    try:
        recorder = TeachRecorder(1, 2)
        recorder.on_stable(semantic_scene(BOXES, LABELS, Settings().vision, 0))
        recorder.on_stable(semantic_scene([{**BOXES[0], "bbox": [.4, .2, .6, .4]}, BOXES[1]], LABELS, Settings().vision, 1))
        s.procedure = recorder.finish()
        s._save()
        before = (tmp_path / "procedure.json").read_bytes()
        calibrate(s, PLAIN_WORLD)
        feed(s, PLAIN_WORLD, H0, frames=4)
        s.clear_mat()
        calibrate(s, PLAIN_WORLD, t0=5)
        assert (tmp_path / "procedure.json").read_bytes() == before
        assert not (tmp_path / "setups").exists()
        assert sorted(p.name for p in (tmp_path / "mat").iterdir()) == ["phone.json", "phone.png"]
    finally:
        s.close()


def test_malformed_calibration_is_a_safe_state_never_a_pass(tmp_path, monkeypatch):
    from app import session as session_module

    folder = tmp_path / "mat"
    folder.mkdir()
    (folder / "phone.json").write_text('{"version": 1, "source": "phone"}')
    monkeypatch.setattr(session_module, "MAT_DIR", folder)
    monkeypatch.setattr(session_module, "PROCEDURE_FILE", tmp_path / "procedure.json")
    monkeypatch.setattr(session_module, "CALIBRATION_FILE", tmp_path / "calibration.json")
    s = Session(Settings(semantic_beta=True), persist=True, setup_repository=JsonSetupRepository(tmp_path / "s"))
    try:
        snap = feed(s, COLOR_WORLD, H0, frames=4)
        assert snap["mat"]["state"] == "recalibrate" and "Recalibrate" in snap["mat"]["message"]
        assert snap["scene"] is None and snap["mat"]["trustworthy"] is False
        s.configure_detector("semantic", ",".join(LABELS))
        snap = feed(s, COLOR_WORLD, H0, frames=6, t0=20)
        assert snap["detector"]["canScan"] is False
    finally:
        s.close()


def test_calibration_command_guards():
    s = Session(persist=False)
    try:
        assert s.calibrate_mat(normalized_landmarks(H0))["notice"].startswith("No camera picture")
        s.process_frame(jpeg(frame(PLAIN_WORLD, H0)), 0, "sim")
        assert "simulator" in s.calibrate_mat(normalized_landmarks(H0))["notice"]
        s.process_frame(jpeg(frame(PLAIN_WORLD, H0)), 0.2, "phone")
        crossed = [normalized_landmarks(H0)[i] for i in (0, 2, 1, 3)]
        snap = s.calibrate_mat(crossed)
        assert snap["notice"].startswith("Mat not calibrated") and snap["mat"]["calibrated"] is False
        assert s.calibrate_mat("nonsense")["notice"].startswith("Mat not calibrated")
    finally:
        s.close()


def test_scan_submitted_before_loss_is_rejected_even_if_tracking_returns():
    s, detector = semantic_session()
    try:
        calibrate(s, PLAIN_WORLD)
        feed(s, PLAIN_WORLD, H0, frames=8)
        detector.release.clear()
        s.command("scan")
        assert detector.started.wait(2)
        s.process_frame(jpeg(np.zeros((1138, 640, 3), np.uint8)), 12, "phone")  # tracking lost mid-scan
        release = threading.Timer(0.1, detector.release.set)
        release.start()
        snap = feed(s, PLAIN_WORLD, H0, frames=8, t0=13)
        release.join()
        assert snap["scene"] is None and snap["detector"]["scanState"] != "valid"
    finally:
        s.close()
