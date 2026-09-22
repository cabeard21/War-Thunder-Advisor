from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest

from wt_advisor.data.freshness import FreshnessPolicy, evaluate_freshness
from wt_advisor.data.imports.availability import import_availability_yaml
from wt_advisor.data.imports.capabilities import import_capabilities_json
from wt_advisor.data.imports.research_graph import import_research_graph_json
from wt_advisor.data.imports.statistics import (
    MAX_IMPORT_BYTES,
    StatisticsImportError,
    import_statistics_csv,
    import_statistics_json,
    inspect_statistics_csv,
    inspect_statistics_json,
)
from wt_advisor.data.models import RawDatasetMetadata
from wt_advisor.data.providers.fixture import FixtureProvider
from wt_advisor.data.providers.wt_vehicles_api import (
    ProviderPayloadError,
    ResponseTooLargeError,
    WarThunderVehiclesApiProvider,
)
from wt_advisor.domain.models import (
    CapabilityState,
    DatasetType,
    Freshness,
    PrerequisiteSemantics,
    StatisticsScope,
)
from wt_advisor.overrides.loader import MAX_OVERRIDE_BYTES, load_overrides


def metadata(dataset_type: DatasetType = DatasetType.GLOBAL_STATISTICS) -> RawDatasetMetadata:
    return RawDatasetMetadata(
        snapshot_id="test-statistics",
        dataset_type=dataset_type,
        provider="test",
        retrieved_at=datetime(2026, 9, 18, tzinfo=UTC),
        source_revision="fixture-v1",
        sample_start=date(2026, 8, 1) if dataset_type == DatasetType.GLOBAL_STATISTICS else None,
        sample_end=date(2026, 8, 31) if dataset_type == DatasetType.GLOBAL_STATISTICS else None,
    )


def test_fixture_provider_is_provenance_stamped_and_covers_acceptance_vehicles() -> None:
    provider = FixtureProvider()

    vehicles = provider.fetch_vehicles()
    statistics = provider.fetch_vehicle_statistics()

    by_id = {record.vehicle_id: record for record in vehicles.records}
    assert {
        "us_m3_stuart",
        "us_m22",
        "us_m3_gmc",
        "us_m15_cgmc",
        "us_m16_mgmc",
        "us_m3_lee",
        "us_m5a1",
        "us_m4a3_105",
        "us_m10_gmc",
        "us_m4a1",
        "us_m4",
        "us_m24",
    } <= by_id.keys()
    assert all(record.ground_realistic_br <= 40 for record in vehicles.records)
    assert all(record.rank == 1 for record in vehicles.records if record.initially_owned)
    assert by_id["us_m3_lee"].initial_status == "researching"
    assert vehicles.snapshot.provider == "committed_acceptance_fixture"
    assert len(vehicles.snapshot.checksum) == 64
    assert statistics.snapshot.sample_end == date(2026, 8, 31)
    assert any(row.battles is None for row in statistics.records)


def test_json_statistics_import_preserves_missing_values_and_scope() -> None:
    payload = json.dumps(
        [
            {
                "vehicle_id": "us_m3_lee",
                "mode_scope": "ground_realistic_ground_vehicles",
                "sample_start": "2026-08-01",
                "sample_end": "2026-08-31",
                "battles": 100,
                "wins": 55,
                "losses": 45,
                "win_rate": 0.55,
                "kills": None,
                "ground_kills": 120,
                "air_kills": 2,
                "deaths": 80,
            }
        ]
    )

    dataset = import_statistics_json(payload, metadata())

    assert dataset.records[0].mode_scope == StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES
    assert dataset.records[0].kills is None
    assert dataset.snapshot.checksum != "0" * 64


def test_json_statistics_import_rejects_unknown_fields() -> None:
    payload = json.dumps(
        [{"vehicle_id": "us_m3_lee", "mode_scope": "unknown_realistic_scope", "rating": 99}]
    )

    with pytest.raises(StatisticsImportError, match="rating"):
        import_statistics_json(payload, metadata())


def test_statistics_import_rejects_oversized_payload_before_parsing() -> None:
    payload = b"[" + b" " * MAX_IMPORT_BYTES

    with pytest.raises(StatisticsImportError, match="byte limit"):
        import_statistics_json(payload, metadata())


