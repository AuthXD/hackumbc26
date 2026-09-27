"""A focused, offline stand-in for the psycopg calls TeachBack makes against Tiger Cloud.

It understands exactly the statements in app.tiger / tiger_check.py, records every (sql, params)
pair, stages writes until commit, supports transactions/savepoints/psycopg.Rollback, and enforces the
constraints of backend/sql/001 and 002: the setups table, and the teachback_setup_checks hypertable
(composite key (event_id, checked_at), status CHECK, JSON-array findings, ON CONFLICT DO NOTHING,
TigerData metadata views, and time_bucket). Unknown SQL fails the test.
"""

from __future__ import annotations

import copy
import re
import threading
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from app.tiger import (
    HYPERTABLE_SQL,
    SELECT_PROCEDURE_SQL,
    SELECT_PROCEDURES_SQL,
    UPSERT_PROCEDURE_SQL,
    INSERT_CHECK_SQL,
    PARTITION_COLUMN_SQL,
    RECENT_CHECKS_SQL,
    SELECT_SETUP_SQL,
    SELECT_SETUPS_SQL,
    SET_STATEMENT_TIMEOUT_SQL,
    SUMMARY_BUCKETS_SQL,
    SUMMARY_TOTALS_SQL,
    UPSERT_SETUP_SQL,
)

SECRET_HOST = "secret-host.tsdb.cloud.timescale.com"
SECRET_USER = "tsdbadmin"
SECRET_PASSWORD = "hunter2-SuperSecret"
SECRET_URL = f"postgres://{SECRET_USER}:{SECRET_PASSWORD}@{SECRET_HOST}:34567/tsdb?sslmode=require"
SECRETS = (SECRET_HOST, SECRET_USER, SECRET_PASSWORD, SECRET_URL)

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
CHECKS = "teachback_setup_checks"
PROCEDURES = "teachback_procedures"


def leaky_error(cls=psycopg.OperationalError):
    """psycopg errors really do embed host and user names; the app must never surface this text."""
    return cls(f'connection to server at "{SECRET_HOST}" (10.0.0.1), port 34567 failed: '
               f'FATAL: password authentication failed for user "{SECRET_USER}" ({SECRET_URL})')


def empty_state() -> dict:
    return {"setups_table": False, "rows": {}, "checks_table": False, "hypertable": False, "checks": {},
            "procedures_table": False, "procedures": {}}


class FakeTiger:
    def __init__(self) -> None:
        self.state = empty_state()
        self.executed: list[tuple[str, object]] = []
        self.connects = 0
        self.connect_error: Exception | None = None
        self.fail_on: tuple[str, Exception] | None = None  # (sql substring, error)
        self.block_on: tuple[str, threading.Event, threading.Event] | None = None  # (substring, entered, release)
        self.ssl = True
        self.bucket_rows_override: list | None = None  # inject malformed time_bucket rows
        self.lock = threading.Lock()  # connections may come from the history writer thread

    # Saved-setups view kept for the existing tests.
    @property
    def rows(self) -> dict:
        return self.state["rows"]

    @rows.setter
    def rows(self, value: dict) -> None:
        self.state["rows"] = value

    @property
    def table_exists(self) -> bool:
        return self.state["setups_table"]

    @table_exists.setter
    def table_exists(self, value: bool) -> None:
        self.state["setups_table"] = value

    @property
    def checks(self) -> dict:
        return self.state["checks"]

    @property
    def procedures(self) -> dict:
        return self.state["procedures"]

    def migrate_all(self) -> "FakeTiger":
        self.state.update(setups_table=True, checks_table=True, hypertable=True, procedures_table=True)
        return self

    def connect(self, url: str, timeout: int):
        assert url == SECRET_URL and timeout > 0
        with self.lock:
            self.connects += 1
        if self.connect_error:
            raise self.connect_error
        return FakeConnection(self)

    def count(self, sql: str) -> int:
        return sum(1 for s, _ in self.executed if s == sql)


