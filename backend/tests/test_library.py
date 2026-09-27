"""Procedure Library: named procedures saved locally or in Tiger Data, loaded back for deterministic Practice."""

import json
import time

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.detectors import LatestScan, semantic_scene
from app.engine import TeachRecorder
from app.library import (
    JsonProcedureRepository,
    ProcedureStorageError,
    SavedProcedure,
    create_procedure_repository,
    fallback_metadata,
)
from app.session import Session
from app.setups import JsonSetupRepository
from app.tiger import SELECT_PROCEDURES_SQL, UPSERT_PROCEDURE_SQL, TigerProcedureRepository

from .fake_tiger import SECRET_URL, SECRETS, FakeTiger, leaky_error
from .helpers import layout, moved
from .test_semantic import BOXES, LABELS, ControlledDetector
from .test_session import START, STEPS, Driver, frame

TIGER = Settings(tiger_database_url=SECRET_URL)


def color_procedure(steps=(dict(red="B"), dict(blue="C"))):
    rec = TeachRecorder(len(steps), 2)
    state = layout(red="A", blue="A", green="C")
    rec.on_stable(state, "/api/keyframes/start000000.jpg")
    for i, change in enumerate(steps):
        state = layout(**moved(state, **change))
        rec.on_stable(state, f"/api/keyframes/step{i:08d}.jpg")
    return rec.finish()


def semantic_procedure():
    rec = TeachRecorder(1, 2)
    rec.on_stable(semantic_scene(BOXES, LABELS, Settings().vision, 0))
    rec.on_stable(semantic_scene([{**BOXES[0], "bbox": [.4, .2, .6, .4]}, BOXES[1]], LABELS, Settings().vision, 1))
    return rec.finish()


def saved(name="Kitchen Prep", proc=None, **kw) -> SavedProcedure:
    now = kw.pop("now", 100.0)
    from app.library import procedure_id_for
    return SavedProcedure(id=procedure_id_for(name), name=name, procedure=proc or color_procedure(),
                          created_at=now, updated_at=now, **kw)


# -- domain ------------------------------------------------------------------------------------------


def test_saved_procedure_validation():
    good = saved("  Kitchen   Prep ", summary="  Two   steps. ", tags=("Color",))
    assert (good.id, good.name, good.summary, good.tags) == ("kitchen-prep", "Kitchen Prep", "Two steps.", ("color",))
    assert good.card()["stepCount"] == 2 and good.card()["objectCount"] == 3 and good.card()["detectorKind"] == "color"
    bad = [
        dict(id="other", name="Kitchen Prep"),
        dict(name="x" * 61),
        dict(tags=("a", "b", "c", "d")),
        dict(tags=("<script>",)),
        dict(tags=("same", "Same")),
        dict(summary="s" * 201),
        dict(version=2),
    ]
    base = saved().to_json()
    for change in bad:
        data = {**base, **{k: v for k, v in change.items()}}
        if "id" not in change and "name" in change:
            data["id"] = base["id"]
        with pytest.raises(ValidationError):
            SavedProcedure.model_validate(data)
    empty = color_procedure()
    empty.steps = []
    with pytest.raises(ValidationError):
        saved(proc=empty)
    with pytest.raises(ValidationError):
        SavedProcedure.model_validate({**base, "updatedAt": 1.0})  # updated before created


def test_fallback_metadata_is_deterministic_and_valid():
    meta = fallback_metadata(color_procedure())
    assert meta.name == "Two-step color-block procedure"
    assert meta.summary == "2 ordered steps using blue, green, red." and meta.tags == ("color",)
    assert fallback_metadata(semantic_procedure()).name == "One-step object procedure"


# -- local repository ----------------------------------------------------------------------------------


def test_json_repository_round_trip_upsert_and_malformed_files(tmp_path):
    folder = tmp_path / "procedures"
    repo = JsonProcedureRepository(folder)
    first = repo.save(saved(now=100.0))
    again = repo.save(saved(now=200.0, summary="edited"))
    assert again.created_at == 100.0 and again.updated_at == 200.0  # upsert keeps the creation time
    reloaded = JsonProcedureRepository(folder)
    assert [p.id for p in reloaded.list()] == ["kitchen-prep"]
    assert reloaded.get("kitchen-prep").summary == "edited"
    assert [s.delta for s in reloaded.get("kitchen-prep").procedure.steps] == \
        [s.delta for s in first.procedure.steps]
    (folder / "garbage.json").write_text("{not json")
    (folder / "renamed.json").write_text(json.dumps(first.to_json()))
    (folder / "future.json").write_text(json.dumps({**first.to_json(), "id": "future", "name": "future", "version": 2}))
    repo.refresh()
    assert [p.id for p in repo.list()] == ["kitchen-prep"]
    assert sorted(e.split(":")[0] for e in repo.errors) == ["future.json", "garbage.json", "renamed.json"]
    assert repo.get("../kitchen-prep") is None and repo.status().provider == "local"


