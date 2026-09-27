"""Setup Check: capture, persistence, deterministic verdicts, and isolation from the saved procedure."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.detectors import LatestScan, semantic_scene
from app.engine import TeachRecorder
from app.models import SceneObject, SceneState
from app.session import Session
from app.setups import JsonSetupRepository, SavedSetup, SetupCheckResult, SetupObject, check_setup

from .test_semantic import BOXES, LABELS, ControlledDetector, hold, wait_result
from .test_session import START, frame

WATCH = {"label": "green smartwatch", "bbox": [.4, .3, .6, .5]}  # zone B


@pytest.fixture
def setup_session(tmp_path):
    repo = JsonSetupRepository(tmp_path / "setups")
    s = Session(Settings(semantic_beta=True), persist=False, setup_repository=repo)
    detector = ControlledDetector()
    s.semantic_detector = detector
    s.scanner = LatestScan(detector)
    s.configure_detector("semantic", ",".join(LABELS))
    s.set_workspace("setup")
    hold(s)
    yield s, detector, repo
    s.close()


def scan(s, now=3):
    s.command("scan")
    wait_result(s.scanner)
    return s.process_frame(frame(START), now=now)


def check(s, now=4):
    s.check_setup()
    wait_result(s.scanner)
    return s.process_frame(frame(START), now=now)


def captured(s, name="Lab Bench"):
    scan(s)
    return s.capture_setup(name)


# -- capture + persistence -------------------------------------------------------------------------


def test_capture_saves_named_setup_and_reloads_from_disk(setup_session, tmp_path):
    s, _, _ = setup_session
    assert not s.setup_status()["canCapture"]  # no accepted scan yet
    snap = captured(s, "  Lab   Bench ")
    assert snap["notice"] == 'Saved setup "Lab Bench" with 2 objects.'
    assert snap["setup"]["selected"]["id"] == "lab-bench"
    assert snap["setup"]["setups"] == [{"id": "lab-bench", "name": "Lab Bench", "objectCount": 2}]
    reloaded = JsonSetupRepository(tmp_path / "setups").get("lab-bench")
    assert [(o.label, o.zone) for o in reloaded.objects] == [("blue bottle", "A"), ("brown wallet", "C")]
    assert not list((tmp_path / "setups").glob("*.tmp"))


def test_capture_requires_a_strict_accepted_scan_and_a_name(setup_session):
    s, detector, repo = setup_session
    assert s.capture_setup("Bench")["notice"].startswith("Scan Objects first")
    detector.boxes = BOXES[:1]
    snap = scan(s)
    assert snap["detector"]["scanState"] == "ambiguous"
    assert s.capture_setup("Bench")["notice"].startswith("Scan Objects first")
    detector.boxes = BOXES
    scan(s, 5)
    assert s.capture_setup(" !! ")["notice"].startswith("Setup not saved")
    assert repo.list() == []


def test_select_saved_setup(setup_session):
    s, _, _ = setup_session
    captured(s, "Bench one")
    captured(s, "Bench two")
    snap = s.select_setup("bench-one")
    assert snap["setup"]["selected"]["name"] == "Bench one"
    assert s.select_setup("missing")["notice"] == "That saved setup is not available."
    assert s.selected_setup_id == "bench-one"


# -- verdicts --------------------------------------------------------------------------------------


def test_complete_and_correctly_arranged(setup_session):
    s, detector, _ = setup_session
    captured(s)
    snap = check(s)
    result = snap["setup"]["result"]
    assert result["status"] == "complete"
    assert [o["label"] for o in result["correct"]] == ["blue bottle", "brown wallet"]
    assert result["missing"] == result["unexpected"] == result["misplaced"] == []
    assert snap["events"][0]["text"] == "Lab Bench is complete and correctly arranged."
    assert detector.labels_seen[-1] == LABELS


def test_missing_object(setup_session):
    s, detector, _ = setup_session
    captured(s)
    detector.boxes = BOXES[:1]
    result = check(s)["setup"]["result"]
    assert result["status"] == "needs_attention"
    assert result["missing"] == [{"label": "brown wallet", "zone": "C"}]


def test_unexpected_object_among_configured_descriptions(setup_session):
    s, detector, _ = setup_session
    captured(s)
    s.configure_detector("semantic", ",".join(LABELS + ("green smartwatch",)))
    hold(s, 10)
    detector.boxes = BOXES + [WATCH]
    result = check(s, 12)["setup"]["result"]
    assert detector.labels_seen[-1] == LABELS + ("green smartwatch",)
    assert result["status"] == "needs_attention"
    assert result["unexpected"] == [{"label": "green smartwatch", "zone": "B"}]
    assert result["missing"] == result["misplaced"] == []


def test_object_in_wrong_zone(setup_session):
    s, detector, _ = setup_session
    captured(s)
    detector.boxes = [BOXES[0], {**BOXES[1], "bbox": [.4, .3, .6, .5]}]
    result = check(s)["setup"]["result"]
    assert result["misplaced"] == [{"label": "brown wallet", "expectedZone": "C", "observedZone": "B"}]
    assert result["status"] == "needs_attention"


def test_pure_checker_is_case_insensitive_and_reports_everything_at_once():
    setup = SavedSetup(id="bench", name="Bench", created_at=0, objects=[
        SetupObject(label="Blue Bottle", zone="A"), SetupObject(label="brown wallet", zone="C"),
        SetupObject(label="green smartwatch", zone="B")])
    scene = semantic_scene([{**BOXES[0], "label": "blue bottle"}, {"label": "blue smartphone", "bbox": [.7, .6, .9, .8]},
                            {**WATCH, "bbox": [.05, .6, .25, .8]}],
                           ("blue bottle", "brown wallet", "green smartwatch", "blue smartphone"),
                           Settings().vision, 1, allow_missing=True)
    r = check_setup(setup, scene, 1)
    assert [o.label for o in r.correct] == ["Blue Bottle"]
    assert [o.label for o in r.missing] == ["brown wallet"]
    assert [(o.label, o.zone) for o in r.unexpected] == [("blue smartphone", "C")]
    assert [(m.label, m.expected_zone, m.observed_zone) for m in r.misplaced] == [("green smartwatch", "B", "A")]


# -- failures never pass ---------------------------------------------------------------------------


def test_semantic_scan_failure_cannot_produce_a_successful_check(setup_session):
    s, detector, _ = setup_session
    captured(s)
    assert check(s)["setup"]["result"]["status"] == "complete"
    detector.error = TimeoutError("worker timed out")
    snap = check(s, 5)
    assert snap["setup"]["result"] is None  # the earlier pass is not left on screen
    assert snap["detector"]["scanState"] == "error"
    detector.error = None
    detector.boxes = BOXES + [{**BOXES[0], "bbox": [.35, .2, .55, .4]}]  # two separate matches → ambiguous
    snap = check(s, 6)
    assert snap["setup"]["result"] is None and snap["detector"]["scanState"] == "ambiguous"
    detector.boxes = []  # nothing seen at all is a failed check, not a pass
    assert check(s, 7)["setup"]["result"]["status"] == "needs_attention"


def test_motion_during_check_discards_it_and_marks_old_result_stale(setup_session):
    s, detector, _ = setup_session
    captured(s)
    check(s)
    detector.release.clear()
    s.check_setup()
    assert detector.started.wait(2)
    moved = frame({**START, "red": "C"})
    s.process_frame(moved, now=4.2)  # hands in: the in-flight scan is invalidated
    detector.release.set()
    hold(s, 5, moved)
    snap = s.snapshot()
    assert snap["setup"]["result"] is None and not snap["setup"]["checking"]


def test_stale_flag_after_table_moves(setup_session):
    s, _, _ = setup_session
    captured(s)
    check(s)
    snap = s.process_frame(frame({**START, "red": "C"}), now=4.2)
    assert snap["setup"]["result"]["status"] == "complete" and snap["setup"]["resultStale"] is True


def test_result_model_rejects_inconsistent_verdict():
    with pytest.raises(ValidationError):
        SetupCheckResult(setup_id="x", setup_name="x", status="complete", checked_at=0,
                         correct=[SetupObject(label="a", zone="A")], missing=[SetupObject(label="b", zone="B")])
    with pytest.raises(ValueError):
        color = SceneObject(id="red", color="red", center=(.2, .2), bbox=(.1, .1, .2, .2), zone="A")
        check_setup(SavedSetup(id="x", name="x", created_at=0, objects=[SetupObject(label="red", zone="A")]),
                    SceneState(objects=[color]), 0)


# -- malformed data --------------------------------------------------------------------------------


def test_malformed_saved_setups_are_skipped_and_reported(tmp_path):
    folder = tmp_path / "setups"
    folder.mkdir()
    good = SavedSetup(id="bench", name="Bench", created_at=0, objects=[SetupObject(label="cup", zone="A")])
    (folder / "bench.json").write_text(json.dumps(good.to_json()))
    (folder / "garbage.json").write_text("{not json")
    dup = {**good.to_json(), "id": "dup", "name": "dup", "objects": [{"label": "cup"}, {"label": "CUP"}]}
    (folder / "dup.json").write_text(json.dumps(dup))
    (folder / "renamed.json").write_text(json.dumps(good.to_json()))  # id does not match file name
    repo = JsonSetupRepository(folder)
    assert [s.id for s in repo.list()] == ["bench"]
    assert sorted(e.split(":")[0] for e in repo.errors) == ["dup.json", "garbage.json", "renamed.json"]
    assert repo.get("../bench") is None and repo.get("garbage") is None
    s = Session(Settings(semantic_beta=True), persist=False, setup_repository=repo)
    try:
        status = s.setup_status()
        assert len(status["repositoryErrors"]) == 3 and status["setups"][0]["id"] == "bench"
        assert s.select_setup("garbage")["notice"] == "That saved setup is not available."
    finally:
        s.close()


# -- isolation from the procedure flow -------------------------------------------------------------


def test_setup_mode_never_changes_the_saved_procedure(setup_session, tmp_path, monkeypatch):
    from app import session as session_module

    s, detector, _ = setup_session
    monkeypatch.setattr(session_module, "PROCEDURE_FILE", tmp_path / "procedure.json")
    monkeypatch.setattr(session_module, "KEYFRAME_DIR", tmp_path / "keyframes")
    recorder = TeachRecorder(1, 2)
    recorder.on_stable(semantic_scene(BOXES, LABELS, Settings().vision, 0))
    recorder.on_stable(semantic_scene([{**BOXES[0], "bbox": [.4, .2, .6, .4]}, BOXES[1]], LABELS, Settings().vision, 1))
    s.procedure = recorder.finish()
    s.persist = True
    s._save()
    before_bytes = (tmp_path / "procedure.json").read_bytes()
    before_json = s.procedure.to_json()

    captured(s)
    detector.boxes = BOXES[:1]
    check(s)
    for action in ("teach", "reset", "practice", "finish", "undo_step", "pause"):
        assert s.command(action)["notice"] == "Switch to Procedure mode to use procedure controls."
    assert s.configure_detector("color")["detector"]["kind"] == "semantic"
    assert s.mode == "idle" and s.recorder is None

    s.set_workspace("procedure")
    assert (tmp_path / "procedure.json").read_bytes() == before_bytes
    assert s.procedure.to_json() == before_json
    assert not (tmp_path / "setups" / "procedure.json").exists()


def test_workspace_switch_guards():
    off = Session(Settings(semantic_beta=False), persist=False)
    try:
        assert "Semantic Objects beta" in off.set_workspace("setup")["notice"]
        assert off.workspace == "procedure"
    finally:
        off.close()
    on = Session(Settings(semantic_beta=True), persist=False)
    try:
        on.command("teach")
        assert on.set_workspace("setup")["notice"].startswith("Pause or finish")
        assert on.workspace == "procedure"
    finally:
        on.close()


def test_websocket_setup_messages_are_wired():
    from app.main import app

    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["workspace"] == "procedure" and "setups" in hello["setup"]
        ws.send_json({"type": "setup_check"})
        assert ws.receive_json()["notice"] == "Switch to Setup Check first."