class FakeConnection:
    def __init__(self, db: FakeTiger) -> None:
        self.db = db
        self.autocommit = False
        self.closed = False
        with db.lock:
            self.work = copy.deepcopy(db.state)
        self.savepoints: list[dict] = []
        self.dirty: set[str] = set()  # like PostgreSQL, commit publishes only what this connection wrote
        self.pgconn = type("PGconn", (), {"ssl_in_use": db.ssl})()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        self.closed = True
        return False

    def commit(self):
        groups = {"setups": ("setups_table", "rows"), "checks": ("checks_table", "hypertable", "checks"),
                  "procedures": ("procedures_table", "procedures")}
        with self.db.lock:
            for group in self.dirty:
                for key in groups[group]:
                    self.db.state[key] = copy.deepcopy(self.work[key])
        self.dirty.clear()

    def rollback(self):
        with self.db.lock:
            self.work = copy.deepcopy(self.db.state)

    def cursor(self):
        return FakeCursor(self)

    def transaction(self):
        return FakeTransaction(self)


class FakeTransaction:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    def __enter__(self):
        self.conn.savepoints.append(copy.deepcopy(self.conn.work))
        return self

    def __exit__(self, exc_type, exc, tb):
        saved = self.conn.savepoints.pop()
        if exc_type is not None:
            self.conn.work = saved  # roll back to this savepoint
            return exc_type is psycopg.Rollback  # psycopg swallows Rollback, re-raises anything else
        if not self.conn.savepoints and self.conn.autocommit:
            self.conn.commit()  # outermost block of an autocommit connection commits
        return False


