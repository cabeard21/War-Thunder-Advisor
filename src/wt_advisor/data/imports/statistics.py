"""Strict, inspectable JSON and CSV imports for exported vehicle statistics."""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Collection, Mapping
from datetime import date
from hashlib import sha256
from io import StringIO
from typing import Any

from pydantic import ValidationError

from wt_advisor.data.models import RawDatasetMetadata, RawStatisticsDataset
from wt_advisor.domain.models import (
    DatasetType,
    StatisticsInspection,
    StatisticsScope,
    VehicleStatistics,
)

STATISTICS_COLUMNS = (
    "vehicle_id", "mode_scope", "sample_start", "sample_end", "battles", "wins",
    "losses", "win_rate", "kills", "ground_kills", "air_kills", "deaths",
)
M2_STATISTICS_COLUMNS = (
    "vehicle_id", "source_vehicle_id", "mode_scope", "sample_start", "sample_end",
    "battles", "wins", "losses", "win_rate", "kills", "ground_kills",
    "air_kills", "deaths", "kd", "kills_per_battle",
)
INTEGER_COLUMNS = frozenset(
    {"battles", "wins", "losses", "kills", "ground_kills", "air_kills", "deaths"}
)
RATIO_COLUMNS = frozenset({"win_rate", "kd", "kills_per_battle"})
METRIC_COLUMNS = INTEGER_COLUMNS | RATIO_COLUMNS
MAX_IMPORT_BYTES = 5_000_000
MAX_IMPORT_ROWS = 100_000


class StatisticsImportError(ValueError):
    """Raised when an imported statistics export is ambiguous or invalid."""


def _validate_metadata(metadata: RawDatasetMetadata) -> None:
    if metadata.dataset_type != DatasetType.GLOBAL_STATISTICS:
        raise StatisticsImportError("statistics metadata must use global_statistics dataset type")


def _content(payload: str | bytes) -> bytes:
    content = payload.encode() if isinstance(payload, str) else payload
    if len(content) > MAX_IMPORT_BYTES:
        raise StatisticsImportError("statistics import exceeds configured byte limit")
    return content


def _decode_json(content: bytes) -> list[dict[str, Any]]:
    try:
        decoded = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StatisticsImportError(f"invalid statistics JSON: {exc}") from exc
    if not isinstance(decoded, list) or not all(isinstance(row, dict) for row in decoded):
        raise StatisticsImportError("statistics JSON must be an array of objects")
    if len(decoded) > MAX_IMPORT_ROWS:
        raise StatisticsImportError("statistics import exceeds configured row limit")
    return decoded


def _decode_csv(content: bytes) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatisticsImportError("statistics CSV must be UTF-8") from exc
    reader = csv.DictReader(StringIO(text))
    columns = tuple(reader.fieldnames or ())
    if not columns or len(columns) != len(set(columns)):
        raise StatisticsImportError("statistics CSV requires a unique, explicit header")
    records: list[dict[str, Any]] = []
    for row_number, raw in enumerate(reader, start=2):
        if len(records) >= MAX_IMPORT_ROWS:
            raise StatisticsImportError("statistics import exceeds configured row limit")
        if None in raw:
            raise StatisticsImportError(f"row {row_number} has more values than the header")
        row: dict[str, Any] = {}
        try:
            for key in columns:
                raw_value = raw[key]
                if raw_value is None:
                    raise ValueError(f"missing value for {key}")
                value = raw_value.strip()
                if value == "":
                    row[key] = None
                elif key in INTEGER_COLUMNS:
                    row[key] = int(value)
                elif key in RATIO_COLUMNS:
                    row[key] = float(value)
                else:
                    row[key] = value
        except (AttributeError, ValueError) as exc:
            raise StatisticsImportError(f"invalid value on CSV row {row_number}: {exc}") from exc
        records.append(row)
    return records, columns


