"""Setup Check history: immutable events, bounded background writer, TigerData hypertable + time_bucket."""

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

import tiger_check
from app import tiger
from app.config import Settings
from app.detectors import LatestScan
from app.history import (
    DisabledCheckHistory,
    HistoryStatus,
    HistoryWriteError,
    HistoryWriter,
    ReadinessSummary,
    SetupCheckEvent,
    create_check_history_repository,
)
from app.session import SETUP_DIR, Session
from app.setups import SetupObject, create_setup_repository
from app.tiger import (
    INSERT_CHECK_SQL,
    RECENT_CHECKS_SQL,
    SUMMARY_BUCKETS_SQL,
    SUMMARY_TOTALS_SQL,
    TigerCheckHistoryRepository,
    migration_files,
    migration_statements,
)

from .fake_tiger import CHECKS, SECRET_URL, SECRETS, FakeTiger, leaky_error
from .test_semantic import BOXES, LABELS, ControlledDetector, hold, wait_result
from .test_session import START, frame

TIGER = Settings(tiger_database_url=SECRET_URL)
CUP = SetupObject(label="cup", zone="A")


def assert_no_secrets(*texts):
    for text in texts:
        for secret in SECRETS:
            assert secret not in text, f"credential leaked: {secret!r}"


def event(setup_id="lab-bench", status="complete", at=None, **findings) -> SetupCheckEvent:
    if status == "complete":
        findings.setdefault("correct", (CUP,))
    else:
        findings.setdefault("missing", (CUP,))
    return SetupCheckEvent(event_id=uuid.uuid4(), checked_at=at or time.time(), setup_id=setup_id,
                           setup_name="Lab Bench", status=status, **findings)


def history_repo(db: FakeTiger) -> TigerCheckHistoryRepository:
    return create_check_history_repository(TIGER, connect=db.connect)


def session_with(db: FakeTiger | None, **settings_kw) -> Session:
    cfg = Settings(semantic_beta=True, tiger_database_url=SECRET_URL if db else "", **settings_kw)
    kw = {}
    if db is not None:
        kw = {"setup_repository": create_setup_repository(cfg, SETUP_DIR, connect=db.connect),
              "history_repository": create_check_history_repository(cfg, connect=db.connect)}
    s = Session(cfg, persist=False, **kw)
    detector = ControlledDetector()
    s.semantic_detector, s.scanner = detector, LatestScan(detector)
    s.configure_detector("semantic", ",".join(LABELS))
    s.set_workspace("setup")
    hold(s)
    s.detector_for_tests = detector
    return s


def scan(s, now=3.0):
    s.command("scan")
    wait_result(s.scanner)
    return s.process_frame(frame(START), now=now)


def check(s, now=4.0):
    s.check_setup()
    wait_result(s.scanner)
    return s.process_frame(frame(START), now=now)


def captured_and_checked(s):
    scan(s)
    assert s.capture_setup("Lab Bench")["notice"].startswith("Saved setup")
    return check(s)


@pytest.fixture
def db():
    return FakeTiger().migrate_all()


@pytest.fixture
def tiger_session(db):
    s = session_with(db)
    yield s
    s.close()


# -- model -----------------------------------------------------------------------------------------


def test_event_is_immutable_and_consistent():
    e = event()
    with pytest.raises(Exception):
        e.status = "needs_attention"
    with pytest.raises(ValueError):
        SetupCheckEvent(event_id=uuid.uuid4(), checked_at=1, setup_id="lab-bench", setup_name="x",
                        status="complete", missing=(CUP,))
    with pytest.raises(ValueError):
        event(setup_id="Not A Slug")
    with pytest.raises(ValueError):
        ReadinessSummary(setup_id="x", total_checks=2, complete_checks=1, needs_attention_checks=1,
                         readiness_percent=75.0, latest_checked_at=1.0)


# -- migration 002 ---------------------------------------------------------------------------------


