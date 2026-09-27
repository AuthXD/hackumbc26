"""Explicit Tiger Data migration + verification:  npm run tiger:check

1. Fails clearly when TIGER_DATABASE_URL is missing.
2. Connects with TLS required (and verified in use).
3. Applies backend/sql/001_tiger_setups.sql twice inside transactions (it is idempotent).
4. Inside one transaction that is always rolled back: SELECT, INSERT, upsert (conflict path), read back
   through SavedSetup validation, and confirm a CHECK constraint rejects an invalid row.
5. Confirms the probe row is gone afterwards.

Only sanitized messages are printed: never the URL, host, user, or password.
"""

from __future__ import annotations

import secrets
import sys
import time
from typing import Callable

import psycopg
from psycopg.types.json import Jsonb

from app.config import settings
from app.setups import SavedSetup, SetupObject
from app.tiger import (
    InsecureConnectionError,
    SELECT_SETUP_SQL,
    SET_STATEMENT_TIMEOUT_SQL,
    UPSERT_SETUP_SQL,
    apply_migration,
    connect_tiger,
    row_to_setup,
    setup_params,
)

COUNT_SQL = "SELECT count(*) FROM teachback_setups"
RAW_INSERT_SQL = "INSERT INTO teachback_setups (id, name, objects, created_at) VALUES (%s, %s, %s, NOW())"


class CheckFailed(Exception):
    pass


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
            for _ in range(2):  # the second run proves the migration is idempotent
                with conn.transaction():
                    apply_migration(conn)
            out("PASS migration 001_tiger_setups applied (re-applied without changes: idempotent)")

            step = "verify"
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
            out("PASS select / insert / upsert / read-back validation / CHECK constraint (transaction rolled back)")

            step = "cleanup"
            with conn.cursor() as cur:
                cur.execute(SELECT_SETUP_SQL, (probe_id,))
                if cur.fetchone() is not None:
                    raise CheckFailed("probe row still present after rollback")
            out("PASS no test setup left in the database")
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
