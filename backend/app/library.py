"""Procedure Library: named, saved copies of learned procedures that can be loaded back for Practice.

Kept apart from the active procedure (procedure.json), from Setup Check and from its history. Saving never
changes what was learned: a SavedProcedure wraps an unmodified, validated Procedure plus editable metadata.
No SQL lives here; Tiger persistence is in tiger.py.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from pydantic import Field, ValidationError, field_validator, model_validator

from .models import CamelModel, Procedure
from .setups import SETUP_ID, StorageStatus, setup_id_for

if TYPE_CHECKING:
    from .config import Settings

MAX_STEPS = 20
MAX_OBJECTS = 12
MAX_TAGS = 3
TAG = re.compile(r"^[a-z0-9][a-z0-9 -]{0,23}$")
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")

procedure_id_for = setup_id_for  # same slug rule: stable, filesystem-safe, derived from the name


def clean_text(value: str) -> str:
    return " ".join(str(value).split())


def validate_procedure(procedure: Procedure) -> Procedure:
    """A procedure worth saving: at least one step, consistent indexes, one detector, sane sizes."""
    if not procedure.steps or len(procedure.steps) > MAX_STEPS:
        raise ValueError(f"A saved procedure needs 1-{MAX_STEPS} steps")
    if not procedure.tracked_ids or len(procedure.tracked_ids) > MAX_OBJECTS \
            or len(set(procedure.tracked_ids)) != len(procedure.tracked_ids):
        raise ValueError("A saved procedure needs unique tracked objects")
    if [s.index for s in procedure.steps] != list(range(len(procedure.steps))):
        raise ValueError("Step indexes must run 0..n-1")
    kinds = {o.kind for o in procedure.initial_state.objects}
    kinds |= {o.kind for s in procedure.steps for o in (*s.before_state.objects, *s.after_state.objects)}
    if len(kinds) > 1:
        raise ValueError("A procedure must come from one detector")
    return procedure


class SavedProcedure(CamelModel):
    version: Literal[1] = 1
    id: str
    name: str = Field(min_length=1, max_length=60)
    summary: str = Field(default="", max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=MAX_TAGS)
    procedure: Procedure
    created_at: float
    updated_at: float
    ai_generated_metadata: bool = False  # true only when a validated Gemini suggestion was saved unchanged

    @field_validator("name", "summary")
    @classmethod
    def tidy(cls, v: str) -> str:
        return clean_text(v)

    @field_validator("tags")
    @classmethod
    def tidy_tags(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        tags = tuple(clean_text(t).lower() for t in v)
        if any(not TAG.match(t) for t in tags) or len(set(tags)) != len(tags):
            raise ValueError("Tags must be short, unique, lowercase words")
        return tags

    @model_validator(mode="after")
    def consistent(self):
        if not SETUP_ID.match(self.id) or self.id != procedure_id_for(self.name):
            raise ValueError("Procedure id must be derived from its name")
        validate_procedure(self.procedure)
        if self.updated_at < self.created_at:
            raise ValueError("updated_at is before created_at")
        return self

    @property
    def detector_kind(self) -> str:
        return self.procedure.detector_kind

    def card(self) -> dict:
        """Compact, credential-free view for library cards (no scene states)."""
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "tags": list(self.tags),
            "detectorKind": self.procedure.detector_kind,
            "objectCount": len(self.procedure.tracked_ids),
            "stepCount": len(self.procedure.steps),
            "objects": list(self.procedure.tracked_ids),
            "updatedAt": self.updated_at,
            "aiGeneratedMetadata": self.ai_generated_metadata,
        }


class ProcedureStorageError(Exception):
    """A save failed. The message is safe to show: it never contains connection details."""


class ProcedureRepository(Protocol):
    """Named procedures: local JSON files, or Tiger Cloud when configured.

    `list`/`get` serve a validated in-memory cache (they run on every snapshot); only `refresh` and `save`
    touch storage. Kept separate from SetupRepository on purpose.
    """

    errors: list[str]

    def list(self) -> list[SavedProcedure]: ...
    def get(self, procedure_id: str) -> SavedProcedure | None: ...
    def save(self, saved: SavedProcedure) -> SavedProcedure: ...  # returns what storage now holds
    def refresh(self) -> None: ...
    def status(self) -> StorageStatus: ...


def _newest_first(items) -> list[SavedProcedure]:
    return sorted(items, key=lambda p: (-p.updated_at, p.name.casefold()))


class JsonProcedureRepository:
    """One validated JSON file per procedure. Unreadable files are skipped and reported, never fatal."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.errors: list[str] = []
        self._cache: dict[str, SavedProcedure] | None = None

    def _load(self) -> dict[str, SavedProcedure]:
        if self._cache is None:
            self._cache, self.errors = {}, []
            for path in sorted(self.directory.glob("*.json")) if self.directory.is_dir() else []:
                try:
                    saved = SavedProcedure.model_validate(json.loads(path.read_text(encoding="utf-8")))
                    if saved.id != path.stem:
                        raise ValueError("file name does not match procedure id")
                    self._cache[saved.id] = saved
                except (OSError, ValueError, ValidationError) as exc:
                    self.errors.append(f"{path.name}: {str(exc).splitlines()[0]}")
        return self._cache

    def list(self) -> list[SavedProcedure]:
        return _newest_first(self._load().values())

    def get(self, procedure_id: str) -> SavedProcedure | None:
        return self._load().get(procedure_id) if SETUP_ID.match(procedure_id or "") else None

    def refresh(self) -> None:
        self._cache = None

    def status(self) -> StorageStatus:
        return StorageStatus(provider="local", state="ready", message="Procedures saved as local JSON on this computer.")

    def save(self, saved: SavedProcedure) -> SavedProcedure:
        existing = self._load().get(saved.id)
        if existing is not None:  # an explicit upsert keeps the original creation time
            saved = saved.model_copy(update={"created_at": existing.created_at})
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{saved.id}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(saved.to_json(), indent=2), encoding="utf-8")
        os.replace(tmp, target)  # atomic: a crash never leaves a half-written procedure
        self._load()[saved.id] = saved
        return saved


