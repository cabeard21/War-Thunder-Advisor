"""Bounded WT Data Project reader for identified RB ground-vehicle proxies.

The project's joined files combine ThunderSkill vehicle metrics and Wiki identity
fields with an acknowledged imperfect join. Only explicit source-name aliases map
to canonical IDs. Scoring retains the broader RB scope and reduces its influence.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import math
import re
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from wt_advisor.data.freshness import evaluate_freshness
from wt_advisor.data.models import RawDatasetMetadata, RawStatisticsDataset
from wt_advisor.domain.models import (
    DatasetType,
    KillTargetDefinition,
    StatisticsScope,
    VehicleStatistics,
)

CONTENTS_URL = "https://api.github.com/repos/ControlNet/wt-data-project.data/contents/joined"
RAW_ROOT = "https://raw.githubusercontent.com/ControlNet/wt-data-project.data/master/joined"
PROVIDER = "wt_data_project_thunderskill_wiki_join"
NORMALIZATION_REVISION = "ground-targets-v2"
MAX_INDEX_BYTES = 2_000_000
MAX_CSV_BYTES = 5_000_000
MAX_ROWS = 20_000
DATE_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.csv$")
MISSING_VALUES = frozenset({"", "na", "n/a", "null", "none"})


def _reported_metric(raw: str | None, *, percentage: bool = False) -> float | None:
    if raw is None or raw.strip().lower() in MISSING_VALUES:
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise ValueError("invalid reported metric") from exc
    upper_bound = Decimal(100) if percentage else None
    if not value.is_finite() or value < 0 or (upper_bound is not None and value > upper_bound):
        raise ValueError("invalid reported metric")
    normalized = float(value / 100 if percentage else value)
    if not math.isfinite(normalized):
        raise ValueError("invalid reported metric")
    return normalized


class ByteCache(Protocol):
    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes) -> None: ...


class CommunityStatisticsError(ValueError):
    """A whole-source failure; individual bad observations are quarantined."""


@dataclass(frozen=True)
class CommunityStatisticsResult:
    dataset: RawStatisticsDataset | None
    accepted_count: int
    quarantine_counts: dict[str, int]
    eligible_count: int
    source_url: str
    observation_date: date
    source_revision: str | None
    age_days: int
    reason: str


class CommunityStatisticsProvider:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache: ByteCache | None = None,
        index_url: str = CONTENTS_URL,
        raw_root: str = RAW_ROOT,
        timeout_seconds: float = 10.0,
    ) -> None:
        for url in (index_url, raw_root):
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.fragment):
                raise ValueError("community statistics URLs must be credential-free HTTPS")
            if parsed.hostname.lower() == "localhost":
                raise ValueError("community statistics URLs must be public")
            try:
                address = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    raise ValueError("community statistics URLs must be public")
        self._client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        self._cache = cache
        self._index_url = index_url
        self._raw_root = raw_root.rstrip("/")

    def _download(self, url: str, limit: int) -> tuple[bytes, str | None]:
        key = f"community-statistics:{url}"
        # Directory listings must be refreshed; a dated CSV is stable and reusable.
        immutable_csv = url != self._index_url
        cached = self._cache.get(key) if self._cache and immutable_csv else None
        if cached is not None:
            if len(cached) > limit:
                raise CommunityStatisticsError("cached response exceeds configured byte limit")
            return cached, None
        try:
            with self._client.stream(
                "GET", url, headers={"Accept": "application/json, text/csv"}
            ) as response:
                response.raise_for_status()
                if int(response.headers.get("content-length", "0")) > limit:
                    raise CommunityStatisticsError("community response exceeds byte limit")
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > limit:
                        raise CommunityStatisticsError("community response exceeds byte limit")
                    data.extend(chunk)
                revision = response.headers.get("etag")
        except httpx.HTTPError as exc:
            raise CommunityStatisticsError("community statistics download failed") from exc
        result = bytes(data)
        if self._cache and immutable_csv:
            self._cache.set(key, result)
        return result, revision

    def fetch_statistics(
        self,
        *,
        identity_aliases: Mapping[str, str],
        canonical_vehicle_ids: Collection[str],
        compatibility_key: str | None = None,
        now: datetime | None = None,
        retained_content: bytes | None = None,
        observation_date: date | None = None,
        source_revision: str | None = None,
    ) -> CommunityStatisticsResult:
        retrieved_at = now or datetime.now(UTC)
        if retained_content is not None:
            if observation_date is None:
                raise CommunityStatisticsError("retained CSV requires its observation date")
            if len(retained_content) > MAX_CSV_BYTES:
                raise CommunityStatisticsError("retained CSV exceeds byte limit")
            observed = observation_date
            content = retained_content
            revision = source_revision
        else:
            listing_bytes, index_revision = self._download(self._index_url, MAX_INDEX_BYTES)
            try:
                listing = json.loads(listing_bytes)
            except (ValueError, UnicodeDecodeError) as exc:
                raise CommunityStatisticsError("invalid community directory listing") from exc
            if not isinstance(listing, list) or len(listing) > MAX_ROWS:
                raise CommunityStatisticsError(
                    "community directory listing is invalid or oversized"
                )
            dates = []
            for item in listing:
                if not isinstance(item, dict) or item.get("type") != "file":
                    continue
                name = item.get("name")
                matched = DATE_FILE.fullmatch(name) if isinstance(name, str) else None
                if matched:
                    try:
                        observed = date.fromisoformat(matched.group(1))
                    except ValueError:
                        continue
                    if isinstance(item.get("size"), int) and item["size"] > MAX_CSV_BYTES:
                        continue
                    dates.append(observed)
            if not dates:
                raise CommunityStatisticsError("no bounded, dated joined CSV was listed")
            observed = max(dates)
            content, content_revision = self._download(
                f"{self._raw_root}/{observed.isoformat()}.csv", MAX_CSV_BYTES,
            )
            revision = content_revision or index_revision
        source_url = f"{self._raw_root}/{observed.isoformat()}.csv"
        try:
            reader = csv.DictReader(StringIO(content.decode("utf-8-sig")))
            fields = reader.fieldnames or []
            if not {"name", "nation", "cls", "rb_battles"}.issubset(fields):
                raise CommunityStatisticsError("joined CSV lacks identity/scope/denominator fields")
            rows = list(reader)
        except UnicodeDecodeError as exc:
            raise CommunityStatisticsError("joined CSV is not UTF-8") from exc
        if len(rows) > MAX_ROWS:
            raise CommunityStatisticsError("joined CSV exceeds row limit")
        valid_ids = set(canonical_vehicle_ids)
        mapping_input = json.dumps(
            {
                "aliases": sorted(identity_aliases.items()),
                "canonical_ids": sorted(valid_ids),
                "compatibility_key": compatibility_key,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        normalization_revision = (
            f"{NORMALIZATION_REVISION}-{sha256(mapping_input).hexdigest()[:12]}"
        )
        snapshot_id = (f"community-statistics-{observed:%Y%m%d}-"
                       f"{sha256(content).hexdigest()[:12]}-{normalization_revision}")
        quarantined: Counter[str] = Counter()
        accepted: dict[str, VehicleStatistics] = {}
        duplicates: set[str] = set()
        for row in rows:
            if None in row or row.get("nation") != "USA" or row.get("cls") != "Ground_vehicles":
                quarantined["wrong_scope"] += 1
                continue
            name = row.get("name")
            vehicle_id = identity_aliases.get(name or "")
            if vehicle_id is None or vehicle_id not in valid_ids:
                quarantined["unresolved_identity"] += 1
                continue
            if vehicle_id in duplicates:
                quarantined["duplicate_identity"] += 1
                continue
            try:
                raw_battles = row.get("rb_battles")
                decimal_battles = Decimal(raw_battles or "")
                if (not decimal_battles.is_finite()
                        or decimal_battles <= 0
                        or decimal_battles != decimal_battles.to_integral_value()):
                    raise ValueError("invalid battle count")
                battles = int(decimal_battles)
                metrics: dict[str, float | None] = {}
                for field_name, percentage in (
                    ("rb_win_rate", True),
                    ("rb_ground_frags_per_battle", False),
                    ("rb_ground_frags_per_death", False),
                ):
                    try:
                        metrics[field_name] = _reported_metric(
                            row.get(field_name), percentage=percentage,
                        )
                    except ValueError:
                        metrics[field_name] = None
                        quarantined[f"invalid_{field_name}"] += 1
                statistic = VehicleStatistics(
                    vehicle_id=vehicle_id,
                    snapshot_id=snapshot_id,
                    mode_scope=StatisticsScope.REALISTIC_ALL_CONTEXTS,
                    sample_end=observed,
                    battles=battles,
                    reported_win_rate=metrics["rb_win_rate"],
                    reported_kills_per_battle=metrics["rb_ground_frags_per_battle"],
                    reported_kd=metrics["rb_ground_frags_per_death"],
                    kill_target_definition=KillTargetDefinition.GROUND_TARGETS,
                )
            except (ValueError, TypeError, InvalidOperation):
                quarantined["invalid_metric"] += 1
                continue
            if vehicle_id in accepted:
                del accepted[vehicle_id]
                duplicates.add(vehicle_id)
                quarantined["duplicate_identity"] += 2
            else:
                accepted[vehicle_id] = statistic
        metadata = RawDatasetMetadata(
            snapshot_id=snapshot_id,
            dataset_type=DatasetType.GLOBAL_STATISTICS,
            provider=PROVIDER,
            retrieved_at=retrieved_at,
            source_revision=revision,
            sample_end=observed,
            compatibility_key=compatibility_key,
        )
        dataset = (RawStatisticsDataset(snapshot=metadata.snapshot(
            content, evaluate_freshness(DatasetType.GLOBAL_STATISTICS, observed,
                                        retrieved_at.date())),
                                       records=tuple(accepted.values()), raw_content=content,
                                       normalization_revision=normalization_revision)
                   if accepted else None)
        return CommunityStatisticsResult(
            dataset=dataset,
            accepted_count=len(accepted),
            quarantine_counts=dict(quarantined),
            eligible_count=sum(
                1 for item in accepted.values()
                if 0 <= (retrieved_at.date() - observed).days <= 90
                and (item.reported_win_rate is not None
                     or item.reported_kills_per_battle is not None
                     or item.reported_kd is not None)
            ),
            source_url=source_url,
            observation_date=observed,
            source_revision=revision,
            age_days=(retrieved_at.date() - observed).days,
            reason=(
                "ThunderSkill RB ground-vehicle proxy; rb_win_rate, "
                "rb_ground_frags_per_battle and rb_ground_frags_per_death are reported "
                "source fields; peer eligibility and reduced influence are applied "
                "during scoring, with actual RB scope retained"
            ),
        )
