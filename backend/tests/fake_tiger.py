"""A focused, offline stand-in for the psycopg calls TeachBack makes against Tiger Cloud.

It understands exactly the statements in app.tiger / tiger_check.py, records every (sql, params)
pair, stages writes until commit, supports transactions/savepoints/psycopg.Rollback, and enforces the
same id/name/objects constraints as backend/sql/001_tiger_setups.sql. Unknown SQL fails the test.
"""

from __future__ import annotations

import copy
import re
import threading
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from app.tiger import SELECT_SETUP_SQL, SELECT_SETUPS_SQL, SET_STATEMENT_TIMEOUT_SQL, UPSERT_SETUP_SQL

SECRET_HOST = "secret-host.tsdb.cloud.timescale.com"
SECRET_USER = "tsdbadmin"
SECRET_PASSWORD = "hunter2-SuperSecret"
SECRET_URL = f"postgres://{SECRET_USER}:{SECRET_PASSWORD}@{SECRET_HOST}:34567/tsdb?sslmode=require"
SECRETS = (SECRET_HOST, SECRET_USER, SECRET_PASSWORD, SECRET_URL)

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def leaky_error(cls=psycopg.OperationalError):
    """psycopg errors really do embed host and user names; the app must never surface this text."""
    return cls(f'connection to server at "{SECRET_HOST}" (10.0.0.1), port 34567 failed: '
               f'FATAL: password authentication failed for user "{SECRET_USER}" ({SECRET_URL})')


class FakeTiger:
    def __init__(self) -> None:
        self.table_exists = False
        self.rows: dict[str, tuple] = {}
        self.executed: list[tuple[str, object]] = []
        self.connects = 0
        self.connect_error: Exception | None = None
        self.fail_on: tuple[str, Exception] | None = None  # (sql substring, error)
        self.block_on: tuple[str, threading.Event, threading.Event] | None = None  # (substring, entered, release)
        self.ssl = True

    def connect(self, url: str, timeout: int):
        assert url == SECRET_URL and timeout > 0
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
        self.work: dict[str, tuple] = copy.deepcopy(db.rows)
        self.table_exists = db.table_exists
        self.savepoints: list[tuple[dict, bool]] = []
        self.pgconn = type("PGconn", (), {"ssl_in_use": db.ssl})()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        self.closed = True
        return False

    def commit(self):
        self.db.rows = copy.deepcopy(self.work)
        self.db.table_exists = self.table_exists

    def rollback(self):
        self.work = copy.deepcopy(self.db.rows)
        self.table_exists = self.db.table_exists

    def cursor(self):
        return FakeCursor(self)

    def transaction(self):
        return FakeTransaction(self)


class FakeTransaction:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    def __enter__(self):
        self.conn.savepoints.append((copy.deepcopy(self.conn.work), self.conn.table_exists))
        return self

    def __exit__(self, exc_type, exc, tb):
        work, table = self.conn.savepoints.pop()
        if exc_type is not None:
            self.conn.work, self.conn.table_exists = work, table  # roll back to this savepoint
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
        db = self.conn.db
        db.executed.append((sql, params))
        if db.block_on and db.block_on[0] in sql:
            db.block_on[1].set()
            assert db.block_on[2].wait(5), "test never released the blocked statement"
        if db.fail_on and db.fail_on[0] in sql:
            raise db.fail_on[1]
        if "%s" in sql:
            assert isinstance(params, tuple) and len(params) == sql.count("%s"), "parameters must be bound"
        if sql.startswith("CREATE TABLE IF NOT EXISTS teachback_setups"):
            self.conn.table_exists = True
        elif sql.startswith("COMMENT ON TABLE teachback_setups"):
            self._need_table()
        elif sql == SET_STATEMENT_TIMEOUT_SQL:
            self.result = [(params[0],)]
        elif sql == SELECT_SETUPS_SQL:
            self._need_table()
            self.result = [self.conn.work[k] for k in sorted(self.conn.work)]
        elif sql == SELECT_SETUP_SQL:
            self._need_table()
            self.result = [self.conn.work[params[0]]] if params[0] in self.conn.work else []
        elif sql == "SELECT count(*) FROM teachback_setups":
            self._need_table()
            self.result = [(len(self.conn.work),)]
        elif sql == UPSERT_SETUP_SQL or sql.startswith("INSERT INTO teachback_setups"):
            self._need_table()
            setup_id, name, objects, created = (*params, None) if len(params) == 3 else params  # 3: NOW()
            assert isinstance(objects, Jsonb)
            self._check_constraints(setup_id, name, objects.obj)
            upsert = sql == UPSERT_SETUP_SQL
            if setup_id in self.conn.work and not upsert:
                raise psycopg.errors.UniqueViolation("duplicate key")
            if setup_id in self.conn.work:  # DO UPDATE keeps the original created_at
                created = self.conn.work[setup_id][3]
            created = created if isinstance(created, datetime) else datetime.now(timezone.utc)
            self.conn.work[setup_id] = (setup_id, name, copy.deepcopy(objects.obj), created)
            self.result = [self.conn.work[setup_id]] if upsert else []
        else:
            raise AssertionError(f"unexpected SQL: {sql[:80]}")

    def _need_table(self):
        if not self.conn.table_exists:
            raise psycopg.errors.UndefinedTable('relation "teachback_setups" does not exist')

    @staticmethod
    def _check_constraints(setup_id, name, objects):
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