def test_csv_statistics_import_requires_explicit_header_and_parses_blanks() -> None:
    payload = (
        "vehicle_id,mode_scope,sample_start,sample_end,battles,wins,losses,win_rate,"
        "kills,ground_kills,air_kills,deaths\n"
        "us_m22,ground_realistic_ground_vehicles,2026-08-01,2026-08-31,50,27,23,"
        "0.54,,45,1,40\n"
    )

    dataset = import_statistics_csv(payload, metadata())

    assert dataset.records[0].kills is None
    assert dataset.records[0].battles == 50

    with pytest.raises(StatisticsImportError, match="header"):
        import_statistics_csv(
            "vehicle_id,mode_scope,score\nus_m22,unknown_realistic_scope,5\n", metadata()
        )


def test_statistics_inspect_maps_source_ids_and_reports_duplicates_without_mutation() -> None:
    payload = json.dumps(
        [
            {
                "source_vehicle_id": "m24-source",
                "mode_scope": "ground_realistic_ground_vehicles",
                "sample_start": "2026-08-01",
                "sample_end": "2026-08-31",
                "battles": 100,
                "wins": 55,
                "kills": 120,
                "deaths": 80,
                "kd": 1.5,
                "kills_per_battle": 1.2,
            },
            {
                "source_vehicle_id": "m24-source",
                "mode_scope": "ground_realistic_ground_vehicles",
                "sample_start": "2026-08-01",
                "sample_end": "2026-08-31",
                "battles": 100,
            },
        ]
    )

    inspection = inspect_statistics_json(
        payload,
        metadata(),
        identity_aliases={"m24-source": "us_m24"},
        canonical_vehicle_ids={"us_m24"},
    )

    assert inspection.matched_rows == 2
    assert inspection.canonical_match_rate == 1.0
    assert inspection.duplicate_observations
    assert inspection.prospective_snapshot_id.startswith("statistics-")
    assert not inspection.valid
    with pytest.raises(StatisticsImportError, match="duplicate"):
        import_statistics_json(
            payload,
            metadata(),
            identity_aliases={"m24-source": "us_m24"},
            canonical_vehicle_ids={"us_m24"},
        )


def test_statistics_inspect_reports_unknown_columns_and_unresolved_identities() -> None:
    payload = (
        "source_vehicle_id,mode_scope,battles,rating\n"
        "unknown,ground_realistic_ground_vehicles,10,99\n"
    )

    inspection = inspect_statistics_csv(
        payload,
        metadata(),
        identity_aliases={},
        canonical_vehicle_ids={"us_m24"},
    )

    assert inspection.unknown_columns == ("rating",)
    assert inspection.unresolved_source_ids == ("unknown",)
    assert not inspection.valid


def test_statistics_m2_import_maps_identity_and_preserves_reported_ratios() -> None:
    payload = json.dumps(
        [
            {
                "source_vehicle_id": "m24-source",
                "mode_scope": "ground_realistic_ground_vehicles",
                "battles": 100,
                "wins": 55,
                "kills": 120,
                "deaths": 80,
                "win_rate": 0.55,
                "kd": 1.5,
                "kills_per_battle": 1.2,
            }
        ]
    )

    dataset = import_statistics_json(
        payload,
        metadata(),
        identity_aliases={"m24-source": "us_m24"},
        canonical_vehicle_ids={"us_m24"},
    )

    row = dataset.records[0]
    assert row.vehicle_id == "us_m24"
    assert row.reported_kd == 1.5
    assert row.derived_kd == 1.5


@pytest.mark.parametrize(
    "scope", ["air_realistic", "realistic_all_contexts", "unknown_realistic_scope"]
)
def test_statistics_import_rejects_non_ground_rb_scope(scope: str) -> None:
    payload = json.dumps([{"vehicle_id": "us_m24", "mode_scope": scope, "battles": 10}])
    inspection = inspect_statistics_json(payload, metadata())
    assert not inspection.valid
    assert any("mode_scope" in error for error in inspection.errors)
    with pytest.raises(StatisticsImportError, match="mode_scope"):
        import_statistics_json(payload, metadata())


