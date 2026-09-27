"""Setup Check history: immutable check events, readiness summaries, and a bounded background writer.

The live Setup Check verdict never waits for history. A finished check becomes one SetupCheckEvent
(one event_id), is handed to a single background worker through a bounded queue, and is persisted by a
CheckHistoryRepository. Tiger Data provides persistence and time_bucket analytics; without Tiger,
history is explicitly disabled (never silently kept somewhere else and called Tiger history).
No SQL lives in this module.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from pydantic import ConfigDict, Field, model_validator

from .models import CamelModel
from .setups import SETUP_ID, MisplacedObject, SetupCheckResult, SetupObject

if TYPE_CHECKING:
    from .config import Settings

log = logging.getLogger("teachback.history")

RECENT_LIMIT = 5
SUMMARY_WINDOW_HOURS = 24
BUCKET_HOURS = 1


class SetupCheckEvent(CamelModel):
    """One completed Setup Check, frozen at the moment of the verdict. Append-only."""

    model_config = ConfigDict(frozen=True)

    event_id: uuid.UUID
    checked_at: float  # epoch seconds (TIMESTAMPTZ in Tiger)
    setup_id: str
    setup_name: str = Field(min_length=1, max_length=60)  # snapshot: later renames don't rewrite history
    status: Literal["complete", "needs_attention"]
    correct: tuple[SetupObject, ...] = ()
    missing: tuple[SetupObject, ...] = ()
    unexpected: tuple[SetupObject, ...] = ()
    misplaced: tuple[MisplacedObject, ...] = ()

    @model_validator(mode="after")
    def consistent(self):
        if not SETUP_ID.match(self.setup_id) or len(self.setup_id) > 40:
            raise ValueError("setup_id must be a setup slug")
        # Same rule as SetupCheckResult: complete exactly when nothing is wrong and something is right.
        clean = not (self.missing or self.unexpected or self.misplaced) and bool(self.correct)
        if (self.status == "complete") != clean:
            raise ValueError("status does not match the recorded findings")
        return self

    @classmethod
    def from_result(cls, result: SetupCheckResult, event_id: uuid.UUID | None = None) -> "SetupCheckEvent":
        return cls(
            event_id=event_id or uuid.uuid4(),
            checked_at=result.checked_at,
            setup_id=result.setup_id,
            setup_name=result.setup_name,
            status=result.status,
            correct=tuple(result.correct),
            missing=tuple(result.missing),
            unexpected=tuple(result.unexpected),
            misplaced=tuple(result.misplaced),
        )


class ReadinessBucket(CamelModel):
    """One time_bucket row: checks that started in [bucket_start, bucket_start + 1 h)."""

    model_config = ConfigDict(frozen=True)

    bucket_start: float
    total: int = Field(ge=1)
    complete: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if self.complete > self.total:
            raise ValueError("bucket has more complete checks than checks")
        return self


class ReadinessSummary(CamelModel):
    model_config = ConfigDict(frozen=True)

    setup_id: str
    total_checks: int = Field(ge=0)
    complete_checks: int = Field(ge=0)
    needs_attention_checks: int = Field(ge=0)
    readiness_percent: float | None  # None when there are no checks yet
    latest_checked_at: float | None
    window_hours: int = SUMMARY_WINDOW_HOURS
    bucket_hours: int = BUCKET_HOURS
    buckets: tuple[ReadinessBucket, ...] = ()

    @model_validator(mode="after")
    def consistent(self):
        if self.complete_checks + self.needs_attention_checks != self.total_checks:
            raise ValueError("complete + needs attention must equal total checks")
        expected = round(100 * self.complete_checks / self.total_checks, 1) if self.total_checks else None
        if self.readiness_percent != expected:
            raise ValueError("readiness percent does not match the counts")
        if (self.latest_checked_at is None) != (self.total_checks == 0):
            raise ValueError("latest check time must exist exactly when there are checks")
        if sum(b.total for b in self.buckets) > self.total_checks:
            raise ValueError("recent buckets exceed the total")
        return self

    @classmethod
    def from_counts(cls, setup_id: str, total: int, complete: int, latest: float | None,
                    buckets: tuple[ReadinessBucket, ...]) -> "ReadinessSummary":
        return cls(setup_id=setup_id, total_checks=total, complete_checks=complete,
                   needs_attention_checks=total - complete,
                   readiness_percent=round(100 * complete / total, 1) if total else None,
                   latest_checked_at=latest, buckets=buckets)


class HistoryStatus(CamelModel):
    """Credential-free: where history lives and whether it works."""

    provider: Literal["tiger", "local"]
    state: Literal["ready", "disabled", "error"]
    message: str = Field(max_length=200)


class HistoryWriteError(Exception):
    """A history write failed. The message is safe to show: no connection details."""


class CheckHistoryRepository(Protocol):
    """Append-only history of Setup Check events.

    `recent`/`summary` must be served from validated in-memory caches (they run on every snapshot).
    `record` and `refresh` may touch the database and are only called by the HistoryWriter thread.
    """

    errors: list[str]

    def record(self, event: SetupCheckEvent) -> bool: ...  # True: persisted (inserted or already present)
    def recent(self, setup_id: str, limit: int = RECENT_LIMIT) -> list[SetupCheckEvent]: ...
    def summary(self, setup_id: str) -> ReadinessSummary | None: ...
    def refresh(self, setup_id: str | None = None) -> None: ...
    def status(self) -> HistoryStatus: ...
    def close(self) -> None: ...


class DisabledCheckHistory:
    """Local mode: checks still work, but nothing is persisted and nothing pretends to be."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def record(self, event: SetupCheckEvent) -> bool:
        raise HistoryWriteError("History is disabled without Tiger Data.")

    def recent(self, setup_id: str, limit: int = RECENT_LIMIT) -> list[SetupCheckEvent]:
        return []

    def summary(self, setup_id: str) -> ReadinessSummary | None:
        return None

    def refresh(self, setup_id: str | None = None) -> None:
        pass

    def status(self) -> HistoryStatus:
        return HistoryStatus(provider="local", state="disabled",
                             message="History needs Tiger Data (TIGER_DATABASE_URL). Setup checks still work.")

    def close(self) -> None:
        pass