class FakeCursor:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.result: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None):
        db, w = self.conn.db, self.conn.work
        with db.lock:
            db.executed.append((sql, params))
        if db.block_on and db.block_on[0] in sql:
            db.block_on[1].set()
            assert db.block_on[2].wait(5), "test never released the blocked statement"
        if db.fail_on and db.fail_on[0] in sql:
            raise db.fail_on[1]
        if "%s" in sql:
            assert isinstance(params, tuple) and len(params) == sql.count("%s"), "parameters must be bound"
        self.result = []
        # -- migrations -----------------------------------------------------------------------
        if sql.startswith(("CREATE TABLE IF NOT EXISTS teachback_setups (", "INSERT INTO teachback_setups "))                 or sql == UPSERT_SETUP_SQL:
            self.conn.dirty.add("setups")
        if sql.startswith(f"CREATE TABLE IF NOT EXISTS {CHECKS} (") or sql == INSERT_CHECK_SQL:
            self.conn.dirty.add("checks")
        if sql.startswith(f"CREATE TABLE IF NOT EXISTS {PROCEDURES} (") or sql == UPSERT_PROCEDURE_SQL:
            self.conn.dirty.add("procedures")
        if sql.startswith("CREATE TABLE IF NOT EXISTS teachback_setups ("):
            w["setups_table"] = True
        elif sql.startswith("COMMENT ON TABLE teachback_setups "):
            self._need(w["setups_table"], "teachback_setups")
        elif sql.startswith(f"CREATE TABLE IF NOT EXISTS {CHECKS} ("):
            assert "PRIMARY KEY (event_id, checked_at)" in sql, "hypertable unique keys must include checked_at"
            assert "tsdb.hypertable" in sql and "tsdb.partition_column = 'checked_at'" in sql
            if not w["checks_table"]:  # IF NOT EXISTS: a second run changes nothing
                w.update(checks_table=True, hypertable=True)
        elif sql.startswith(f"CREATE INDEX IF NOT EXISTS {CHECKS}_setup_time"):
            self._need(w["checks_table"], CHECKS)
            assert "(setup_id, checked_at DESC)" in sql
        elif sql.startswith(f"COMMENT ON TABLE {CHECKS} "):
            self._need(w["checks_table"], CHECKS)
        elif sql.startswith(f"CREATE TABLE IF NOT EXISTS {PROCEDURES} ("):
            for needed in ("id TEXT PRIMARY KEY", "procedure JSONB NOT NULL", "tags TEXT[]",
                           "detector_kind IN ('color', 'semantic')", "step_count BETWEEN 1 AND 20"):
                assert needed in sql, needed
            assert "tsdb.hypertable" not in sql  # library rows are current records, not time series
            w["procedures_table"] = True
        elif sql.startswith(f"CREATE INDEX IF NOT EXISTS {PROCEDURES}_updated"):
            self._need(w["procedures_table"], PROCEDURES)
        elif sql.startswith(f"COMMENT ON TABLE {PROCEDURES} "):
            self._need(w["procedures_table"], PROCEDURES)
        # -- common ------------------------------------------------------------------------------
        elif sql == SET_STATEMENT_TIMEOUT_SQL:
            self.result = [(params[0],)]
        # -- saved setups (001) -------------------------------------------------------------------
        elif sql == SELECT_SETUPS_SQL:
            self._need(w["setups_table"], "teachback_setups")
            self.result = [w["rows"][k] for k in sorted(w["rows"])]
        elif sql == SELECT_SETUP_SQL:
            self._need(w["setups_table"], "teachback_setups")
            self.result = [w["rows"][params[0]]] if params[0] in w["rows"] else []
        elif sql == "SELECT count(*) FROM teachback_setups":
            self._need(w["setups_table"], "teachback_setups")
            self.result = [(len(w["rows"]),)]
        elif sql == UPSERT_SETUP_SQL or sql.startswith("INSERT INTO teachback_setups "):
            self._need(w["setups_table"], "teachback_setups")
            setup_id, name, objects, created = (*params, None) if len(params) == 3 else params  # 3: NOW()
            assert isinstance(objects, Jsonb)
            self._check_setup(setup_id, name, objects.obj)
            upsert = sql == UPSERT_SETUP_SQL
            if setup_id in w["rows"] and not upsert:
                raise psycopg.errors.UniqueViolation("duplicate key")
            if setup_id in w["rows"]:  # DO UPDATE keeps the original created_at
                created = w["rows"][setup_id][3]
            created = created if isinstance(created, datetime) else datetime.now(timezone.utc)
            w["rows"][setup_id] = (setup_id, name, copy.deepcopy(objects.obj), created)
            self.result = [w["rows"][setup_id]] if upsert else []
        # -- check history (002) ------------------------------------------------------------------
        elif sql == HYPERTABLE_SQL:
            self.result = [(1 if params == (CHECKS,) and w["hypertable"] else 0,)]
        elif sql == PARTITION_COLUMN_SQL:
            self.result = [("checked_at",)] if params == (CHECKS,) and w["hypertable"] else []
        elif sql == INSERT_CHECK_SQL:
            self._need(w["checks_table"], CHECKS)
            checked_at, event_id, setup_id, setup_name, status, *lists = params
            assert isinstance(checked_at, datetime) and checked_at.tzinfo is not None
            assert all(isinstance(v, Jsonb) and isinstance(v.obj, list) for v in lists)
            if status not in ("complete", "needs_attention"):
                raise psycopg.errors.CheckViolation(f"{CHECKS}_status")
            if not (isinstance(setup_id, str) and SLUG.match(setup_id) and len(setup_id) <= 40):
                raise psycopg.errors.CheckViolation(f"{CHECKS}_setup_id")
            key = (event_id, checked_at)
            if key in w["checks"]:
                self.result = []  # ON CONFLICT (event_id, checked_at) DO NOTHING
            else:
                w["checks"][key] = (event_id, checked_at, setup_id, setup_name, status,
                                    *(copy.deepcopy(v.obj) for v in lists))
                self.result = [(event_id,)]
        elif sql == RECENT_CHECKS_SQL:
            self._need(w["checks_table"], CHECKS)
            setup_id, limit = params
            rows = sorted((r for r in w["checks"].values() if r[2] == setup_id), key=lambda r: r[1], reverse=True)
            self.result = rows[:limit]
        elif sql == SUMMARY_TOTALS_SQL:
            self._need(w["checks_table"], CHECKS)
            complete_word, setup_id = params
            rows = [r for r in w["checks"].values() if r[2] == setup_id]
            self.result = [(len(rows), sum(r[4] == complete_word for r in rows), max((r[1] for r in rows), default=None))]
        elif sql == SUMMARY_BUCKETS_SQL:
            self._need(w["checks_table"], CHECKS)
            width, complete_word, setup_id, window = params
            assert isinstance(width, timedelta) and isinstance(window, timedelta)
            if db.bucket_rows_override is not None:
                self.result = list(db.bucket_rows_override)
            else:
                since = datetime.now(timezone.utc) - window
                buckets: dict[datetime, list[int]] = {}
                for r in w["checks"].values():
                    if r[2] == setup_id and r[1] >= since:
                        step = width.total_seconds()
                        start = datetime.fromtimestamp((r[1].timestamp() // step) * step, tz=timezone.utc)  # time_bucket
                        counts = buckets.setdefault(start, [0, 0])
                        counts[0] += 1
                        counts[1] += r[4] == complete_word
                self.result = [(k, v[0], v[1]) for k, v in sorted(buckets.items())]
        # -- procedure library (003) -------------------------------------------------------------
        elif sql == UPSERT_PROCEDURE_SQL:
            self._need(w["procedures_table"], PROCEDURES)
            pid, name, summary, tags, kind, steps, objects, payload, ai, created = params
            assert isinstance(payload, Jsonb) and isinstance(tags, list)
            self._check_procedure(pid, name, summary, tags, kind, steps, objects, payload.obj)
            now = datetime.now(timezone.utc)
            if pid in w["procedures"]:  # DO UPDATE keeps the original created_at
                created = w["procedures"][pid][9]
            w["procedures"][pid] = (pid, name, summary, list(tags), kind, steps, objects, copy.deepcopy(payload.obj),
                                    ai, created, max(now, created))
            self.result = [w["procedures"][pid]]
        elif sql == SELECT_PROCEDURES_SQL:
            self._need(w["procedures_table"], PROCEDURES)
            self.result = sorted(w["procedures"].values(), key=lambda r: (-r[10].timestamp(), r[0]))
        elif sql == SELECT_PROCEDURE_SQL:
            self._need(w["procedures_table"], PROCEDURES)
            self.result = [w["procedures"][params[0]]] if params[0] in w["procedures"] else []
        elif sql == f"SELECT count(*) FROM {CHECKS} WHERE setup_id = %s":
            self._need(w["checks_table"], CHECKS)
            self.result = [(sum(r[2] == params[0] for r in w["checks"].values()),)]
        else:
            raise AssertionError(f"unexpected SQL: {sql[:80]}")

    @staticmethod
    def _need(exists: bool, table: str):
        if not exists:
            raise psycopg.errors.UndefinedTable(f'relation "{table}" does not exist')

    @staticmethod
    def _check_procedure(pid, name, summary, tags, kind, steps, objects, payload):
        if not (isinstance(pid, str) and 1 <= len(pid) <= 40 and SLUG.match(pid)):
            raise psycopg.errors.CheckViolation("teachback_procedures_id_slug")
        if not (isinstance(name, str) and 1 <= len(name) <= 60):
            raise psycopg.errors.CheckViolation("teachback_procedures_name_length")
        if len(summary) > 200:
            raise psycopg.errors.CheckViolation("teachback_procedures_summary_length")
        if len(tags) > 3:
            raise psycopg.errors.CheckViolation("teachback_procedures_tag_count")
        if kind not in ("color", "semantic"):
            raise psycopg.errors.CheckViolation("teachback_procedures_detector")
        if not (1 <= steps <= 20) or not (1 <= objects <= 12):
            raise psycopg.errors.CheckViolation("teachback_procedures_step_count")
        if not isinstance(payload, dict):
            raise psycopg.errors.CheckViolation("teachback_procedures_payload")

    @staticmethod
    def _check_setup(setup_id, name, objects):
        if not (isinstance(setup_id, str) and 1 <= len(setup_id) <= 40 and SLUG.match(setup_id)):
            raise psycopg.errors.CheckViolation("teachback_setups_id_slug")
        if not (isinstance(name, str) and 1 <= len(name) <= 60):
            raise psycopg.errors.CheckViolation("teachback_setups_name_length")
        if not (isinstance(objects, list) and 1 <= len(objects) <= 6):
            raise psycopg.errors.CheckViolation("teachback_setups_objects_array")

    def fetchall(self):
        rows, self.result = self.result, []
        return rows

    def fetchone(self):
        return self.result.pop(0) if self.result else None
