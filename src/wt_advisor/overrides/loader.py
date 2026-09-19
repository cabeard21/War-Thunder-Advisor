"""Load explicit YAML corrections without mutating provider observations."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError, model_validator

from wt_advisor.domain.models import FrozenModel

MAX_OVERRIDE_BYTES = 1_000_000
MAX_OVERRIDE_ENTRIES = 10_000


class OverrideEntry(FrozenModel):
    vehicle_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    field: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_.]*$")
    value: Any
    reason: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    added_at: date


class OverrideSet(FrozenModel):
    revision: str = Field(min_length=1)
    entries: tuple[OverrideEntry, ...]

    @model_validator(mode="after")
    def unique_targets(self) -> OverrideSet:
        targets = [(entry.vehicle_id, entry.field) for entry in self.entries]
        if len(targets) != len(set(targets)):
            raise ValueError("duplicate override target in the same revision")
        return self


def load_overrides(path: str | Path) -> OverrideSet:
    try:
        content = Path(path).read_bytes()
        if len(content) > MAX_OVERRIDE_BYTES:
            raise ValueError("override document exceeds configured byte limit")
        raw = yaml.safe_load(content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"unable to load overrides: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("override document must be a mapping")
    revision = raw.get("revision")
    entries = raw.get("overrides", ())
    if not isinstance(revision, str) or not revision:
        raise ValueError("override document requires a non-empty revision")
    if not isinstance(entries, (list, tuple)):
        raise ValueError("override document overrides must be an array")
    if len(entries) > MAX_OVERRIDE_ENTRIES:
        raise ValueError("override document exceeds configured entry limit")
    try:
        return OverrideSet(revision=revision, entries=tuple(entries))
    except ValidationError as exc:
        raise ValueError(f"invalid override document: {exc}") from exc