def test_migrations_are_discovered_in_order_and_002_is_a_hypertable():
    assert [p.name for p in migration_files()] == ["001_tiger_setups.sql", "002_tiger_setup_check_history.sql"]
    create, index, comment = migration_statements(migration_files()[1])
    assert create.startswith(f"CREATE TABLE IF NOT EXISTS {CHECKS} (")
    assert "tsdb.hypertable" in create and "tsdb.partition_column = 'checked_at'" in create
    for column in ("checked_at TIMESTAMPTZ NOT NULL", "event_id UUID NOT NULL", "setup_id TEXT NOT NULL",
                   "setup_name TEXT NOT NULL", "status TEXT NOT NULL", "correct JSONB", "missing JSONB",
                   "unexpected JSONB", "misplaced JSONB"):
        assert column in create
    assert "PRIMARY KEY (event_id, checked_at)" in create  # unique keys include the partition column
    assert "CHECK (status IN ('complete', 'needs_attention'))" in create
    assert index.startswith("CREATE INDEX IF NOT EXISTS") and "(setup_id, checked_at DESC)" in index
    code = "\n".join(line for stmt in (create, index, comment) for line in stmt.splitlines()
                     if not line.strip().startswith("--")).upper()
    assert not any(word in code for word in ("UPDATE ", "DELETE", "TRIGGER", "RULE "))


def test_migrations_are_idempotent_and_keep_history():
    db = FakeTiger()
    with db.connect(SECRET_URL, 5) as conn:
        tiger.apply_migrations(conn)
    repo = history_repo(db)
    assert repo.status().state == "ready"
    assert repo.record(event())
    for _ in range(3):
        with db.connect(SECRET_URL, 5) as conn:
            assert tiger.apply_migrations(conn) == ["001_tiger_setups", "002_tiger_setup_check_history"]
    assert db.state["hypertable"] and len(db.checks) == 1


def test_plain_table_is_rejected_as_history_storage():
    db = FakeTiger().migrate_all()
    db.state["hypertable"] = False  # e.g. created by hand without tsdb.hypertable
    repo = history_repo(db)
    assert repo.status().state == "error" and "hypertable" in repo.status().message
    lines = []
    assert tiger_check.run(SECRET_URL, connect=db.connect, out=lines.append) == 1
    assert lines[-1] == f"FAIL at verify hypertable: {CHECKS} is not a TigerData hypertable"


# -- writes ----------------------------------------------------------------------------------------


def test_insert_is_parameterized_append_only_and_idempotent(db):
    repo = history_repo(db)
    e = event(status="needs_attention", missing=(CUP,), unexpected=(SetupObject(label="phone", zone="B"),))
    assert repo.record(e) and repo.record(e)  # a retry of the same event is confirmed, not duplicated
    assert len(db.checks) == 1
    sql, params = [(s, p) for s, p in db.executed if s == INSERT_CHECK_SQL][0]
    assert "ON CONFLICT (event_id, checked_at) DO NOTHING" in sql
    assert "Lab Bench" not in sql and "phone" not in sql
    assert params[1] == e.event_id and params[0] == datetime.fromtimestamp(e.checked_at, tz=timezone.utc)
    assert params[5].obj == [] and params[6].obj == [{"label": "cup", "zone": "A"}]
    assert not any(s.lstrip().upper().startswith(("UPDATE", "DELETE")) for s, _ in db.executed)


def test_one_check_produces_exactly_one_event_despite_repeated_frames(tiger_session, db):
    s = tiger_session
    snap = captured_and_checked(s)
    assert snap["setup"]["result"]["status"] == "complete"
    event_id = snap["setup"]["resultHistory"]["eventId"]
    for i in range(25):  # frames, broadcasts, stale snapshots
        s.process_frame(frame(START), now=5 + i * 0.2)
        s.snapshot()
    s.history_writer.submit(s.setup_event)  # an explicit re-submit of the same check
    assert s.history_writer.wait_idle(5)
    assert [str(k[0]) for k in db.checks] == [event_id]
    assert db.count(INSERT_CHECK_SQL) == 1
    assert s.snapshot()["setup"]["resultHistory"] == {"eventId": event_id, "state": "saved"}


