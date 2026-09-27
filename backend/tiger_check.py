"""Explicit Tiger Data migration + verification:  npm run tiger:check

1. Fails clearly when TIGER_DATABASE_URL is missing.
2. Connects with TLS required (and verified in use).
3. Applies every numbered migration in backend/sql, in order, twice (they are idempotent).
4. Saved setups (001): inside a transaction that is always rolled back, SELECT, INSERT, upsert (conflict
   path), read back through SavedSetup validation, and confirm a CHECK constraint rejects an invalid row.
5. Check history (002): confirm teachback_setup_checks is a TigerData hypertable partitioned on
   checked_at; inside a rolled-back transaction insert two check events, read recent history, run the
   time_bucket readiness query, and validate every returned row.
6. Confirms no probe rows remain.

Only sanitized messages are printed: never the URL, host, user, or password.
"""

from __future__ import annotations

import secrets
import sys
import time
import uuid
from datetime import timedelta
from typing import Callable

import psycopg
from psycopg.types.json import Jsonb

from app.config import settings
from app.history import BUCKET_HOURS, SUMMARY_WINDOW_HOURS, SetupCheckEvent
from app.setups import MisplacedObject, SavedSetup, SetupObject
from app.tiger import (
    CHECK_TABLE,
    HYPERTABLE_SQL,
    INSERT_CHECK_SQL,
    PARTITION_COLUMN_SQL,
    RECENT_CHECKS_SQL,
    SELECT_SETUP_SQL,
    SET_STATEMENT_TIMEOUT_SQL,
    SUMMARY_BUCKETS_SQL,
    SUMMARY_TOTALS_SQL,
    UPSERT_SETUP_SQL,
    InsecureConnectionError,
    apply_migrations,
    connect_tiger,
    event_params,
    row_to_event,
    row_to_setup,
    rows_to_summary,
    setup_params,
)

COUNT_SQL = "SELECT count(*) FROM teachback_setups"
RAW_INSERT_SQL = "INSERT INTO teachback_setups (id, name, objects, created_at) VALUES (%s, %s, %s, NOW())"
PROBE_CHECKS_SQL = "SELECT count(*) FROM teachback_setup_checks WHERE setup_id = %s"


class CheckFailed(Exception):
    pass


def _verify_setups(conn, statement_timeout_ms: int) -> str:
    probe_id = f"teachback-check-{secrets.token_hex(4)}"
    probe = SavedSetup(id=probe_id, name=probe_id, created_at=time.time(),
                       objects=[SetupObject(label="check probe", zone="A")])
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(SET_STATEMENT_TIMEOUT_SQL, (str(statement_timeout_ms),))
        cur.execute(COUNT_SQL)
        cur.fetchone()
        cur.execute(UPSERT_SETUP_SQL, setup_params(probe))
        inserted = row_to_setup(cur.fetchone())
        cur.execute(UPSERT_SETUP_SQL, setup_params(probe))  # ON CONFLICT ... DO UPDATE path
        cur.fetchone()
        cur.execute(SELECT_SETUP_SQL, (probe_id,))
        read_back = row_to_setup(cur.fetchone())
        if inserted.objects != probe.objects or read_back.objects != probe.objects:
            raise CheckFailed("read-back did not match the inserted setup")
        try:
            with conn.transaction():  # savepoint: the bad row must be refused by the schema
                cur.execute(RAW_INSERT_SQL, ("Not A Slug!", "bad", Jsonb([])))
        except psycopg.errors.CheckViolation:
            pass
        else:
            raise CheckFailed("the schema accepted an invalid setup row")
        raise psycopg.Rollback()  # nothing from this transaction is kept
    return probe_id


