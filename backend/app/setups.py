"""Setup Check: what an organized workspace should contain, where it is saved, and how a scan is judged.

Judging is a deterministic comparison of labels and zones. No model or LLM decides correctness.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, ValidationError, field_validator, model_validator

from .models import CamelModel, SceneState

SETUP_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_OBJECTS = 6


def setup_id_for(name: str) -> str:
    """Stable, filesystem-safe id derived from the name; re-capturing a name updates that setup."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40].strip("-")
    if not slug:
        raise ValueError("Setup name needs at least one letter or digit.")
    return slug


class SetupObject(CamelModel):
    label: str = Field(min_length=1, max_length=80)
    zone: str | None = Field(default=None, max_length=8)


class MisplacedObject(CamelModel):
    label: str
    expected_zone: str | None
    observed_zone: str | None


class SavedSetup(CamelModel):
    id: str
    name: str = Field(min_length=1, max_length=60)
    objects: list[SetupObject] = Field(min_length=1, max_length=MAX_OBJECTS)
    created_at: float

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return " ".join(v.split())

    @model_validator(mode="after")
    def consistent(self):
        if not SETUP_ID.match(self.id) or self.id != setup_id_for(self.name):
            raise ValueError("Setup id must be derived from its name")
        if len({o.label.casefold() for o in self.objects}) != len(self.objects):
            raise ValueError("Each expected object must be unique")
        return self

    @classmethod
    def from_scene(cls, name: str, scene: SceneState, now: float) -> "SavedSetup":
        if not scene.objects or any(o.kind != "semantic" for o in scene.objects):
            raise ValueError("Capture needs an accepted semantic-object scan.")
        name = " ".join(name.split())
        return cls(
            id=setup_id_for(name),
            name=name,
            objects=[SetupObject(label=o.label, zone=o.zone) for o in scene.objects if o.visible],
            created_at=now,
        )


class SetupCheckResult(CamelModel):
    setup_id: str
    setup_name: str
    status: Literal["complete", "needs_attention"]
    correct: list[SetupObject] = Field(default_factory=list)
    missing: list[SetupObject] = Field(default_factory=list)
    unexpected: list[SetupObject] = Field(default_factory=list)
    misplaced: list[MisplacedObject] = Field(default_factory=list)
    checked_at: float

    @model_validator(mode="after")
    def complete_means_no_findings(self):
        clean = not (self.missing or self.unexpected or self.misplaced) and bool(self.correct)
        if (self.status == "complete") != clean:
            raise ValueError("A setup is complete only when every expected object is present and in place")
        return self


def check_setup(setup: SavedSetup, scene: SceneState, now: float) -> SetupCheckResult:
    """Compare an accepted scan with the saved setup by label (case-insensitive) and zone."""
    if any(o.kind != "semantic" for o in scene.objects):
        raise ValueError("Setup checks need a semantic-object scan.")
    observed = {o.label.casefold(): o for o in scene.objects if o.visible}
    expected = {o.label.casefold(): o for o in setup.objects}
    correct, missing, misplaced = [], [], []
    for key, want in expected.items():
        seen = observed.get(key)
        if seen is None:
            missing.append(want)
        elif seen.zone != want.zone:
            misplaced.append(MisplacedObject(label=want.label, expected_zone=want.zone, observed_zone=seen.zone))
        else:
            correct.append(want)
    unexpected = [SetupObject(label=o.label, zone=o.zone) for key, o in observed.items() if key not in expected]
    complete = not (missing or unexpected or misplaced) and len(correct) == len(expected)
    return SetupCheckResult(
        setup_id=setup.id,
        setup_name=setup.name,
        status="complete" if complete else "needs_attention",
        correct=correct,
        missing=missing,
        unexpected=unexpected,
        misplaced=misplaced,
        checked_at=now,
    )


class SetupRepository(Protocol):
    """Where saved setups live. The local JSON store is the only implementation for now."""

    errors: list[str]

    def list(self) -> list[SavedSetup]: ...
    def get(self, setup_id: str) -> SavedSetup | None: ...
    def save(self, setup: SavedSetup) -> None: ...


class JsonSetupRepository:
    """One validated JSON file per setup. Unreadable files are skipped and reported, never fatal."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.errors: list[str] = []
        self._cache: dict[str, SavedSetup] | None = None

    def _load(self) -> dict[str, SavedSetup]:
        if self._cache is None:
            self._cache, self.errors = {}, []
            for path in sorted(self.directory.glob("*.json")) if self.directory.is_dir() else []:
                try:
                    setup = SavedSetup.model_validate(json.loads(path.read_text(encoding="utf-8")))
                    if setup.id != path.stem:
                        raise ValueError("file name does not match setup id")
                    self._cache[setup.id] = setup
                except (OSError, ValueError, ValidationError) as exc:
                    self.errors.append(f"{path.name}: {str(exc).splitlines()[0]}")
        return self._cache

    def list(self) -> list[SavedSetup]:
        return sorted(self._load().values(), key=lambda s: s.name.casefold())

    def get(self, setup_id: str) -> SavedSetup | None:
        return self._load().get(setup_id) if SETUP_ID.match(setup_id or "") else None

    def save(self, setup: SavedSetup) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{setup.id}.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(setup.to_json(), indent=2), encoding="utf-8")
        os.replace(tmp, target)  # atomic: a crash never leaves a half-written setup
        self._load()[setup.id] = setup