def test_history_write_never_blocks_frames_or_delays_the_verdict(tiger_session, db):
    s = tiger_session
    scan(s)
    s.capture_setup("Lab Bench")
    assert s.history_writer.wait_idle(5)
    entered, release = threading.Event(), threading.Event()
    db.block_on = ("INSERT INTO teachback_setup_checks", entered, release)
    try:
        started = time.perf_counter()
        snap = check(s)
        assert snap["setup"]["result"]["status"] == "complete"  # verdict is immediate
        assert snap["setup"]["resultHistory"]["state"] == "pending"
        assert entered.wait(5)
        for i in range(5):  # frames flow while the database write is stuck
            s.process_frame(frame(START), now=10 + i * 0.2)
        assert time.perf_counter() - started < 2.0
    finally:
        release.set()
    assert s.history_writer.wait_idle(5)
    assert s.snapshot()["setup"]["resultHistory"]["state"] == "saved"


class SlowRepo(DisabledCheckHistory):
    """Records after a gate opens; used for queue overflow and shutdown drains."""

    def __init__(self, delay=0.0):
        super().__init__()
        self.gate = threading.Event()
        self.gate.set()
        self.delay = delay
        self.recorded = []

    def record(self, e):
        assert self.gate.wait(10)
        time.sleep(self.delay)
        self.recorded.append(e.event_id)
        return True

    def status(self):
        return HistoryStatus(provider="tiger", state="ready", message="ok")


def test_queue_overflow_is_visible_not_silent(caplog):
    caplog.set_level(logging.WARNING)
    repo = SlowRepo()
    repo.gate.clear()
    writer = HistoryWriter(repo, maxsize=2)
    try:
        states = [writer.submit(event()) for _ in range(5)]
        # One in flight on the worker (or queued), two queued, the rest refused and counted.
        assert states.count("dropped") >= 2 and states[-1] == "dropped"
        stats = writer.stats()
        assert stats["dropped"] == states.count("dropped") and "queue full" in stats["lastProblem"]
        assert "queue full" in caplog.text
    finally:
        repo.gate.set()
        assert writer.close(5)
    assert len(repo.recorded) == states.count("pending")


def test_overflow_reaches_the_snapshot(tiger_session, db):
    s = tiger_session
    s.history_writer = HistoryWriter(SlowRepo(), maxsize=1)
    s.history_writer.repo.gate.clear()
    for _ in range(4):
        s.history_writer.submit(event())
    writer_stats = s.snapshot()["setup"]["history"]["writer"]
    assert writer_stats["dropped"] >= 2 and "queue full" in writer_stats["lastProblem"]
    s.history_writer.repo.gate.set()


def test_write_failure_keeps_the_verdict_and_is_never_reported_saved(tiger_session, db, caplog):
    caplog.set_level(logging.DEBUG)
    s = tiger_session
    scan(s)
    s.capture_setup("Lab Bench")
    assert s.history_writer.wait_idle(5)
    db.fail_on = ("INSERT INTO teachback_setup_checks", leaky_error())
    snap = check(s)
    assert s.history_writer.wait_idle(5)
    snap = s.snapshot()
    assert snap["setup"]["result"]["status"] == "complete"  # deterministic verdict untouched
    assert snap["setup"]["resultHistory"]["state"] == "failed"
    history = snap["setup"]["history"]
    assert history["state"] == "error" and "OperationalError" in history["message"]
    assert history["writer"]["failed"] == 1 and history["writer"]["saved"] == 0
    assert "History not saved" in history["writer"]["lastProblem"]
    assert db.checks == {}
    from app import main

    main_session = main.session
    try:
        main.session = s
        with TestClient(main.app) as client:
            health = client.get("/api/health").json()
    finally:
        main.session = main_session
    assert health["history"]["state"] == "error"
    assert_no_secrets(json.dumps(snap), json.dumps(health), caplog.text, repr(s.history))