@pytest.mark.parametrize(
    ("metric", "value"),
    [("win_rate", float("nan")), ("kd", float("inf")), ("kills_per_battle", float("-inf"))],
)
def test_statistics_import_rejects_nonfinite_metrics(metric: str, value: float) -> None:
    payload = json.dumps([{
        "vehicle_id": "us_m24", "mode_scope": "ground_realistic_ground_vehicles", metric: value,
    }])
    inspection = inspect_statistics_json(payload, metadata())
    assert not inspection.valid
    assert any(metric in error for error in inspection.errors)
    with pytest.raises(StatisticsImportError, match=metric):
        import_statistics_json(payload, metadata())


@pytest.mark.parametrize(
    ("metric", "denominator"),
    [("win_rate", "battles"), ("kd", "deaths"), ("kills_per_battle", "battles")],
)
def test_statistics_import_rejects_ratio_with_explicit_zero_denominator(
    metric: str, denominator: str
) -> None:
    payload = json.dumps([{
        "vehicle_id": "us_m24", "mode_scope": "ground_realistic_ground_vehicles",
        metric: 0.5, denominator: 0,
    }])
    inspection = inspect_statistics_json(payload, metadata())
    assert not inspection.valid
    assert any("zero" in error for error in inspection.errors)
    with pytest.raises(StatisticsImportError, match="zero"):
        import_statistics_json(payload, metadata())


def test_statistics_import_preserves_missing_denominators_without_deriving_counts() -> None:
    payload = json.dumps([{
        "vehicle_id": "us_m24", "mode_scope": "ground_realistic_ground_vehicles", "kd": 1.2,
    }])
    row = import_statistics_json(payload, metadata()).records[0]
    assert row.battles is None
    assert row.deaths is None
    assert row.reported_kd == 1.2
    assert row.derived_kd is None


def test_capability_import_preserves_explicit_false_and_rejects_missing_value() -> None:
    cap_metadata = metadata(DatasetType.CAPABILITIES)
    payload = json.dumps(
        [
            {
                "vehicle_id": "us_m24",
                "capability": "scouting",
                "value": False,
                "source_provider": "curated",
                "source_reference": "reference",
                "source_type": "official_reference",
            }
        ]
    )

    dataset = import_capabilities_json(payload, cap_metadata)

    assert dataset.resolve("us_m24", "scouting").state is CapabilityState.VERIFIED_ABSENT
    with pytest.raises(ValueError, match="value"):
        import_capabilities_json(payload.replace(', "value": false', ""), cap_metadata)


def test_capability_import_rejects_duplicate_claims() -> None:
    cap_metadata = metadata(DatasetType.CAPABILITIES)
    row = {
        "vehicle_id": "us_m24",
        "capability": "scouting",
        "value": True,
        "source_provider": "curated",
        "source_reference": "reference",
        "source_type": "curated_import",
        "verified_at": "2026-09-21",
        "source_revision": "manual-r1",
    }
    with pytest.raises(ValueError, match="duplicate observations"):
        import_capabilities_json(json.dumps([row, row]), cap_metadata)


def test_capability_import_rejects_overridden_snapshot_identity() -> None:
    row = {
        "vehicle_id": "us_m24",
        "capability": "scouting",
        "value": True,
        "source_provider": "curated",
        "source_snapshot_id": "forged-snapshot",
        "source_reference": "reference",
        "source_type": "curated_import",
    }
    with pytest.raises(ValueError, match="source_snapshot_id"):
        import_capabilities_json(json.dumps([row]), metadata(DatasetType.CAPABILITIES))


def test_curated_capability_import_requires_per_claim_verification() -> None:
    row = {
        "vehicle_id": "us_m24",
        "capability": "scouting",
        "value": True,
        "source_provider": "curated",
        "source_reference": "https://example.invalid/vehicle",
        "source_type": "curated_import",
    }
    with pytest.raises(ValueError, match="verified_at"):
        import_capabilities_json(json.dumps([row]), metadata(DatasetType.CAPABILITIES))
    row["verified_at"] = "2026-09-21"
    with pytest.raises(ValueError, match="source_revision"):
        import_capabilities_json(json.dumps([row]), metadata(DatasetType.CAPABILITIES))
    row["source_revision"] = "wiki-2026-09-21"
    assert import_capabilities_json(
        json.dumps([row]), metadata(DatasetType.CAPABILITIES)
    ).records[0].verified_at == date(2026, 9, 21)


