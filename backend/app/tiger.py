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
import threading
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .config import BACKEND_ROOT
from .setups import SavedSetup, SetupStorageError, StorageStatus

log = logging.getLogger("teachback.tiger")

MIGRATION_FILE = BACKEND_ROOT / "sql" / "001_tiger_setups.sql"

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


def migration_statements() -> list[str]:
    """The migration file split into statements (it has no functions or dollar-quoting)."""
    text = MIGRATION_FILE.read_text(encoding="utf-8")
    statements = []
    for chunk in text.split(";\n"):
        lines = chunk.strip().splitlines()
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
            lines.pop(0)  # drop the leading comment block; comments inside a statement are kept
        if lines:
            statements.append("\n".join(lines).strip())
    return statements


def apply_migration(conn: Any) -> None:
    with conn.cursor() as cur:
        for statement in migration_statements():
            cur.execute(statement)


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