def test_history_outage_does_not_break_procedure_mode_or_capture(db):
    down = FakeTiger()
    down.connect_error = leaky_error()  # history unreachable, saved setups fine
    s = session_with(db)
    s.history_writer.close(1)
    s.history = create_check_history_repository(TIGER, connect=down.connect)
    s.history_writer = HistoryWriter(s.history)
    try:
        assert s.history.status().state == "error"
        snap = captured_and_checked(s)
        assert snap["setup"]["result"]["status"] == "complete"
        assert list(db.rows) == ["lab-bench"]
        s.set_workspace("procedure")
        assert s.command("teach")["mode"] == "teaching"
    finally:
        s.close()


# -- reads -----------------------------------------------------------------------------------------


def test_recent_and_summary_come_from_caches_not_per_frame_queries(tiger_session, db):
    s = tiger_session
    captured_and_checked(s)
    assert s.history_writer.wait_idle(5)
    queries = lambda: (db.count(RECENT_CHECKS_SQL), db.count(SUMMARY_TOTALS_SQL), db.count(SUMMARY_BUCKETS_SQL))  # noqa: E731
    before = queries()
    for i in range(30):
        s.process_frame(frame(START), now=20 + i * 0.2)
        s.snapshot()
        s.history.recent("lab-bench")
        s.history.summary("lab-bench")
    assert queries() == before
    history = s.snapshot()["setup"]["history"]
    assert history["summary"]["totalChecks"] == 1 and len(history["recent"]) == 1
    s.refresh_history()  # explicit refresh is a controlled boundary
    assert s.history_writer.wait_idle(5)
    assert queries()[0] == before[0] + 1


def test_time_bucket_summary_is_computed_and_validated(db):
    repo = history_repo(db)
    now = time.time()
    hour = 3600
    for at, status in ((now - 2 * hour, "complete"), (now - 2 * hour + 60, "needs_attention"),
                       (now - 10, "complete"), (now - 5, "complete"), (now - 48 * hour, "complete")):
        repo.record(event(status=status, at=at))
    repo.refresh("lab-bench")
    summary = repo.summary("lab-bench")
    assert (summary.total_checks, summary.complete_checks, summary.needs_attention_checks) == (5, 4, 1)
    assert summary.readiness_percent == 80.0 and summary.latest_checked_at == pytest.approx(now - 5)
    assert [(b.total, b.complete) for b in summary.buckets] == [(2, 1), (2, 2)]  # 48 h-old check is outside
    assert all(b.bucket_start % hour == 0 for b in summary.buckets)
    sql, params = [(s, p) for s, p in db.executed if s == SUMMARY_BUCKETS_SQL][-1]
    assert "time_bucket(%s::interval, checked_at)" in sql and params[0] == timedelta(hours=1)
    newest_first = sorted((now - 5, now - 10, now - 2 * hour + 60, now - 2 * hour, now - 48 * hour), reverse=True)
    # TIMESTAMPTZ keeps microseconds, so epoch floats round-trip to within 1 us.
    assert [e.checked_at for e in repo.recent("lab-bench")] == pytest.approx(newest_first, abs=1e-5)


