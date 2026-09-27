"""Tiger Cloud (PostgreSQL) persistence for Setup Check saved setups.

Tiger Cloud is plain PostgreSQL, so this is psycopg 3 over TLS. Rules this module keeps:
  * the connection URL is never logged, returned, or put in an error message (psycopg messages can
    contain host and user names, so only exception class names are surfaced);
  * every statement is parameterized;
  * every transaction has a short statement timeout, and connections have a connect timeout;
  * list/get serve a validated in-memory cache; only refresh() and save() talk to the database.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .config import BACKEND_ROOT
from .setups import SavedSetup, SetupStorageError, StorageStatus

log = logging.getLogger("teachback.tiger")

MIGRATIONS_DIR = BACKEND_ROOT / "sql"
MIGRATION_FILE = MIGRATIONS_DIR / "001_tiger_setups.sql"

SET_STATEMENT_TIMEOUT_SQL = "SELECT set_config('statement_timeout', %s, true)"
SELECT_SETUPS_SQL = "SELECT id, name, objects, created_at FROM teachback_setups ORDER BY id"
SELECT_SETUP_SQL = "SELECT id, name, objects, created_at FROM teachback_setups WHERE id = %s"
UPSERT_SETUP_SQL = (
    "INSERT INTO teachback_setups (id, name, objects, created_at, updated_at) "
    "VALUES (%s, %s, %s, %s, NOW()) "
    "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, objects = EXCLUDED.objects, updated_at = NOW() "
    "RETURNING id, name, objects, created_at"
)

TLS_MODES = ("require", "verify-ca", "verify-full")


class InsecureConnectionError(Exception):
    """The server connection is not encrypted with TLS."""


def tls_conninfo(url: str, connect_timeout: int) -> str:
    """Force TLS: weaker sslmodes (disable/allow/prefer/unset) become 'require'; verify-* are kept."""
    requested = conninfo_to_dict(url).get("sslmode")
    sslmode = requested if requested in TLS_MODES else "require"
    return make_conninfo(url, sslmode=sslmode, connect_timeout=connect_timeout, application_name="teachback")


def connect_tiger(url: str, connect_timeout: int) -> psycopg.Connection:
    conn = psycopg.connect(tls_conninfo(url, connect_timeout))
    if not conn.pgconn.ssl_in_use:
        conn.close()
        raise InsecureConnectionError("connection is not using TLS")
    return conn


def migration_files() -> list[Path]:
    """Every numbered migration (NNN_name.sql) in backend/sql, in sorted order."""
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if re.match(r"^\d{3}_[a-z0-9_]+\.sql$", p.name))


def migration_statements(path: Path | None = None) -> list[str]:
    """One migration file split into statements (they use no functions or dollar-quoting)."""
    text = (path or MIGRATION_FILE).read_text(encoding="utf-8")
    statements = []
    for chunk in text.split(";\n"):
        lines = chunk.strip().splitlines()
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
            lines.pop(0)  # drop the leading comment block; comments inside a statement are kept
        if lines:
            statements.append("\n".join(lines).strip())
    return statements


def apply_migrations(conn: Any) -> list[str]:
    """Apply every numbered migration in order (each is idempotent). Returns the file names applied."""
    applied = []
    with conn.cursor() as cur:
        for path in migration_files():
            for statement in migration_statements(path):
                cur.execute(statement)
            applied.append(path.stem)
    return applied


def setup_params(setup: SavedSetup) -> tuple:
    return (
        setup.id,
        setup.name,
        Jsonb([o.to_json() for o in setup.objects]),
        datetime.fromtimestamp(setup.created_at, tz=timezone.utc),
    )


def row_to_setup(row: Any) -> SavedSetup:
    """Every row read from Tiger passes the same validation as a local JSON file."""
    setup_id, name, objects, created_at = row
    if isinstance(objects, (str, bytes)):
        objects = json.loads(objects)
    if isinstance(created_at, datetime):
        created_at = created_at.timestamp()
    return SavedSetup.model_validate({"id": setup_id, "name": name, "objects": objects, "createdAt": created_at})


def _row_error(row: Any, exc: Exception) -> str:
    raw_id = row[0] if isinstance(row, (tuple, list)) and row else None
    label = repr(raw_id[:40]) if isinstance(raw_id, str) else "(no id)"
    if isinstance(exc, ValidationError) and exc.errors():
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "row"
        return f"row {label}: {where}: {first['msg']}"
    return f"row {label}: {type(exc).__name__}"


def _is_rejection(exc: Exception) -> bool:
    """The database is reachable but refused this particular row (constraint or bad data)."""
    return isinstance(exc, (psycopg.errors.IntegrityError, psycopg.errors.DataError))


class TigerSetupRepository:
    provider = "tiger"

    def __init__(self, url: str, connect: Callable[[str, int], Any] | None = None,
                 connect_timeout: int = 5, statement_timeout_ms: int = 5000) -> None:
        self._url = url
        self._connect = connect or connect_tiger
        self._connect_timeout = connect_timeout
        self._statement_timeout_ms = statement_timeout_ms
        self._lock = threading.Lock()  # guards the cache and status only; never held during I/O
        self._cache: dict[str, SavedSetup] = {}
        self.errors: list[str] = []
        self._status = StorageStatus(provider="tiger", state="error", message="Tiger Data has not been loaded yet.")

    def __repr__(self) -> str:
        return "TigerSetupRepository(<connection details hidden>)"

    # -- database boundaries ---------------------------------------------------------------------

    def _open(self):
        return self._connect(self._url, self._connect_timeout)

    def refresh(self) -> None:
        """Reload every setup from Tiger (startup and explicit refresh only)."""
        try:
            with self._open() as conn, conn.cursor() as cur:
                cur.execute(SET_STATEMENT_TIMEOUT_SQL, (str(self._statement_timeout_ms),))
                cur.execute(SELECT_SETUPS_SQL)
                rows = cur.fetchall()
        except Exception as exc:
            self._fail("load", exc)
            return
        cache: dict[str, SavedSetup] = {}
        errors: list[str] = []
        for row in rows:
            try:
                setup = row_to_setup(row)
                cache[setup.id] = setup
            except Exception as exc:  # any malformed row is skipped and reported, never fatal
                errors.append(_row_error(row, exc))
        with self._lock:
            self._cache, self.errors = cache, errors
            self._status = StorageStatus(provider="tiger", state="ready",
                                         message=f"Loaded {len(cache)} saved setups from Tiger Data.")

    def save(self, setup: SavedSetup) -> None:
        """Upsert one setup. Raises SetupStorageError (safe message) unless Tiger confirmed the write."""
        try:
            with self._open() as conn, conn.cursor() as cur:
                cur.execute(SET_STATEMENT_TIMEOUT_SQL, (str(self._statement_timeout_ms),))
                cur.execute(UPSERT_SETUP_SQL, setup_params(setup))
                row = cur.fetchone()
            stored = row_to_setup(row)
        except Exception as exc:
            if _is_rejection(exc):
                raise SetupStorageError(f"Tiger Data rejected the setup ({type(exc).__name__}).") from None
            self._fail("save", exc)
            raise SetupStorageError("Tiger Data is unavailable, so the setup was not saved.") from None
        with self._lock:
            self._cache[stored.id] = stored
            self._status = StorageStatus(provider="tiger", state="ready",
                                         message=f"Saved \"{stored.name}\" to Tiger Data.")

    def _fail(self, action: str, exc: Exception) -> None:
        hint = " Run npm run tiger:check to create the table." if isinstance(exc, psycopg.errors.UndefinedTable) else ""
        message = f"Tiger Data unavailable: could not {action} setups ({type(exc).__name__}).{hint}"
        log.warning(message)  # class name only: psycopg messages may include host or user
        with self._lock:
            self._status = StorageStatus(provider="tiger", state="error", message=message[:200])

    # -- cache (called on every snapshot) ----------------------------------------------------------

    def list(self) -> list[SavedSetup]:
        with self._lock:
            return sorted(self._cache.values(), key=lambda s: s.name.casefold())

    def get(self, setup_id: str) -> SavedSetup | None:
        with self._lock:
            return self._cache.get(setup_id or "")

    def status(self) -> StorageStatus:
        with self._lock:
            return self._status


# -- Setup Check history (TigerData hypertable) -----------------------------------------------------

from datetime import timedelta  # noqa: E402

from .history import (  # noqa: E402
    BUCKET_HOURS,
    RECENT_LIMIT,
    SUMMARY_WINDOW_HOURS,
    HistoryStatus,
    HistoryWriteError,
    ReadinessBucket,
    ReadinessSummary,
    SetupCheckEvent,
)

CHECK_TABLE = "teachback_setup_checks"
CHECK_COLUMNS = "event_id, checked_at, setup_id, setup_name, status, correct, missing, unexpected, misplaced"
HYPERTABLE_SQL = "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name = %s"
PARTITION_COLUMN_SQL = "SELECT column_name FROM timescaledb_information.dimensions WHERE hypertable_name = %s"
INSERT_CHECK_SQL = (
    "INSERT INTO teachback_setup_checks "
    "(checked_at, event_id, setup_id, setup_name, status, correct, missing, unexpected, misplaced) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (event_id, checked_at) DO NOTHING RETURNING event_id"
)
RECENT_CHECKS_SQL = (
    f"SELECT {CHECK_COLUMNS} FROM teachback_setup_checks WHERE setup_id = %s ORDER BY checked_at DESC LIMIT %s"
)
SUMMARY_TOTALS_SQL = (
    "SELECT count(*), count(*) FILTER (WHERE status = %s), max(checked_at) "
    "FROM teachback_setup_checks WHERE setup_id = %s"
)
# TigerData time_bucket: hourly readiness over the recent window, computed in the database.
SUMMARY_BUCKETS_SQL = (
    "SELECT time_bucket(%s::interval, checked_at) AS bucket, count(*), count(*) FILTER (WHERE status = %s) "
    "FROM teachback_setup_checks WHERE setup_id = %s AND checked_at >= now() - %s::interval "
    "GROUP BY bucket ORDER BY bucket"
)


def event_params(event: SetupCheckEvent) -> tuple:
    def dump(items):
        return Jsonb([i.to_json() for i in items])

    return (
        datetime.fromtimestamp(event.checked_at, tz=timezone.utc),
        event.event_id,
        event.setup_id,
        event.setup_name,
        event.status,
        dump(event.correct),
        dump(event.missing),
        dump(event.unexpected),
        dump(event.misplaced),
    )


def _epoch(value: Any) -> Any:
    return value.timestamp() if isinstance(value, datetime) else value


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def row_to_event(row: Any) -> SetupCheckEvent:
    """Every history row passes SetupCheckEvent validation (status must match its findings)."""
    event_id, checked_at, setup_id, setup_name, status, correct, missing, unexpected, misplaced = row
    return SetupCheckEvent.model_validate({
        "eventId": event_id, "checkedAt": _epoch(checked_at), "setupId": setup_id, "setupName": setup_name,
        "status": status, "correct": _json(correct), "missing": _json(missing),
        "unexpected": _json(unexpected), "misplaced": _json(misplaced),
    })


def row_to_bucket(row: Any) -> ReadinessBucket:
    bucket, total, complete = row
    return ReadinessBucket.model_validate({"bucketStart": _epoch(bucket), "total": total, "complete": complete})


def rows_to_summary(setup_id: str, totals: Any, bucket_rows: list) -> tuple[ReadinessSummary | None, list[str]]:
    """Validated summary from the totals row and time_bucket rows. Invalid rows are reported, not trusted."""
    errors: list[str] = []
    buckets = []
    for row in bucket_rows:
        try:
            buckets.append(row_to_bucket(row))
        except Exception as exc:
            errors.append(_row_error(("bucket",), exc))
    try:
        total, complete, latest = totals
        summary = ReadinessSummary.from_counts(setup_id, total, complete, _epoch(latest), tuple(buckets))
    except Exception as exc:
        errors.append(_row_error(("summary",), exc))
        summary = None
    return summary, errors


class TigerCheckHistoryRepository:
    """Append-only Setup Check history in the teachback_setup_checks hypertable.

    Only the HistoryWriter thread calls record/refresh. recent/summary read validated caches.
    """

    provider = "tiger"

    def __init__(self, url: str, connect: Callable[[str, int], Any] | None = None,
                 connect_timeout: int = 5, statement_timeout_ms: int = 5000) -> None:
        self._url = url
        self._connect = connect or connect_tiger
        self._connect_timeout = connect_timeout
        self._statement_timeout_ms = statement_timeout_ms
        self._lock = threading.Lock()  # guards caches and status only; never held during I/O
        self._recent: dict[str, tuple[SetupCheckEvent, ...]] = {}
        self._summary: dict[str, ReadinessSummary] = {}
        self.errors: list[str] = []
        self._status = HistoryStatus(provider="tiger", state="error", message="History has not been loaded yet.")

    def __repr__(self) -> str:
        return "TigerCheckHistoryRepository(<connection details hidden>)"

    def _open(self):
        return self._connect(self._url, self._connect_timeout)

    def refresh(self, setup_id: str | None = None) -> None:
        """setup_id None: verify the hypertable (startup/retry). Otherwise reload that setup's caches."""
        try:
            with self._open() as conn, conn.cursor() as cur:
                cur.execute(SET_STATEMENT_TIMEOUT_SQL, (str(self._statement_timeout_ms),))
                if setup_id is None:
                    cur.execute(HYPERTABLE_SQL, (CHECK_TABLE,))
                    if cur.fetchone()[0] != 1:
                        self._set_status("error", "History table is missing or not a hypertable. "
                                                  "Run npm run tiger:check.")
                        return
                    self._set_status("ready", "History: Tiger Data hypertable ready.")
                    return
                cur.execute(RECENT_CHECKS_SQL, (setup_id, RECENT_LIMIT))
                recent_rows = cur.fetchall()
                cur.execute(SUMMARY_TOTALS_SQL, ("complete", setup_id))
                totals = cur.fetchone()
                cur.execute(SUMMARY_BUCKETS_SQL, (timedelta(hours=BUCKET_HOURS), "complete", setup_id,
                                                  timedelta(hours=SUMMARY_WINDOW_HOURS)))
                bucket_rows = cur.fetchall()
        except Exception as exc:
            self._fail("load history", exc)
            return
        errors: list[str] = []
        recent = []
        for row in recent_rows:
            try:
                recent.append(row_to_event(row))
            except Exception as exc:  # malformed rows are skipped and reported, never shown as history
                errors.append(_row_error(row, exc))
        summary, summary_errors = rows_to_summary(setup_id, totals, bucket_rows)
        with self._lock:
            self._recent[setup_id] = tuple(recent)
            if summary is not None:
                self._summary[setup_id] = summary
            else:
                self._summary.pop(setup_id, None)
            self.errors = errors + summary_errors
        self._set_status("ready", "History: Tiger Data hypertable ready.")

    def record(self, event: SetupCheckEvent) -> bool:
        """Append one event. True once Tiger confirms it (inserted now, or already stored by a retry)."""
        try:
            with self._open() as conn, conn.cursor() as cur:
                cur.execute(SET_STATEMENT_TIMEOUT_SQL, (str(self._statement_timeout_ms),))
                cur.execute(INSERT_CHECK_SQL, event_params(event))
                cur.fetchone()  # None means ON CONFLICT: this event_id was already recorded
        except Exception as exc:
            if _is_rejection(exc):
                raise HistoryWriteError(f"Tiger Data rejected the check event ({type(exc).__name__}).") from None
            self._fail("record history", exc)
            raise HistoryWriteError(f"History not saved: Tiger Data unavailable ({type(exc).__name__}).") from None
        return True

    def recent(self, setup_id: str, limit: int = RECENT_LIMIT) -> list[SetupCheckEvent]:
        with self._lock:
            return list(self._recent.get(setup_id, ()))[:limit]

    def summary(self, setup_id: str) -> ReadinessSummary | None:
        with self._lock:
            return self._summary.get(setup_id)

    def status(self) -> HistoryStatus:
        with self._lock:
            return self._status

    def close(self) -> None:
        pass  # one short-lived connection per operation; nothing to release

    def _set_status(self, state: str, message: str) -> None:
        with self._lock:
            self._status = HistoryStatus(provider="tiger", state=state, message=message[:200])

    def _fail(self, action: str, exc: Exception) -> None:
        hint = " Run npm run tiger:check." if isinstance(exc, psycopg.errors.UndefinedTable) else ""
        message = f"History unavailable: could not {action} ({type(exc).__name__}).{hint}"
        log.warning(message)  # class name only: psycopg messages may include host or user
        self._set_status("error", message)