def create_check_history_repository(cfg: "Settings", connect: Callable | None = None) -> CheckHistoryRepository:
    if not cfg.tiger_database_url:
        return DisabledCheckHistory()
    from .tiger import TigerCheckHistoryRepository  # psycopg is only imported when Tiger is configured

    repo = TigerCheckHistoryRepository(cfg.tiger_database_url, connect=connect,
                                       connect_timeout=cfg.tiger_connect_timeout,
                                       statement_timeout_ms=cfg.tiger_statement_timeout_ms)
    repo.refresh()  # startup boundary: verifies the hypertable exists
    return repo


# -- background writer --------------------------------------------------------------------------------

EventState = Literal["pending", "saved", "failed", "dropped", "disabled"]


@dataclass(frozen=True)
class _Record:
    event: SetupCheckEvent


@dataclass(frozen=True)
class _Refresh:
    setup_id: str | None


_STOP = object()


class HistoryWriter:
    """Exactly one worker thread and one bounded queue for all history I/O.

    * submit()/request_refresh() never block: a full queue drops the job and says so (visible, counted).
    * each event_id is accepted at most once, so re-sent frames or snapshots can't duplicate it.
    * close() stops intake, lets the worker drain accepted jobs, and joins within a timeout.
    """

    def __init__(self, repo: CheckHistoryRepository, maxsize: int = 32,
                 on_change: Callable[[], None] | None = None, remember: int = 256) -> None:
        self.repo = repo
        self.on_change = on_change
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._states: OrderedDict[uuid.UUID, EventState] = OrderedDict()
        self._remember = remember
        self._queued_refreshes: set[str | None] = set()
        self._closed = False
        self.saved = self.failed = self.dropped = 0
        self.last_problem = ""
        self._thread = threading.Thread(target=self._run, daemon=True, name="history-writer")
        self._thread.start()

    # -- producer side (called under Session.lock: must never block or do I/O) ------------------

    def submit(self, event: SetupCheckEvent) -> EventState:
        with self._lock:
            if event.event_id in self._states:  # same check again: never a second event
                return self._states[event.event_id]
            if self.repo.status().state == "disabled":
                state: EventState = "disabled"
            elif self._closed:
                state = self._drop("History is shutting down; the check was not recorded.")
            else:
                try:
                    self._queue.put_nowait(_Record(event))
                    state = "pending"
                except queue.Full:
                    state = self._drop(f"History queue full ({self._queue.maxsize}); a check was not recorded.")
            self._remember_state(event.event_id, state)
            return state

    def request_refresh(self, setup_id: str | None) -> bool:
        """Ask the worker to reload caches for a setup. Coalesced; never blocks."""
        with self._lock:
            if self._closed or self.repo.status().state == "disabled" or setup_id in self._queued_refreshes:
                return False
            try:
                self._queue.put_nowait(_Refresh(setup_id))
            except queue.Full:
                return False  # a refresh is best-effort; the next boundary will ask again
            self._queued_refreshes.add(setup_id)
            return True

    def wait_idle(self, timeout: float) -> bool:
        """Block until every accepted job has been processed (tests, orderly shutdown)."""
        with self._queue.all_tasks_done:
            return self._queue.all_tasks_done.wait_for(lambda: self._queue.unfinished_tasks == 0, timeout)

    def event_state(self, event_id: uuid.UUID | None) -> EventState | None:
        with self._lock:
            return self._states.get(event_id) if event_id else None

    def stats(self) -> dict:
        with self._lock:
            return {"queued": self._queue.qsize(), "capacity": self._queue.maxsize, "saved": self.saved,
                    "failed": self.failed, "dropped": self.dropped, "lastProblem": self.last_problem}

    def _drop(self, why: str) -> EventState:
        self.dropped += 1
        self.last_problem = why
        log.warning(why)
        return "dropped"

    def _remember_state(self, event_id: uuid.UUID, state: EventState) -> None:
        self._states[event_id] = state
        self._states.move_to_end(event_id)
        while len(self._states) > self._remember:
            self._states.popitem(last=False)

    # -- worker side ------------------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is _STOP:
                    return
                if isinstance(job, _Record):
                    self._write(job.event)
                else:
                    with self._lock:
                        self._queued_refreshes.discard(job.setup_id)
                    self.repo.refresh(job.setup_id)  # repository catches and reports its own failures
            except Exception as exc:  # the worker must survive anything; message stays sanitized
                log.warning("History worker error (%s).", type(exc).__name__)
            finally:
                self._queue.task_done()
            if self.on_change:
                try:
                    self.on_change()
                except Exception:
                    pass

    def _write(self, event: SetupCheckEvent) -> None:
        problem = "History write was not confirmed."
        try:
            persisted = self.repo.record(event)
        except HistoryWriteError as exc:
            persisted, problem = False, str(exc)
        except Exception as exc:
            persisted, problem = False, f"History write failed ({type(exc).__name__})."
        with self._lock:
            if persisted:
                self.saved += 1
                self._states[event.event_id] = "saved"
            else:
                self.failed += 1
                self.last_problem = problem
                self._states[event.event_id] = "failed"
        if persisted:
            self.repo.refresh(event.setup_id)  # successful-write boundary: reload recent + summary

    def close(self, timeout: float = 5.0) -> bool:
        """Stop intake, drain what was accepted, join. True when the worker finished in time."""
        with self._lock:
            self._closed = True
        deadline = time.monotonic() + timeout
        try:
            self._queue.put(_STOP, timeout=max(0.0, deadline - time.monotonic()))
        except queue.Full:
            pass
        self._thread.join(max(0.0, deadline - time.monotonic()))
        drained = not self._thread.is_alive()
        if not drained:
            log.warning("History writer did not drain within %.1fs; %d jobs left.", timeout, self._queue.qsize())
        self.repo.close()
        return drained
