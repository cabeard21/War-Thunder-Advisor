"""Bounded, strict operational research-graph imports."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from wt_advisor.data.models import RawDatasetMetadata, RawResearchGraphDataset
from wt_advisor.domain.models import DatasetType, ResearchEdge

MAX_RESEARCH_GRAPH_IMPORT_BYTES = 2_000_000
MAX_RESEARCH_GRAPH_IMPORT_ROWS = 100_000


def import_research_graph_json(
    payload: str | bytes, metadata: RawDatasetMetadata
) -> RawResearchGraphDataset:
    content = payload.encode() if isinstance(payload, str) else payload
    if len(content) > MAX_RESEARCH_GRAPH_IMPORT_BYTES:
        raise ValueError("research graph import exceeds configured byte limit")
    if metadata.dataset_type is not DatasetType.RESEARCH_GRAPH:
        raise ValueError("research graph metadata must use research_graph dataset type")
    try:
        decoded = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid research graph JSON: {exc}") from exc
    if not isinstance(decoded, list) or not all(isinstance(row, dict) for row in decoded):
        raise ValueError("research graph JSON must be an array of objects")
    if len(decoded) > MAX_RESEARCH_GRAPH_IMPORT_ROWS:
        raise ValueError("research graph import exceeds configured row limit")
    try:
        records = tuple(
            ResearchEdge.model_validate(
                {**_object(row), "snapshot_id": metadata.snapshot_id}
            )
            for row in decoded
        )
        return RawResearchGraphDataset(
            snapshot=metadata.snapshot(content), records=records, raw_content=content
        )
    except ValidationError as exc:
        raise ValueError(f"invalid research edge: {exc}") from exc


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("import record must be an object")
    return value