def test_availability_yaml_import_is_strict_and_provenance_stamped() -> None:
    payload = """\
- vehicle_id: us_m24
  acquisition_type: research
  researchability: normally_researchable
  tree_membership: main_tree
  visibility: visible
  source_provider: curated
  source_reference: reference
  reason: visible main-tree vehicle
  provider_observation: research_tree
"""

    dataset = import_availability_yaml(payload, metadata(DatasetType.AVAILABILITY))

    assert dataset.records[0].vehicle_id == "us_m24"
    with pytest.raises(ValueError, match="unknown"):
        import_availability_yaml(payload + "  unknown: true\n", metadata(DatasetType.AVAILABILITY))


def test_research_graph_import_supports_all_and_any_prerequisite_groups() -> None:
    payload = json.dumps(
        [
            {
                "nation": "usa",
                "domain": "ground",
                "parent_vehicle_id": "us_m3_lee",
                "child_vehicle_id": "us_m4a1",
                "edge_type": "required_predecessor",
                "prerequisite_group": "entry",
                "prerequisite_semantics": "all",
            },
            {
                "nation": "usa",
                "domain": "ground",
                "parent_vehicle_id": "us_m4",
                "child_vehicle_id": "us_m24",
                "edge_type": "branch_unlock",
                "prerequisite_group": "branch",
                "prerequisite_semantics": "any",
            },
        ]
    )

    dataset = import_research_graph_json(payload, metadata(DatasetType.RESEARCH_GRAPH))

    assert dataset.records[1].prerequisite_semantics is PrerequisiteSemantics.ANY


def test_override_loader_validates_provenance_and_duplicate_targets(tmp_path) -> None:
    path = tmp_path / "overrides.yaml"
    path.write_text(
        """revision: test-v1
overrides:
  - vehicle_id: us_m4a1
    field: ground_realistic_br
    value: 33
    reason: Fixture correction
    reference: https://wiki.warthunder.com/unit/us_m4a1_sherman
    added_at: 2026-09-18
""",
        encoding="utf-8",
    )

    overrides = load_overrides(path)

    assert overrides.revision == "test-v1"
    assert overrides.entries[0].value == 33

    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "    added_at: 2026-09-18\n",
            """    added_at: 2026-09-18
  - vehicle_id: us_m4a1
    field: ground_realistic_br
    value: 37
    reason: duplicate
    reference: ref
    added_at: 2026-09-18
""",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_overrides(path)


def test_override_loader_rejects_oversized_document(tmp_path) -> None:
    path = tmp_path / "oversized.yaml"
    path.write_bytes(b" " * (MAX_OVERRIDE_BYTES + 1))

    with pytest.raises(ValueError, match="byte limit"):
        load_overrides(path)


@pytest.mark.parametrize(
    ("dataset_type", "observed", "expected"),
    [
        (DatasetType.VEHICLE_METADATA, date(2026, 9, 4), Freshness.FRESH),
        (DatasetType.VEHICLE_METADATA, date(2026, 9, 3), Freshness.AGING),
        (DatasetType.VEHICLE_METADATA, date(2026, 8, 3), Freshness.STALE),
        (DatasetType.GLOBAL_STATISTICS, date(2026, 8, 19), Freshness.FRESH),
        (DatasetType.GLOBAL_STATISTICS, date(2026, 8, 18), Freshness.AGING),
        (DatasetType.GLOBAL_STATISTICS, date(2026, 6, 19), Freshness.STALE),
    ],
)
def test_freshness_policy_boundaries(
    dataset_type: DatasetType, observed: date, expected: Freshness
) -> None:
    assert (
        evaluate_freshness(dataset_type, observed, date(2026, 9, 18), FreshnessPolicy())
        == expected
    )