def test_every_database_row_is_validated(db):
    repo = history_repo(db)
    good = event()
    repo.record(good)
    ts = datetime.now(timezone.utc)
    bad = {  # rows the database would store but the app must not trust
        "complete-with-missing": (uuid.uuid4(), ts, "lab-bench", "Lab Bench", "complete", [], [{"label": "x"}], [], []),
        "bad-uuid": ("not-a-uuid", ts, "lab-bench", "Lab Bench", "needs_attention", [], [{"label": "x"}], [], []),
        "bad-list": (uuid.uuid4(), ts, "lab-bench", "Lab Bench", "needs_attention", [], "oops", [], []),
    }
    for i, row in enumerate(bad.values()):
        db.checks[("bad", i)] = row
    db.bucket_rows_override = [(ts, 1, 2), (ts, 0, 0), ("x",), (ts, 1, 1)]
    repo.refresh("lab-bench")
    assert [e.event_id for e in repo.recent("lab-bench")] == [good.event_id]
    assert len(repo.errors) == 3 + 3  # three bad events, three bad buckets
    assert [(b.total, b.complete) for b in repo.summary("lab-bench").buckets] == [(1, 1)]


def test_invalid_totals_row_yields_no_summary():
    for totals in ((1, 3, None), (2, 1, None), (0, 0, datetime.now(timezone.utc)), ("x",)):
        summary, errors = tiger.rows_to_summary("lab-bench", totals, [])
        assert summary is None and errors, totals


# -- local mode + lifecycle ------------------------------------------------------------------------


def test_disabled_local_mode_still_checks_setups():
    s = session_with(None)
    try:
        assert isinstance(s.history, DisabledCheckHistory)
        snap = captured_and_checked(s)
        assert snap["setup"]["result"]["status"] == "complete"
        history = snap["setup"]["history"]
        assert (history["provider"], history["state"]) == ("local", "disabled")
        assert history["recent"] == [] and history["summary"] is None
        assert snap["setup"]["resultHistory"]["state"] == "disabled"  # never shown as saved
        assert s.history_writer.stats()["queued"] == 0
        with pytest.raises(HistoryWriteError):
            s.history.record(s.setup_event)
    finally:
        s.close()


def test_shutdown_drains_accepted_events_within_a_bounded_timeout():
    repo = SlowRepo(delay=0.02)
    writer = HistoryWriter(repo, maxsize=16)
    ids = [writer.submit(event()) for _ in range(8)]
    assert ids.count("pending") == 8
    started = time.perf_counter()
    assert writer.close(timeout=5) is True
    assert len(repo.recorded) == 8 and time.perf_counter() - started < 5
    assert writer.submit(event()) == "dropped"  # no intake after close

    stuck = SlowRepo()
    stuck.gate.clear()
    writer = HistoryWriter(stuck, maxsize=4)
    writer.submit(event())
    started = time.perf_counter()
    assert writer.close(timeout=0.3) is False  # bounded: never hangs shutdown
    assert time.perf_counter() - started < 2
    stuck.gate.set()


def test_session_close_drains_pending_history(db):
    s = session_with(db)
    captured_and_checked(s)
    s.close()
    assert len(db.checks) == 1


# -- tiger:check -----------------------------------------------------------------------------------


def test_check_command_verifies_the_history_hypertable_and_leaves_nothing():
    db = FakeTiger()
    lines = []
    assert tiger_check.run(SECRET_URL, connect=db.connect, out=lines.append) == 0, lines
    joined = "\n".join(lines)
    assert "001_tiger_setups, 002_tiger_setup_check_history" in joined
    assert f"PASS {CHECKS} is a TigerData hypertable partitioned on checked_at" in joined
    assert "time_bucket summary" in joined and lines[-1] == "Tiger Data check passed."
    assert db.checks == {} and db.rows == {}
    assert db.count(INSERT_CHECK_SQL) == 3  # two events + one duplicate retry
    assert_no_secrets(*lines)


def test_check_command_history_failure_is_sanitized_and_rolled_back():
    db = FakeTiger()
    db.fail_on = (SUMMARY_BUCKETS_SQL, leaky_error(psycopg.errors.InternalError))
    lines = []
    assert tiger_check.run(SECRET_URL, connect=db.connect, out=lines.append) == 1
    assert lines[-1] == "FAIL at verify history: InternalError"
    assert db.checks == {}
    assert_no_secrets(*lines)