# -- Tiger repository ------------------------------------------------------------------------------


def test_repository_selection(tmp_path):
    assert isinstance(create_procedure_repository(Settings(tiger_database_url=""), tmp_path), JsonProcedureRepository)
    db = FakeTiger().migrate_all()
    repo = create_procedure_repository(TIGER, tmp_path, connect=db.connect)
    assert isinstance(repo, TigerProcedureRepository) and repo.status().state == "ready"
    assert db.count(SELECT_PROCEDURES_SQL) == 1


def test_tiger_save_is_a_parameterized_upsert_and_reads_come_from_cache(tmp_path):
    db = FakeTiger().migrate_all()
    repo = create_procedure_repository(TIGER, tmp_path, connect=db.connect)
    first = repo.save(saved(summary="Bob's kitchen; DROP TABLE x"))
    sql, params = [(s, p) for s, p in db.executed if s == UPSERT_PROCEDURE_SQL][0]
    assert "ON CONFLICT (id) DO UPDATE" in sql and "Bob" not in sql and params[2].startswith("Bob's")
    assert params[3] == [] and params[4:7] == ("color", 2, 3)
    time.sleep(0.01)
    second = repo.save(saved(summary="edited", now=500.0))
    assert list(db.procedures) == ["kitchen-prep"] and second.created_at == first.created_at
    assert repo.get("kitchen-prep").summary == "edited"
    selects = db.count(SELECT_PROCEDURES_SQL)
    for _ in range(20):
        repo.list(), repo.get("kitchen-prep")
    assert db.count(SELECT_PROCEDURES_SQL) == selects  # cache only


def test_tiger_malformed_rows_are_skipped_and_reported(tmp_path):
    db = FakeTiger().migrate_all()
    repo = create_procedure_repository(TIGER, tmp_path, connect=db.connect)
    good = repo.save(saved())
    row = db.procedures["kitchen-prep"]
    db.procedures["liar"] = ("liar", "liar", "", [], "semantic", 2, 3, row[7], False, row[9], row[10])  # columns lie
    broken = json.loads(json.dumps(row[7]))
    broken["steps"][0]["index"] = 5
    db.procedures["broken"] = ("broken", "broken", "", [], "color", 2, 3, broken, False, row[9], row[10])
    db.procedures["garbage"] = ("garbage", "garbage", "", [], "color", 2, 3, {"nope": 1}, False, row[9], row[10])
    repo.refresh()
    assert [p.id for p in repo.list()] == [good.id]
    assert sorted(e.split(":")[0] for e in repo.errors) == ["row 'broken'", "row 'garbage'", "row 'liar'"]


def test_tiger_outage_fails_honestly_without_local_fallback(tmp_path, caplog):
    db = FakeTiger()
    db.connect_error = leaky_error()
    repo = create_procedure_repository(TIGER, tmp_path / "procedures", connect=db.connect)
    assert repo.status().state == "error" and "OperationalError" in repo.status().message
    with pytest.raises(ProcedureStorageError):
        repo.save(saved())
    assert not (tmp_path / "procedures").exists()
    assert all(secret not in caplog.text + repo.status().message for secret in SECRETS)


def test_missing_table_points_to_the_check_command(tmp_path):
    repo = create_procedure_repository(TIGER, tmp_path, connect=FakeTiger().connect)
    assert repo.status().state == "error" and "npm run tiger:check" in repo.status().message


# -- session: teach -> finish -> name -> save -> load -> practice ---------------------------------------


def taught_driver(repo=None) -> Driver:
    d = Driver()
    if repo is not None:
        d.s.procedures = repo
    d.s.command("teach")
    d.show(frame(START), 1.6)
    layout_now = START
    for step in STEPS:
        layout_now = d.move(layout_now, **step)
    return d


