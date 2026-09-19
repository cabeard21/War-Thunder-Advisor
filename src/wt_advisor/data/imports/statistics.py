"""Strict JSON and CSV imports for exported vehicle statistics."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Any

from pydantic import ValidationError

from wt_advisor.data.models import RawDatasetMetadata, RawStatisticsDataset
from wt_advisor.domain.models import DatasetType, VehicleStatistics

STATISTICS_COLUMNS = (
    "vehicle_id",
    "mode_scope",
    "sample_start",
    "sample_end",
    "battles",
    "wins",
    "losses",
    "win_rate",
    "kills",
    "ground_kills",
    "air_kills",
    "deaths",
)
INTEGER_COLUMNS = frozenset(
    {"battles", "wins", "losses", "kills", "ground_kills", "air_kills", "deaths"}
)
MAX_IMPORT_BYTES = 5_000_000
MAX_IMPORT_ROWS = 100_000


class StatisticsImportError(ValueError):
    """Raised when an imported statistics export is ambiguous or invalid."""


def _validate_metadata(metadata: RawDatasetMetadata) -> None:
    if metadata.dataset_type != DatasetType.GLOBAL_STATISTICS:
        raise StatisticsImportError("statistics metadata must use global_statistics dataset type")


def _build(
    records: list[dict[str, Any]], metadata: RawDatasetMetadata, content: bytes
) -> RawStatisticsDataset:
    _validate_metadata(metadata)
    try:
        rows = tuple(
            VehicleStatistics(snapshot_id=metadata.snapshot_id, **record) for record in records
        )
    except ValidationError as exc:
        raise StatisticsImportError(str(exc)) from exc
    return RawStatisticsDataset(
        snapshot=metadata.snapshot(content), records=rows, raw_content=content
    )


def import_statistics_json(
    payload: str | bytes, metadata: RawDatasetMetadata
) -> RawStatisticsDataset:
    content = payload.encode() if isinstance(payload, str) else payload
    if len(content) > MAX_IMPORT_BYTES:
        raise StatisticsImportError("statistics import exceeds configured byte limit")
    try:
        decoded = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StatisticsImportError(f"invalid statistics JSON: {exc}") from exc
    if not isinstance(decoded, list) or not all(isinstance(row, dict) for row in decoded):
        raise StatisticsImportError("statistics JSON must be an array of objects")
    if len(decoded) > MAX_IMPORT_ROWS:
        raise StatisticsImportError("statistics import exceeds configured row limit")
    allowed = frozenset(STATISTICS_COLUMNS)
    for index, row in enumerate(decoded):
        unknown = set(row) - allowed
        if unknown:
            raise StatisticsImportError(
                f"record {index} contains unknown fields: {sorted(unknown)}"
            )
    return _build(decoded, metadata, content)


def import_statistics_csv(
    payload: str | bytes, metadata: RawDatasetMetadata
) -> RawStatisticsDataset:
    content = payload.encode() if isinstance(payload, str) else payload
    if len(content) > MAX_IMPORT_BYTES:
        raise StatisticsImportError("statistics import exceeds configured byte limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatisticsImportError("statistics CSV must be UTF-8") from exc
    reader = csv.DictReader(StringIO(text))
    if tuple(reader.fieldnames or ()) != STATISTICS_COLUMNS:
        raise StatisticsImportError(
            "statistics CSV header must exactly match: " + ",".join(STATISTICS_COLUMNS)
        )
    records: list[dict[str, Any]] = []
    for row_number, raw in enumerate(reader, start=2):
        if len(records) >= MAX_IMPORT_ROWS:
            raise StatisticsImportError("statistics import exceeds configured row limit")
        if None in raw:
            raise StatisticsImportError(f"row {row_number} has more values than the header")
        row: dict[str, Any] = {}
        try:
            for key in STATISTICS_COLUMNS:
                value = raw[key].strip()
                if value == "":
                    row[key] = None
                elif key in INTEGER_COLUMNS:
                    row[key] = int(value)
                elif key == "win_rate":
                    row[key] = float(value)
                else:
                    row[key] = value
        except (AttributeError, ValueError) as exc:
            raise StatisticsImportError(f"invalid value on CSV row {row_number}: {exc}") from exc
        records.append(row)
    return _build(records, metadata, content)
