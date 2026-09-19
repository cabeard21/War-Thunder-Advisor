"""Bounded, strict resolved-availability imports."""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from wt_advisor.data.models import RawAvailabilityDataset, RawDatasetMetadata
from wt_advisor.domain.models import DatasetType, ResolvedAvailability

MAX_AVAILABILITY_IMPORT_BYTES = 2_000_000
MAX_AVAILABILITY_IMPORT_ROWS = 50_000


def import_availability_yaml(
    payload: str | bytes, metadata: RawDatasetMetadata
) -> RawAvailabilityDataset:
    content = payload.encode() if isinstance(payload, str) else payload
    if len(content) > MAX_AVAILABILITY_IMPORT_BYTES:
        raise ValueError("availability import exceeds configured byte limit")
    if metadata.dataset_type is not DatasetType.AVAILABILITY:
        raise ValueError("availability metadata must use availability dataset type")
    try:
        decoded = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid availability YAML: {exc}") from exc
    if not isinstance(decoded, list) or not all(isinstance(row, dict) for row in decoded):
        raise ValueError("availability YAML must be an array of objects")
    if len(decoded) > MAX_AVAILABILITY_IMPORT_ROWS:
        raise ValueError("availability import exceeds configured row limit")
    try:
        records = tuple(
            ResolvedAvailability.model_validate(
                {
                    **_object(row),
                    "source_snapshot_id": metadata.snapshot_id,
                }
            )
            for row in decoded
        )
        return RawAvailabilityDataset(
            snapshot=metadata.snapshot(content), records=records, raw_content=content
        )
    except ValidationError as exc:
        raise ValueError(f"invalid availability observation: {exc}") from exc


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("import record must be an object")
    return value