def _canonical_id(
    row: Mapping[str, Any],
    aliases: Mapping[str, str],
    canonical_ids: Collection[str] | None,
) -> tuple[str | None, str | None]:
    vehicle_id = row.get("vehicle_id")
    source_id = row.get("source_vehicle_id")
    if vehicle_id and source_id:
        return None, "<both vehicle_id and source_vehicle_id>"
    if vehicle_id:
        value = str(vehicle_id)
        if canonical_ids is not None and value not in canonical_ids:
            return None, value
        return value, None
    if source_id:
        source = str(source_id)
        mapped = aliases.get(source)
        if mapped is None or (canonical_ids is not None and mapped not in canonical_ids):
            return None, source
        return mapped, None
    return None, "<missing identity>"


def _inspection(
    records: list[dict[str, Any]],
    columns: Collection[str],
    content: bytes,
    metadata: RawDatasetMetadata,
    aliases: Mapping[str, str],
    canonical_ids: Collection[str] | None,
) -> StatisticsInspection:
    _validate_metadata(metadata)
    allowed = frozenset(M2_STATISTICS_COLUMNS)
    recognized = tuple(column for column in M2_STATISTICS_COLUMNS if column in columns)
    unknown = tuple(sorted(set(columns) - allowed))
    errors: list[str] = []
    if unknown:
        errors.append(f"unknown fields: {list(unknown)}")
    if "mode_scope" not in columns:
        errors.append("required field mode_scope is missing")
    if "vehicle_id" not in columns and "source_vehicle_id" not in columns:
        errors.append("required identity field vehicle_id or source_vehicle_id is missing")

    matched = 0
    unresolved: set[str] = set()
    identities: list[str | None] = []
    for row in records:
        canonical, identity_error = _canonical_id(row, aliases, canonical_ids)
        identities.append(canonical)
        if canonical is None:
            if identity_error:
                unresolved.add(identity_error)
        else:
            matched += 1

    duplicate_labels: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row, canonical in zip(records, identities, strict=True):
        if canonical is None:
            continue
        key = (
            canonical,
            str(row.get("mode_scope") or ""),
            str(row.get("sample_start") or metadata.sample_start or ""),
            str(row.get("sample_end") or metadata.sample_end or ""),
        )
        if key in seen:
            duplicate_labels.append("|".join(key))
        seen.add(key)
    if duplicate_labels:
        errors.append("duplicate canonical/scope/period observations")

    scopes: set[StatisticsScope] = set()
    starts: list[date] = []
    ends: list[date] = []
    for index, row in enumerate(records):
        try:
            if row.get("mode_scope") is not None:
                scope = StatisticsScope(str(row["mode_scope"]))
                scopes.add(scope)
                if scope is not StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES:
                    errors.append(f"record {index} mode_scope is not Ground RB ground vehicles")
            else:
                errors.append(f"record {index} mode_scope is missing")
            if row.get("sample_start") is not None:
                starts.append(date.fromisoformat(str(row["sample_start"])))
            if row.get("sample_end") is not None:
                ends.append(date.fromisoformat(str(row["sample_end"])))
        except ValueError as exc:
            errors.append(f"record {index} contains invalid scope or date: {exc}")
        for metric in METRIC_COLUMNS:
            value = row.get(metric)
            if isinstance(value, bool) or (
                value is not None
                and (not isinstance(value, (int, float)) or not math.isfinite(value))
            ):
                errors.append(f"record {index} {metric} must be a finite number")
        for ratio, denominator in (
            ("win_rate", "battles"),
            ("kd", "deaths"),
            ("kills_per_battle", "battles"),
        ):
            if row.get(ratio) is not None and row.get(denominator) == 0:
                errors.append(f"record {index} {ratio} has explicit zero {denominator} denominator")

    total = len(records)
    coverage = {
        metric: sum(row.get(metric) is not None for row in records) / total if total else 0.0
        for metric in sorted(METRIC_COLUMNS)
        if metric in columns
    }
    unresolved_ids = tuple(sorted(unresolved))
    if unresolved_ids:
        errors.append("unresolved vehicle identities")
    return StatisticsInspection(
        valid=not errors,
        recognized_columns=recognized,
        unknown_columns=unknown,
        total_rows=total,
        matched_rows=matched,
        canonical_match_rate=matched / total if total else 0.0,
        unresolved_source_ids=unresolved_ids,
        duplicate_observations=tuple(sorted(set(duplicate_labels))),
        scopes=tuple(sorted(scopes, key=str)),
        sample_start=min(starts) if starts else metadata.sample_start,
        sample_end=max(ends) if ends else metadata.sample_end,
        metric_coverage=coverage,
        prospective_snapshot_id=f"statistics-{sha256(content).hexdigest()[:16]}",
        errors=tuple(errors),
    )


