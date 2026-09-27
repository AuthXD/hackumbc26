"""Tiger Data (PostgreSQL) persistence for saved setups, verified offline against a psycopg fake."""

import json
import logging
import re
import threading
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import tiger_check
from app import tiger
from app.config import Settings
from app.detectors import LatestScan, semantic_scene
from app.engine import TeachRecorder
from app.session import SETUP_DIR, Session
from app.setups import SETUP_ID, JsonSetupRepository, SavedSetup, SetupObject, create_setup_repository
from app.tiger import (
    SELECT_SETUPS_SQL,
    UPSERT_SETUP_SQL,
    TigerSetupRepository,
    migration_statements,
    tls_conninfo,
)

from .fake_tiger import SECRET_URL, SECRETS, FakeTiger, leaky_error
from .test_semantic import BOXES, LABELS, ControlledDetector, hold, wait_result
from .test_session import START, frame


def assert_no_secrets(*texts):
    for text in texts:
        for secret in SECRETS:
            assert secret not in text, f"credential leaked: {secret!r}"


def migrated() -> FakeTiger:
    db = FakeTiger()
    db.table_exists = True
    return db


def tiger_repo(db: FakeTiger) -> TigerSetupRepository:
    return create_setup_repository(Settings(tiger_database_url=SECRET_URL), SETUP_DIR, connect=db.connect)


def row(setup_id, name, objects, created=datetime(2026, 9, 26, tzinfo=timezone.utc)):
    return (setup_id, name, objects, created)


BENCH = [{"label": "blue bottle", "zone": "A"}, {"label": "brown wallet", "zone": "C"}]


@pytest.fixture
def tiger_session(tmp_path):
    db = migrated()
    repo = tiger_repo(db)
    s = Session(Settings(semantic_beta=True, tiger_database_url=SECRET_URL), persist=False, setup_repository=repo)
    detector = ControlledDetector()
    s.semantic_detector = detector
    s.scanner = LatestScan(detector)
    s.configure_detector("semantic", ",".join(LABELS))
    s.set_workspace("setup")
    hold(s)
    yield s, db, detector
    s.close()


def scan(s, now=3):
    s.command("scan")
    wait_result(s.scanner)
    return s.process_frame(frame(START), now=now)


# -- selection -------------------------------------------------------------------------------------


def test_repository_selection_with_and_without_tiger_url(tmp_path):
    local = create_setup_repository(Settings(tiger_database_url=""), tmp_path / "setups")
    assert isinstance(local, JsonSetupRepository)
    assert local.status().to_json() == {"provider": "local", "state": "ready",
                                        "message": "Saved as local JSON on this computer."}
    db = migrated()
    remote = tiger_repo(db)
    assert isinstance(remote, TigerSetupRepository)
    assert remote.status().provider == "tiger" and remote.status().state == "ready"
    assert db.count(SELECT_SETUPS_SQL) == 1  # loaded once at startup
    # The default app configuration in tests (no TIGER_DATABASE_URL) keeps local JSON.
    s = Session(persist=False)
    try:
        assert isinstance(s.setups, JsonSetupRepository)
        assert s.snapshot()["setup"]["storage"]["provider"] == "local"
    finally:
        s.close()


# -- schema ----------------------------------------------------------------------------------------


def test_migration_is_an_idempotent_plain_table():
    statements = migration_statements()
    assert [s.splitlines()[0] for s in statements] == [
        "CREATE TABLE IF NOT EXISTS teachback_setups (", "COMMENT ON TABLE teachback_setups IS"]
    text = tiger.MIGRATION_FILE.read_text(encoding="utf-8")
    assert "create_hypertable" not in text.lower()
    for column in ("id TEXT PRIMARY KEY", "name TEXT NOT NULL", "objects JSONB NOT NULL",
                   "created_at TIMESTAMPTZ NOT NULL", "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"):
        assert column in statements[0]
    assert statements[0].count("CHECK (") == 3
    db = FakeTiger()
    for _ in range(3):  # applying repeatedly never fails and never drops data
        with db.connect(SECRET_URL, 5) as conn:
            tiger.apply_migration(conn)
    assert db.table_exists