def test_full_round_trip_through_real_frames(tmp_path):
    repo = JsonProcedureRepository(tmp_path / "procedures")
    d = taught_driver(repo)
    snap = d.s.snapshot()
    draft = snap["library"]["draft"]
    assert snap["mode"] == "idle" and draft["available"] and not draft["saved"]
    assert draft["suggestion"]["name"] == "Four-step color-block procedure"
    learned = json.dumps(d.s.procedure.to_json(), sort_keys=True)

    revision = snap["library"]["revision"]
    snap = d.s.save_procedure("Kitchen Prep")
    assert snap["notice"] == 'Saved procedure "Kitchen Prep" to this computer.'
    assert snap["library"]["revision"] == revision + 1  # the page's "Saving…" ends on this reply
    assert snap["library"]["loadedId"] == "kitchen-prep" and snap["library"]["draft"]["saved"]
    assert json.dumps(d.s.procedure.to_json(), sort_keys=True) == learned  # saving never changes it
    card = snap["library"]["procedures"][0]
    assert (card["name"], card["stepCount"], card["objectCount"], card["detectorKind"]) == ("Kitchen Prep", 4, 4, "color")

    assert d.s.save_procedure(" kitchen   prep ")["notice"].startswith('Updated saved procedure "kitchen prep"')
    assert len(repo.list()) == 1

    snap = d.s.command("reset")
    assert snap["procedure"] is None and snap["library"]["loadedId"] is None
    assert [p["id"] for p in snap["library"]["procedures"]] == ["kitchen-prep"]  # reset keeps the library

    snap = d.s.load_procedure("kitchen-prep")
    assert snap["notice"].startswith('Loaded "kitchen prep"') and snap["library"]["loadedId"] == "kitchen-prep"
    assert json.dumps(d.s.procedure.to_json(), sort_keys=True) == learned
    d.s.command("practice")
    d.show(frame(START), 1.6)
    layout_now = START
    for step in STEPS:
        layout_now = d.move(layout_now, **step)
    assert d.last["practice"]["status"] == "complete"  # deterministic practice on the loaded procedure