def create_procedure_repository(cfg: "Settings", local_dir: Path, connect: Callable | None = None) -> ProcedureRepository:
    """Tiger Cloud when TIGER_DATABASE_URL is configured, otherwise local JSON. A configured-but-unreachable
    Tiger database stays selected in the error state; saves are refused, never written locally instead."""
    if not cfg.tiger_database_url:
        return JsonProcedureRepository(local_dir)
    from .tiger import TigerProcedureRepository  # psycopg is only imported when Tiger is configured

    repo = TigerProcedureRepository(cfg.tiger_database_url, connect=connect,
                                    connect_timeout=cfg.tiger_connect_timeout,
                                    statement_timeout_ms=cfg.tiger_statement_timeout_ms)
    repo.refresh()
    return repo


# -- draft metadata ---------------------------------------------------------------------------------------

SuggestionState = Literal["none", "generating", "suggested", "unavailable", "rejected"]


class ProcedureMetadata(CamelModel):
    name: str = Field(min_length=1, max_length=60)
    summary: str = Field(default="", max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=MAX_TAGS)


def fallback_metadata(procedure: Procedure) -> ProcedureMetadata:
    """Deterministic name/summary used when Gemini is absent, fails, or its answer is rejected."""
    n = len(procedure.steps)
    count = NUMBER_WORDS[n].capitalize() if n < len(NUMBER_WORDS) else str(n)
    kind = "color-block" if procedure.detector_kind == "color" else "object"
    objects = ", ".join(procedure.tracked_ids[:4])
    return ProcedureMetadata(
        name=f"{count}-step {kind} procedure",
        summary=f"{n} ordered step{'s' if n != 1 else ''} using {objects}."[:200],
        tags=(procedure.detector_kind,),
    )