def _verify_history(conn, statement_timeout_ms: int) -> str:
    probe_setup = f"teachback-check-{secrets.token_hex(4)}"
    now = time.time()
    events = [
        SetupCheckEvent(event_id=uuid.uuid4(), checked_at=now - 60, setup_id=probe_setup, setup_name=probe_setup,
                        status="complete", correct=(SetupObject(label="check probe", zone="A"),)),
        SetupCheckEvent(event_id=uuid.uuid4(), checked_at=now, setup_id=probe_setup, setup_name=probe_setup,
                        status="needs_attention", missing=(SetupObject(label="check probe", zone="A"),),
                        misplaced=(MisplacedObject(label="probe two", expected_zone="B", observed_zone="C"),)),
    ]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(SET_STATEMENT_TIMEOUT_SQL, (str(statement_timeout_ms),))
        for event in events:
            cur.execute(INSERT_CHECK_SQL, event_params(event))
            if cur.fetchone() is None:
                raise CheckFailed("a new check event was not inserted")
        cur.execute(INSERT_CHECK_SQL, event_params(events[0]))  # retry of the same event: no second row
        if cur.fetchone() is not None:
            raise CheckFailed("a duplicate event_id was inserted twice")
        cur.execute(RECENT_CHECKS_SQL, (probe_setup, 5))
        recent = [row_to_event(row) for row in cur.fetchall()]  # every row validated
        if [e.event_id for e in recent] != [events[1].event_id, events[0].event_id] or \
                [(e.status, e.missing, e.misplaced) for e in recent] != [(e.status, e.missing, e.misplaced) for e in events[::-1]]:
            raise CheckFailed("recent history did not match the inserted events")
        cur.execute(SUMMARY_TOTALS_SQL, ("complete", probe_setup))
        totals = cur.fetchone()
        cur.execute(SUMMARY_BUCKETS_SQL, (timedelta(hours=BUCKET_HOURS), "complete", probe_setup,
                                          timedelta(hours=SUMMARY_WINDOW_HOURS)))
        summary, errors = rows_to_summary(probe_setup, totals, cur.fetchall())  # every row validated
        if errors or summary is None:
            raise CheckFailed("time_bucket summary rows failed validation")
        if (summary.total_checks, summary.complete_checks, summary.readiness_percent) != (2, 1, 50.0) or \
                sum(b.total for b in summary.buckets) != 2:
            raise CheckFailed("time_bucket summary did not match the inserted events")
        raise psycopg.Rollback()  # probe events are never kept
    return probe_setup


def run(url: str, connect: Callable = connect_tiger, out: Callable[[str], None] = print,
        connect_timeout: int = 5, statement_timeout_ms: int = 5000) -> int:
    if not url:
        out("FAIL TIGER_DATABASE_URL is not set. Copy the connection string from Tiger Cloud into .env "
            "(never commit it), then run npm run tiger:check again.")
        return 2
    step = "connect"
    try:
        with connect(url, connect_timeout) as conn:
            if not conn.pgconn.ssl_in_use:  # verified on the live connection, whatever the factory
                raise InsecureConnectionError("connection is not using TLS")
            conn.autocommit = True  # explicit transactions below
            out("PASS connected to Tiger Data with TLS")

            step = "migrate"
            for _ in range(2):  # the second pass proves every migration is idempotent
                with conn.transaction():
                    applied = apply_migrations(conn)
            out(f"PASS migrations applied in order and re-applied without changes: {', '.join(applied)}")

            step = "verify setups"
            probe_id = _verify_setups(conn, statement_timeout_ms)
            out("PASS setups: select / insert / upsert / read-back validation / CHECK constraint (rolled back)")

            step = "verify hypertable"
            with conn.cursor() as cur:
                cur.execute(HYPERTABLE_SQL, (CHECK_TABLE,))
                if cur.fetchone()[0] != 1:
                    raise CheckFailed(f"{CHECK_TABLE} is not a TigerData hypertable")
                cur.execute(PARTITION_COLUMN_SQL, (CHECK_TABLE,))
                if [row[0] for row in cur.fetchall()] != ["checked_at"]:
                    raise CheckFailed(f"{CHECK_TABLE} is not partitioned on checked_at")
            out(f"PASS {CHECK_TABLE} is a TigerData hypertable partitioned on checked_at")

            step = "verify history"
            probe_setup = _verify_history(conn, statement_timeout_ms)
            out("PASS history: two events inserted, duplicate ignored, recent read, time_bucket summary, "
                "all rows validated (rolled back)")

            step = "cleanup"
            with conn.cursor() as cur:
                cur.execute(SELECT_SETUP_SQL, (probe_id,))
                if cur.fetchone() is not None:
                    raise CheckFailed("probe setup still present after rollback")
                cur.execute(PROBE_CHECKS_SQL, (probe_setup,))
                if cur.fetchone()[0] != 0:
                    raise CheckFailed("probe check events still present after rollback")
            out("PASS no test setup or check event left in the database")
    except CheckFailed as exc:
        out(f"FAIL at {step}: {exc}")
        return 1
    except Exception as exc:  # sanitized: psycopg messages may contain host or user names
        out(f"FAIL at {step}: {type(exc).__name__}")
        return 1
    out("Tiger Data check passed.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(run(settings.tiger_database_url, connect_timeout=settings.tiger_connect_timeout,
                 statement_timeout_ms=settings.tiger_statement_timeout_ms))