def test_freshness_is_unknown_without_observation_date() -> None:
    assert (
        evaluate_freshness(
            DatasetType.GLOBAL_STATISTICS, None, date(2026, 9, 18), FreshnessPolicy()
        )
        == Freshness.UNKNOWN
    )


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def set(self, key: str, value: bytes) -> None:
        self.values[key] = value


def test_http_provider_retries_then_uses_cache_without_live_network() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            request=request,
            json=[
                {
                    "identifier": "us_m3_lee",
                    "country": "USA",
                    "vehicle_type": "mediumTank",
                    "era": "II",
                    "realistic_ground_br": 2.7,
                    "is_premium": False,
                }
            ],
        )

    cache = MemoryCache()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = WarThunderVehiclesApiProvider(
        client=client, cache=cache, max_attempts=2, retry_delay_seconds=0
    )

    first = provider.fetch_vehicles()
    second = provider.fetch_vehicles()

    assert attempts == 2
    assert first.records == second.records
    assert first.records[0].vehicle_id == "us_m3_lee"


def test_http_provider_rejects_oversized_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=b"[]" * 100)

    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)), max_response_bytes=10
    )

    with pytest.raises(ResponseTooLargeError):
        provider.fetch_vehicles()


def test_http_provider_uses_lowercase_country_filter_and_imports_every_page() -> None:
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested_pages.append(page)
        assert request.url.params["country"] == "usa"
        identifiers = {0: "us_m2a4", 1: "us_m3_lee"}
        return httpx.Response(
            200,
            request=request,
            json={
                "vehicles": [
                    {
                        "identifier": identifiers[page],
                        "country": "USA",
                        "vehicle_type": "mediumTank",
                        "era": "II",
                        "realistic_ground_br": 2.7,
                        "is_premium": False,
                    }
                ],
                "pagination": {"totalPages": 2},
            },
        )

    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_delay_seconds=0,
    )

    dataset = provider.fetch_vehicles()

    assert requested_pages == [0, 1]
    assert tuple(row.vehicle_id for row in dataset.records) == ("us_m2a4", "us_m3_lee")


def test_http_provider_rejects_an_empty_successful_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"vehicles": [], "pagination": {"totalPages": 1}},
        )

    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_delay_seconds=0,
    )

    with pytest.raises(ProviderPayloadError, match=r"no .*vehicles|empty"):
        provider.fetch_vehicles()


@pytest.mark.parametrize(
    ("source_id", "canonical_id"),
    [
        ("us_m2a4", "us_m2a4"),
        ("us_m3_stuart", "us_m3_stuart"),
        ("us_m3a1_stuart", "us_m3a1_stuart"),
        ("us_m3_lee", "us_m3_lee"),
        ("us_m10", "us_m10_gmc"),
        ("us_halftrack_m13", "us_m13_mgmc"),
        ("us_halftrack_m15", "us_m15_cgmc"),
        ("us_halftrack_m16", "us_m16_mgmc"),
        ("us_m22_locust", "us_m22"),
        ("us_m24_chaffee", "us_m24"),
        ("us_m2a4_1st_armor_div", "us_m2a4_first_tank_div"),
        ("us_halftrack_m3_75mm_gmc", "us_m3_gmc"),
        ("us_m4_sherman", "us_m4"),
        ("us_m4a1_1942_sherman", "us_m4a1"),
        ("us_m4a3_105_sherman", "us_m4a3_105"),
        ("us_m5a1_stuart", "us_m5a1"),
    ],
)
def test_live_provider_maps_source_aliases_to_fixture_canonical_ids(
    source_id: str, canonical_id: str
) -> None:
    fixture = FixtureProvider().fetch_vehicles()
    fixture_record = next(row for row in fixture.records if row.vehicle_id == canonical_id)
    raw = {
        "identifier": source_id,
        "name": fixture_record.name,
        "country": "USA",
        "vehicle_type": fixture_record.vehicle_class.value,
        "era": fixture_record.rank,
        "realistic_ground_br": fixture_record.ground_realistic_br / 10,
        "is_premium": False,
    }

    normalized = WarThunderVehiclesApiProvider._normalize(raw)

    assert normalized.vehicle_id == fixture_record.vehicle_id
    assert normalized.source_vehicle_id == source_id