def inspect_statistics_json(
    payload: str | bytes,
    metadata: RawDatasetMetadata,
    *,
    identity_aliases: Mapping[str, str] | None = None,
    canonical_vehicle_ids: Collection[str] | None = None,
) -> StatisticsInspection:
    content = _content(payload)
    records = _decode_json(content)
    return _inspection(
        records,
        {key for row in records for key in row},
        content,
        metadata,
        identity_aliases or {},
        canonical_vehicle_ids,
    )


def inspect_statistics_csv(
    payload: str | bytes,
    metadata: RawDatasetMetadata,
    *,
    identity_aliases: Mapping[str, str] | None = None,
    canonical_vehicle_ids: Collection[str] | None = None,
) -> StatisticsInspection:
    content = _content(payload)
    records, columns = _decode_csv(content)
    return _inspection(
        records,
        columns,
        content,
        metadata,
        identity_aliases or {},
        canonical_vehicle_ids,
    )


def _build(
    records: list[dict[str, Any]],
    metadata: RawDatasetMetadata,
    content: bytes,
    inspection: StatisticsInspection,
    aliases: Mapping[str, str],
    canonical_ids: Collection[str] | None,
) -> RawStatisticsDataset:
    if not inspection.valid:
        raise StatisticsImportError("; ".join(inspection.errors))
    normalized: list[VehicleStatistics] = []
    try:
        for record in records:
            canonical, _ = _canonical_id(record, aliases, canonical_ids)
            if canonical is None:
                raise StatisticsImportError("unresolved vehicle identity")
            values = {
                key: value
                for key, value in record.items()
                if key not in {"source_vehicle_id", "kd", "kills_per_battle"}
            }
            values["vehicle_id"] = canonical
            if "kd" in record:
                values["reported_kd"] = record["kd"]
            if "kills_per_battle" in record:
                values["reported_kills_per_battle"] = record["kills_per_battle"]
            if "source_vehicle_id" in record and "win_rate" in record:
                values["reported_win_rate"] = record["win_rate"]
            normalized.append(VehicleStatistics(snapshot_id=metadata.snapshot_id, **values))
    except ValidationError as exc:
        raise StatisticsImportError(str(exc)) from exc
    return RawStatisticsDataset(
        snapshot=metadata.snapshot(content),
        records=tuple(normalized),
        raw_content=content,
    )


def import_statistics_json(
    payload: str | bytes,
    metadata: RawDatasetMetadata,
    *,
    identity_aliases: Mapping[str, str] | None = None,
    canonical_vehicle_ids: Collection[str] | None = None,
) -> RawStatisticsDataset:
    content = _content(payload)
    records = _decode_json(content)
    aliases = identity_aliases or {}
    inspection = _inspection(
        records,
        {key for row in records for key in row},
        content,
        metadata,
        aliases,
        canonical_vehicle_ids,
    )
    return _build(records, metadata, content, inspection, aliases, canonical_vehicle_ids)


def import_statistics_csv(
    payload: str | bytes,
    metadata: RawDatasetMetadata,
    *,
    identity_aliases: Mapping[str, str] | None = None,
    canonical_vehicle_ids: Collection[str] | None = None,
) -> RawStatisticsDataset:
    content = _content(payload)
    records, columns = _decode_csv(content)
    aliases = identity_aliases or {}
    inspection = _inspection(records, columns, content, metadata, aliases, canonical_vehicle_ids)
    if tuple(columns) != STATISTICS_COLUMNS and set(columns) - set(M2_STATISTICS_COLUMNS):
        unknown = sorted(set(columns) - set(M2_STATISTICS_COLUMNS))
        raise StatisticsImportError(
            "statistics CSV header contains unknown fields: " + ",".join(unknown)
        )
    return _build(records, metadata, content, inspection, aliases, canonical_vehicle_ids)
