"""Bounded, strict capability-enrichment imports."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from wt_advisor.data.models import RawCapabilityDataset, RawDatasetMetadata
from wt_advisor.domain.models import CapabilityObservation, DatasetType

MAX_CAPABILITY_IMPORT_BYTES = 2_000_000
MAX_CAPABILITY_IMPORT_ROWS = 50_000


def import_capabilities_json(
    payload: str | bytes, metadata: RawDatasetMetadata
) -> RawCapabilityDataset:
    content = payload.encode() if isinstance(payload, str) else payload
    if len(content) > MAX_CAPABILITY_IMPORT_BYTES:
        raise ValueError("capability import exceeds configured byte limit")
    if metadata.dataset_type is not DatasetType.CAPABILITIES:
        raise ValueError("capability metadata must use capabilities dataset type")
    try:
        decoded = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid capability JSON: {exc}") from exc
    if not isinstance(decoded, list) or not all(isinstance(row, dict) for row in decoded):
        raise ValueError("capability JSON must be an array of objects")
    if len(decoded) > MAX_CAPABILITY_IMPORT_ROWS:
        raise ValueError("capability import exceeds configured row limit")
    if any("source_snapshot_id" in row for row in decoded):
        raise ValueError("source_snapshot_id is assigned by import metadata")
    for row in decoded:
        if row.get("source_type") == "curated_import":
            if not row.get("verified_at"):
                raise ValueError("curated capability observation requires verified_at")
            if not row.get("source_revision"):
                raise ValueError("curated capability observation requires source_revision")
    try:
        records = tuple(
            CapabilityObservation.model_validate(
                {
                    **_object(row),
                    "source_snapshot_id": metadata.snapshot_id,
                }
            )
            for row in decoded
        )
        return RawCapabilityDataset(
            snapshot=metadata.snapshot(content), records=records, raw_content=content
        )
    except ValidationError as exc:
        raise ValueError(f"invalid capability observation: {exc}") from exc


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("import record must be an object")
    return value