def test_missing_thumbnails_do_not_break_loading_or_practice(tmp_path, monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(session_module, "PROCEDURE_FILE", tmp_path / "procedure.json")
    monkeypatch.setattr(session_module, "KEYFRAME_DIR", tmp_path / "keyframes")
    monkeypatch.setattr(session_module, "CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(session_module, "MAT_DIR", tmp_path / "mat")
    repo = JsonProcedureRepository(tmp_path / "procedures")
    repo.save(saved())  # its keyframe URLs point at files that do not exist
    s = Session(Settings(), persist=True, setup_repository=JsonSetupRepository(tmp_path / "setups"),
                procedure_repository=repo)
    try:
        snap = s.load_procedure("kitchen-prep")
        assert snap["procedure"]["steps"][0]["afterImage"].endswith(".jpg")
        assert s.keyframe("step00000000") is None  # /api/keyframes answers 404; the page hides the image
        assert s.command("practice")["mode"] == "practicing"
        assert (tmp_path / "procedure.json").exists()
    finally:
        s.close()


def test_reset_keeps_library_keyframes_on_disk(tmp_path, monkeypatch):
    from app import session as session_module

    keyframes = tmp_path / "keyframes"
    keyframes.mkdir()
    for key in ("start000000", "step00000000", "other"):
        (keyframes / f"{key}.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 200)
    monkeypatch.setattr(session_module, "PROCEDURE_FILE", tmp_path / "procedure.json")
    monkeypatch.setattr(session_module, "KEYFRAME_DIR", keyframes)
    monkeypatch.setattr(session_module, "CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(session_module, "MAT_DIR", tmp_path / "mat")
    repo = JsonProcedureRepository(tmp_path / "procedures")
    repo.save(saved())
    s = Session(Settings(), persist=True, setup_repository=JsonSetupRepository(tmp_path / "setups"),
                procedure_repository=repo)
    try:
        s.command("reset")
        assert sorted(p.stem for p in keyframes.iterdir()) == ["start000000", "step00000000"]
        s.load_procedure("kitchen-prep")
        assert s.keyframe("step00000000") is not None  # restored from disk for the thumbnail
    finally:
        s.close()


def test_loading_restores_detector_kind_and_tracked_objects(tmp_path):
    repo = JsonProcedureRepository(tmp_path / "procedures")
    repo.save(saved("Desk Check", proc=semantic_procedure()))
    repo.save(saved("Blocks", proc=color_procedure()))

    s = Session(Settings(semantic_beta=True), persist=False, procedure_repository=repo)
    detector = ControlledDetector()
    s.semantic_detector, s.scanner = detector, LatestScan(detector)
    try:
        assert s.detector_kind == "color"
        snap = s.load_procedure("desk-check")
        assert s.detector_kind == "semantic" and set(s.semantic_labels) == set(LABELS)
        assert snap["detector"]["procedureKind"] == "semantic" and snap["notice"].startswith('Loaded "Desk Check".')
        snap = s.load_procedure("blocks")
        assert s.detector_kind == "color" and snap["procedure"]["detectorKind"] == "color"
    finally:
        s.close()

    off = Session(Settings(semantic_beta=False), persist=False, procedure_repository=repo)
    try:
        snap = off.load_procedure("desk-check")
        assert off.procedure is not None and off.detector_kind == "color"  # preserved, not practiced wrongly
        assert "TEACHBACK_SEMANTIC_BETA=1" in snap["notice"]
        assert off.command("practice")["mode"] == "idle"
    finally:
        off.close()


def test_load_and_save_guards():
    d = taught_driver()
    s = d.s
    assert s.load_procedure("missing")["notice"] == "That saved procedure is not available."
    s.command("teach")
    assert s.save_procedure("Anything")["notice"] == "Finish teaching before saving the procedure."
    assert s.load_procedure("anything")["notice"].startswith("Stop teaching")
    s.command("reset")
    assert s.save_procedure("Anything")["notice"] == "Teach a procedure first, then save it."


def test_storage_outage_refuses_the_save_and_keeps_the_procedure(tmp_path):
    down = FakeTiger()
    down.connect_error = leaky_error()
    repo = create_procedure_repository(TIGER, tmp_path, connect=down.connect)
    d = taught_driver(repo)
    snap = d.s.save_procedure("Kitchen Prep")
    assert snap["notice"].startswith("Procedure not saved: Tiger Data unavailable")
    assert snap["library"]["storage"]["state"] == "error" and snap["library"]["loadedId"] is None
    assert d.s.procedure is not None and d.s.command("practice")["mode"] == "practicing"
    assert all(secret not in json.dumps(snap) for secret in SECRETS)


def test_save_failure_mid_flight_is_reported(tmp_path):
    db = FakeTiger().migrate_all()
    repo = create_procedure_repository(TIGER, tmp_path, connect=db.connect)
    d = taught_driver(repo)
    db.fail_on = ("INSERT INTO teachback_procedures", leaky_error())
    snap = d.s.save_procedure("Kitchen Prep")
    assert snap["notice"] == "Procedure not saved: Tiger Data is unavailable, so the procedure was not saved."
    assert db.procedures == {} and snap["library"]["loadedId"] is None


def test_library_is_isolated_from_setup_check_procedure_json_and_mat(tmp_path, monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(session_module, "PROCEDURE_FILE", tmp_path / "procedure.json")
    monkeypatch.setattr(session_module, "KEYFRAME_DIR", tmp_path / "keyframes")
    monkeypatch.setattr(session_module, "CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(session_module, "MAT_DIR", tmp_path / "mat")
    setups = JsonSetupRepository(tmp_path / "setups")
    repo = JsonProcedureRepository(tmp_path / "procedures")
    s = Session(Settings(semantic_beta=False), persist=True, setup_repository=setups, procedure_repository=repo)
    try:
        s.procedure = color_procedure()
        s._save()
        s.save_procedure("Kitchen Prep")
        writer_before = s.history_writer.stats()
        s.procedure = color_procedure((dict(green="A"),))
        s._save()
        s.load_procedure("kitchen-prep")
        assert json.loads((tmp_path / "procedure.json").read_text()) == repo.get("kitchen-prep").procedure.to_json()
        assert not (tmp_path / "setups").exists() and setups.list() == []
        assert s.history_writer.stats() == writer_before
        assert not (tmp_path / "mat").exists() and s.mat.calibration("phone") is None
        assert s.frame_source == "webcam"
        assert sorted(p.name for p in (tmp_path / "procedures").iterdir()) == ["kitchen-prep.json"]
        s.workspace = "setup"  # Setup Check owns its own scene: the library refuses to load into it
        assert s.load_procedure("kitchen-prep")["notice"] == "Switch to Procedure mode to load a procedure."
        assert s.save_procedure("Other")["notice"] == "Switch to Procedure mode to save a procedure."
        assert [p.id for p in repo.list()] == ["kitchen-prep"]
    finally:
        s.close()


def test_existing_version_one_procedure_json_still_loads(tmp_path, monkeypatch):
    from app import session as session_module

    (tmp_path / "procedure.json").write_text(json.dumps(color_procedure().to_json()))  # plain pre-library file
    monkeypatch.setattr(session_module, "PROCEDURE_FILE", tmp_path / "procedure.json")
    monkeypatch.setattr(session_module, "KEYFRAME_DIR", tmp_path / "keyframes")
    monkeypatch.setattr(session_module, "CALIBRATION_FILE", tmp_path / "calibration.json")
    monkeypatch.setattr(session_module, "MAT_DIR", tmp_path / "mat")
    s = Session(Settings(), persist=True, setup_repository=JsonSetupRepository(tmp_path / "setups"),
                procedure_repository=JsonProcedureRepository(tmp_path / "procedures"))
    try:
        snap = s.snapshot()
        assert snap["procedure"]["detectorKind"] == "color" and snap["library"]["procedures"] == []
        assert snap["library"]["draft"]["available"] and not snap["library"]["draft"]["saved"]
    finally:
        s.close()