def test_sql_slug_constraint_matches_python_validation():
    sql_regex = re.search(r"id ~ '([^']+)'", migration_statements()[0]).group(1)
    for candidate in ("lab-bench", "a", "bench-2", "Lab-bench", "lab--bench", "-lab", "lab-", "lab bench", "ünï"):
        assert bool(re.fullmatch(sql_regex, candidate)) == bool(SETUP_ID.match(candidate)), candidate


# -- writes ----------------------------------------------------------------------------------------


def test_save_is_a_parameterized_upsert():
    db = migrated()
    repo = tiger_repo(db)
    name = "Bob's bench; DROP TABLE x"
    first = SavedSetup(id="bob-s-bench-drop-table-x", name=name, created_at=100.0,
                       objects=[SetupObject(label="cup", zone="A")])
    repo.save(first)
    sql, params = [(s, p) for s, p in db.executed if s == UPSERT_SETUP_SQL][0]
    assert "ON CONFLICT (id) DO UPDATE" in sql and name not in sql and "cup" not in sql
    assert params[0] == first.id and params[1] == name
    assert isinstance(params[2], Jsonb) and params[2].obj == [{"label": "cup", "zone": "A"}]
    assert params[3] == datetime.fromtimestamp(100.0, tz=timezone.utc)
    again = first.model_copy(update={"objects": [SetupObject(label="cup", zone="B")], "created_at": 200.0})
    repo.save(again)
    assert list(db.rows) == [first.id]  # one row, updated in place
    assert db.rows[first.id][2] == [{"label": "cup", "zone": "B"}]
    stored = repo.get(first.id)
    assert stored.objects[0].zone == "B" and stored.created_at == 100.0  # cache holds what Tiger returned


# -- reads -----------------------------------------------------------------------------------------


def test_valid_rows_become_saved_setups_and_malformed_rows_are_reported():
    db = migrated()
    db.rows = {
        "lab-bench": row("lab-bench", "Lab Bench", BENCH),
        "tray": row("tray", "Tray", json.dumps([{"label": "scalpel", "zone": None}])),  # JSON text column
        "renamed": row("renamed", "Other Name", BENCH),  # id not derived from name
        "dups": row("dups", "dups", [{"label": "cup"}, {"label": "CUP"}]),
        "notlist": row("notlist", "notlist", {"label": "cup"}),
        "noname": row("noname", None, BENCH),
        "short": ("short", "short"),  # wrong column count
    }
    repo = tiger_repo(db)
    assert [s.id for s in repo.list()] == ["lab-bench", "tray"]
    assert isinstance(repo.get("lab-bench"), SavedSetup)
    assert repo.get("tray").objects[0].zone is None
    assert repo.get("lab-bench").created_at == datetime(2026, 9, 26, tzinfo=timezone.utc).timestamp()
    reported = sorted(e.split(":")[0] for e in repo.errors)
    assert reported == ["row 'dups'", "row 'noname'", "row 'notlist'", "row 'renamed'", "row 'short'"]
    assert repo.status().state == "ready"


def test_list_and_get_use_the_cache_not_one_query_per_snapshot(tiger_session):
    s, db, _ = tiger_session
    selects, connects = db.count(SELECT_SETUPS_SQL), db.connects
    for i in range(20):
        s.process_frame(frame(START), now=10 + i * 0.2)
        s.snapshot()
        s.setups.get("anything")
    assert db.count(SELECT_SETUPS_SQL) == selects == 1
    assert db.connects == connects
    s.refresh_setups()  # explicit refresh is the controlled boundary
    assert db.count(SELECT_SETUPS_SQL) == 2


# -- failures --------------------------------------------------------------------------------------


def test_outage_is_visible_and_never_falls_back_to_local_json(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    db = FakeTiger()
    db.connect_error = leaky_error()
    repo = create_setup_repository(Settings(tiger_database_url=SECRET_URL), tmp_path / "setups", connect=db.connect)
    assert isinstance(repo, TigerSetupRepository)
    status = repo.status()
    assert status.provider == "tiger" and status.state == "error"
    assert "Tiger Data unavailable" in status.message and "OperationalError" in status.message
    s = Session(Settings(semantic_beta=True, tiger_database_url=SECRET_URL), persist=False, setup_repository=repo)
    try:
        detector = ControlledDetector()
        s.semantic_detector, s.scanner = detector, LatestScan(detector)
        s.configure_detector("semantic", ",".join(LABELS))
        s.set_workspace("setup")
        hold(s)
        snap = scan(s)
        assert snap["setup"]["storage"]["state"] == "error" and snap["setup"]["canCapture"] is False
        snap = s.capture_setup("Lab Bench")
        assert snap["notice"].startswith("Setup not saved: Tiger Data unavailable")
        assert snap["setup"]["setups"] == [] and s.selected_setup_id is None
        assert not (tmp_path / "setups").exists()  # nothing silently written locally
        # Procedure mode and the app still work.
        s.set_workspace("procedure")
        assert s.command("teach")["mode"] == "teaching"
        from app import main

        monkeypatch.setattr(main.session, "setups", repo)
        with TestClient(main.app) as client:
            health = client.get("/api/health").json()
        assert health["storage"]["state"] == "error" and health["storage"]["provider"] == "tiger"
        assert_no_secrets(json.dumps(snap), json.dumps(health), caplog.text, status.message, repr(repo),
                          repr(Settings(tiger_database_url=SECRET_URL)), " ".join(repo.errors))
    finally:
        s.close()


def test_missing_table_points_to_the_check_command():
    repo = tiger_repo(FakeTiger())  # reachable database, migration never applied
    assert repo.status().state == "error"
    assert "UndefinedTable" in repo.status().message and "npm run tiger:check" in repo.status().message


def test_capture_never_claims_success_after_a_database_failure(tiger_session, caplog):
    caplog.set_level(logging.DEBUG)
    s, db, _ = tiger_session
    scan(s)
    db.fail_on = ("INSERT INTO teachback_setups", leaky_error())
    snap = s.capture_setup("Lab Bench")
    assert snap["notice"] == "Setup not saved: Tiger Data is unavailable, so the setup was not saved."
    assert snap["setup"]["setups"] == [] and snap["setup"]["selected"] is None
    assert snap["setup"]["storage"]["state"] == "error" and snap["setup"]["canCapture"] is False
    assert db.rows == {}
    assert_no_secrets(json.dumps(snap), caplog.text)
    # While storage is in error, capture is refused up front. An explicit refresh recovers it.
    db.fail_on = None
    assert s.capture_setup("Lab Bench")["notice"].startswith("Setup not saved: Tiger Data unavailable")
    snap = s.refresh_setups()
    assert snap["setup"]["storage"]["state"] == "ready"
    scan(s, 5)
    snap = s.capture_setup("Lab Bench")
    assert snap["notice"] == 'Saved setup "Lab Bench" with 2 objects.'
    assert snap["setup"]["storage"] == {"provider": "tiger", "state": "ready", "message": 'Saved "Lab Bench" to Tiger Data.'}
    assert list(db.rows) == ["lab-bench"]


def test_constraint_rejection_is_reported_without_marking_tiger_down(tiger_session):
    s, db, _ = tiger_session
    scan(s)
    db.fail_on = ("INSERT INTO teachback_setups", psycopg.errors.CheckViolation("teachback_setups_name_length"))
    snap = s.capture_setup("Lab Bench")
    assert snap["notice"] == "Setup not saved: Tiger Data rejected the setup (CheckViolation)."
    assert snap["setup"]["storage"]["state"] == "ready"


def test_slow_save_does_not_block_camera_frames(tiger_session):
    s, db, _ = tiger_session
    scan(s)
    entered, release = threading.Event(), threading.Event()
    db.block_on = ("INSERT INTO teachback_setups", entered, release)
    worker = threading.Thread(target=s.capture_setup, args=("Lab Bench",))
    worker.start()
    try:
        assert entered.wait(5)
        done = threading.Event()
        threading.Thread(target=lambda: (s.process_frame(frame(START), now=20), done.set())).start()
        assert done.wait(2), "a frame was blocked behind the database round trip"
    finally:
        release.set()
        worker.join(5)
    assert s.selected_setup_id == "lab-bench"


# -- isolation -------------------------------------------------------------------------------------


def test_tiger_setups_never_touch_the_saved_procedure(tiger_session, tmp_path, monkeypatch):
    from app import session as session_module

    s, db, detector = tiger_session
    monkeypatch.setattr(session_module, "PROCEDURE_FILE", tmp_path / "procedure.json")
    monkeypatch.setattr(session_module, "KEYFRAME_DIR", tmp_path / "keyframes")
    recorder = TeachRecorder(1, 2)
    recorder.on_stable(semantic_scene(BOXES, LABELS, Settings().vision, 0))
    recorder.on_stable(semantic_scene([{**BOXES[0], "bbox": [.4, .2, .6, .4]}, BOXES[1]], LABELS, Settings().vision, 1))
    s.procedure = recorder.finish()
    s.persist = True
    s._save()
    before = (tmp_path / "procedure.json").read_bytes()
    scan(s)
    s.capture_setup("Lab Bench")
    s.check_setup()
    wait_result(s.scanner)
    s.process_frame(frame(START), now=4)
    s.refresh_setups()
    assert (tmp_path / "procedure.json").read_bytes() == before
    assert list(db.rows) == ["lab-bench"]
    assert all("procedure" not in sql.lower() for sql, _ in db.executed)


# -- TLS -------------------------------------------------------------------------------------------


def test_tls_is_always_required():
    for weak in ("", "?sslmode=disable", "?sslmode=allow", "?sslmode=prefer"):
        assert "sslmode=require" in tls_conninfo(f"postgres://u:p@h:5432/db{weak}", 5)
    assert "sslmode=verify-full" in tls_conninfo("postgres://u:p@h:5432/db?sslmode=verify-full", 5)
    assert "connect_timeout=5" in tls_conninfo("postgres://u:p@h:5432/db", 5)


def test_connection_without_tls_is_refused(monkeypatch):
    class PlainConn:
        pgconn = type("P", (), {"ssl_in_use": False})()
        closed = False

        def close(self):
            self.closed = True

    conn = PlainConn()
    monkeypatch.setattr(tiger.psycopg, "connect", lambda conninfo: conn)
    with pytest.raises(tiger.InsecureConnectionError):
        tiger.connect_tiger(SECRET_URL, 5)
    assert conn.closed


# -- tiger:check -----------------------------------------------------------------------------------


def test_check_command_requires_url():
    lines = []
    assert tiger_check.run("", out=lines.append) == 2
    assert "TIGER_DATABASE_URL is not set" in lines[0]


def test_check_command_migrates_verifies_and_leaves_no_rows():
    db = FakeTiger()
    db.rows = {}
    lines = []
    assert tiger_check.run(SECRET_URL, connect=db.connect, out=lines.append) == 0, lines
    assert lines[-1] == "Tiger Data check passed."
    assert db.table_exists and db.rows == {}  # probe rolled back
    assert db.count(UPSERT_SETUP_SQL) == 2  # insert + conflict/update path
    assert_no_secrets(*lines)


def test_check_command_failures_are_sanitized():
    db = FakeTiger()
    db.connect_error = leaky_error()
    lines = []
    assert tiger_check.run(SECRET_URL, connect=db.connect, out=lines.append) == 1
    assert lines == ["FAIL at connect: OperationalError"]
    db = FakeTiger()
    db.fail_on = ("INSERT INTO teachback_setups (id, name, objects, created_at) VALUES", leaky_error(psycopg.errors.InternalError))
    lines = []
    assert tiger_check.run(SECRET_URL, connect=db.connect, out=lines.append) == 1
    assert lines[-1] == "FAIL at verify: InternalError" and db.rows == {}
    assert_no_secrets(*lines)


def test_check_command_refuses_a_connection_without_tls():
    db = FakeTiger()
    db.ssl = False
    lines = []
    assert tiger_check.run(SECRET_URL, connect=db.connect, out=lines.append) == 1
    assert lines == ["FAIL at connect: InsecureConnectionError"] and not db.table_exists
