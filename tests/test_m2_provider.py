from __future__ import annotations

import json

import httpx
import pytest

from wt_advisor.data.providers.wt_vehicles_api import (
    ProviderPayloadError,
    ResponseTooLargeError,
    WarThunderVehiclesApiProvider,
)
from wt_advisor.domain.models import AvailabilityType, Capability, CapabilityState, DatasetType
from wt_advisor.storage import EvidenceRepository, create_database


def _detail(identifier: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "identifier": identifier,
        "version": "2.59.0.12",
        "required_vehicle": None,
        "modifications": [],
        "gun_stabilizer": {"has_vertical": False},
        "has_ess": False,
        "weapons": [],
    }
    return {**payload, **overrides}


def test_detail_normalization_records_only_explicit_capability_evidence() -> None:
    detail = _detail(
        "us_m24_chaffee",
        required_vehicle="us_m5a1_stuart",
        modifications=[
            {"name": "improved_optics", "icon": "modifications/scouting.png"},
            {"name": "art_support"},
            {"name": "tank_smoke_screen_system_mod"},
        ],
        gun_stabilizer={"has_vertical": True},
    )

    observations = WarThunderVehiclesApiProvider.normalize_capabilities(detail, "snapshot")
    by_capability = {row.capability: row for row in observations}

    assert by_capability[Capability.SCOUTING].value is True
    assert by_capability[Capability.ARTILLERY].value is True
    assert by_capability[Capability.SMOKE].value is True
    assert by_capability[Capability.VERTICAL_STABILIZER].value is True
    assert Capability.HIGH_CALIBER_HE not in by_capability

    absent = WarThunderVehiclesApiProvider.normalize_capabilities(
        _detail("us_m3_lee"), "snapshot"
    )
    absent_by_capability = {row.capability: row for row in absent}
    assert absent_by_capability[Capability.VERTICAL_STABILIZER].value is False
    assert Capability.SMOKE not in absent_by_capability
    assert Capability.SCOUTING not in absent_by_capability


def test_operational_components_are_independently_versioned_and_bounded() -> None:
    list_payload = {
        "vehicles": [
            {
                "identifier": "us_m24_chaffee",
                "name": "M24",
                "country": "usa",
                "vehicle_class": "light_tank",
                "rank": 2,
                "realistic_ground_br": 3.7,
            },
            {
                "identifier": "us_m4_sherman",
                "name": "M4",
                "country": "usa",
                "vehicle_class": "medium_tank",
                "rank": 2,
                "realistic_ground_br": 4.0,
            },
            {
                "identifier": "us_m5a1_stuart",
                "name": "M5A1",
                "country": "usa",
                "vehicle_class": "light_tank",
                "rank": 2,
                "realistic_ground_br": 2.7,
            },
            {
                "identifier": "us_m3_lee",
                "name": "M3 Lee",
                "country": "usa",
                "vehicle_class": "medium_tank",
                "rank": 2,
                "realistic_ground_br": 2.7,
            },
            {
                "identifier": "us_m46_patton",
                "name": "M46",
                "country": "usa",
                "vehicle_class": "medium_tank",
                "rank": 5,
                "realistic_ground_br": 7.0,
            },
        ],
        "totalPages": 1,
    }
    details = {
        "us_m24_chaffee": _detail(
            "us_m24_chaffee",
            required_vehicle="us_m5a1_stuart",
            modifications=[{"icon": "modifications/scouting.png"}],
            gun_stabilizer={"has_vertical": True},
        ),
        "us_m4_sherman": _detail("us_m4_sherman", required_vehicle="us_m3_lee"),
        "us_m5a1_stuart": _detail("us_m5a1_stuart"),
        "us_m3_lee": _detail("us_m3_lee"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        source_id = request.url.path.rsplit("/", 1)[-1]
        payload = details.get(source_id, list_payload)
        return httpx.Response(
            200,
            content=json.dumps(payload).encode(),
            headers={"etag": "upstream-revision"},
            request=request,
        )

    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://example.com/api/vehicles",
        retry_delay_seconds=0,
    )
    bundle = provider.fetch_operational_components(max_br=40, max_details=10)

    assert bundle.capabilities.snapshot.dataset_type is DatasetType.CAPABILITIES
    assert bundle.availability.snapshot.dataset_type is DatasetType.AVAILABILITY
    assert bundle.research_graph.snapshot.dataset_type is DatasetType.RESEARCH_GRAPH
    assert bundle.capabilities.snapshot.snapshot_id != bundle.research_graph.snapshot.snapshot_id
    assert (
        bundle.vehicles.snapshot.compatibility_key
        == bundle.capabilities.snapshot.compatibility_key
    )
    assert (
        bundle.vehicles.snapshot.compatibility_key
        == bundle.research_graph.snapshot.compatibility_key
    )
    assert (
        bundle.vehicles.snapshot.compatibility_key
        == bundle.availability.snapshot.compatibility_key
    )
    assert {row.vehicle_id for row in bundle.availability.records} == {
        "us_m24",
        "us_m4",
        "us_m5a1",
        "us_m3_lee",
        "us_m46_patton",
    }
    assert {row.vehicle_id for row in bundle.capabilities.records} == {
        "us_m24",
        "us_m4",
        "us_m5a1",
        "us_m3_lee",
    }
    assert {
        (row.parent_vehicle_id, row.child_vehicle_id)
        for row in bundle.research_graph.records
    } == {("us_m3_lee", "us_m4"), ("us_m5a1", "us_m24")}
    assert bundle.capabilities.resolve(
        "us_m24", Capability.SCOUTING
    ).state is CapabilityState.PRESENT


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_attempts": 0}, "max_attempts"),
        ({"max_response_bytes": 0}, "max_response_bytes"),
        ({"page_size": 0}, "page_size"),
        ({"max_pages": 0}, "max_pages"),
        ({"base_url": "http://example.com"}, "base_url"),
        ({"base_url": "https://localhost/api"}, "localhost"),
        ({"base_url": "https://127.0.0.1/api"}, "public address"),
    ],
)
def test_provider_rejects_unsafe_or_unbounded_configuration(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        WarThunderVehiclesApiProvider(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [b"not-json", b"{}", b"42", b'{"vehicles":[1]}'],
)
def test_page_decoder_rejects_ambiguous_payloads(payload: bytes) -> None:
    with pytest.raises(ProviderPayloadError):
        WarThunderVehiclesApiProvider._decode_page(payload)


def test_page_decoder_accepts_array_and_nested_pagination() -> None:
    assert WarThunderVehiclesApiProvider._decode_page(b"[]") == ([], None)
    assert WarThunderVehiclesApiProvider._decode_page(
        b'{"data":[],"pagination":{"total_pages":2}}'
    ) == ([], 2)


def test_normalizers_reject_invalid_identity_br_rank_and_metadata() -> None:
    with pytest.raises(ProviderPayloadError, match="identifier"):
        WarThunderVehiclesApiProvider._vehicle_id({})
    with pytest.raises(ProviderPayloadError, match="normalized"):
        WarThunderVehiclesApiProvider._vehicle_id({"identifier": "!!!"})
    with pytest.raises(ProviderPayloadError, match="invalid realistic BR"):
        WarThunderVehiclesApiProvider._br_tenths("bad")
    with pytest.raises(ProviderPayloadError, match="one decimal"):
        WarThunderVehiclesApiProvider._br_tenths("2.25")
    assert WarThunderVehiclesApiProvider._rank("IV") == 4
    assert WarThunderVehiclesApiProvider._rank("4") == 4
    with pytest.raises(ProviderPayloadError, match="rank"):
        WarThunderVehiclesApiProvider._rank("bad")

    base = {
        "identifier": "us_test",
        "country": "usa",
        "vehicle_class": "light_tank",
        "rank": 1,
        "realistic_ground_br": 1.0,
    }
    with pytest.raises(ProviderPayloadError, match="nation"):
        WarThunderVehiclesApiProvider._normalize({**base, "country": "germany"})
    with pytest.raises(ProviderPayloadError, match="class"):
        WarThunderVehiclesApiProvider._normalize({**base, "vehicle_class": "boat"})
    without_br = {key: value for key, value in base.items() if key != "realistic_ground_br"}
    with pytest.raises(ProviderPayloadError, match="BR"):
        WarThunderVehiclesApiProvider._normalize(without_br)


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("is_pack", AvailabilityType.PACK),
        ("squadron_vehicle", AvailabilityType.SQUADRON),
        ("event", AvailabilityType.EVENT),
        ("premium", AvailabilityType.PREMIUM),
    ],
)
def test_vehicle_normalization_preserves_non_tree_availability(
    flag: str, expected: AvailabilityType
) -> None:
    normalized = WarThunderVehiclesApiProvider._normalize(
        {
            "identifier": "us_test",
            "country": "usa",
            "vehicle_class": "light_tank",
            "rank": 1,
            "realistic_ground_br": 1.0,
            flag: True,
        }
    )
    assert normalized.availability_type is expected


def test_capability_and_graph_normalizers_keep_unknowns_unknown() -> None:
    observations = WarThunderVehiclesApiProvider.normalize_capabilities(
        _detail(
            "us_test",
            modifications=[None, {"name": "unrelated"}],
            has_ess=True,
            weapons=[{"ammo": "smoke grenade"}],
        ),
        "snapshot",
    )
    assert {row.capability for row in observations} == {
        Capability.SMOKE,
        Capability.VERTICAL_STABILIZER,
    }
    assert (
        WarThunderVehiclesApiProvider.normalize_research_edge(
            _detail("us_test"), "snapshot", known_vehicle_ids=frozenset({"us_test"})
        )
        is None
    )
    assert (
        WarThunderVehiclesApiProvider.normalize_research_edge(
            _detail("us_test", required_vehicle="us_parent"),
            "snapshot",
            known_vehicle_ids=frozenset({"us_test"}),
        )
        is None
    )


def test_detail_request_and_component_limits_fail_closed() -> None:
    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        base_url="https://example.com/api/vehicles",
        max_attempts=1,
        retry_delay_seconds=0,
    )
    with pytest.raises(ProviderPayloadError, match="unsafe"):
        provider._request_detail("../unsafe")
    with pytest.raises(ValueError, match="max_br"):
        provider.fetch_operational_components(max_br=9)
    with pytest.raises(ValueError, match="max_details"):
        provider.fetch_operational_components(max_details=0)


def test_detail_content_length_and_json_are_bounded() -> None:
    responses = iter(
        [
            httpx.Response(200, content=b"[]", headers={"content-length": "bad"}),
            httpx.Response(200, content=b"12345", headers={"content-length": "5"}),
        ]
    )
    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: next(responses))
        ),
        base_url="https://example.com/api/vehicles",
        max_attempts=1,
        max_response_bytes=4,
        retry_delay_seconds=0,
    )
    with pytest.raises(ProviderPayloadError, match="Content-Length"):
        provider._request_detail("us_test")
    with pytest.raises(ResponseTooLargeError):
        provider._request_detail("us_test")


class _Cache:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def set(self, key: str, value: bytes) -> None:
        self.values[key] = value


@pytest.mark.parametrize(
    "cached",
    [b"not-json", b"{}"],
)
def test_vehicle_list_cache_rejects_invalid_shapes(cached: bytes) -> None:
    base_url = "https://example.com/api/vehicles"
    provider = WarThunderVehiclesApiProvider(
        cache=_Cache({f"wt-vehicles-api:{base_url}": cached}),
        base_url=base_url,
    )
    with pytest.raises(ProviderPayloadError, match="cached vehicle API payload"):
        provider.fetch_vehicles()


def test_vehicle_list_cache_and_detail_normalization_cover_safe_fallbacks() -> None:
    base_url = "https://example.com/api/vehicles"
    cache = _Cache({f"wt-vehicles-api:{base_url}": b"[]"})
    provider = WarThunderVehiclesApiProvider(cache=cache, base_url=base_url, max_response_bytes=1)
    with pytest.raises(ResponseTooLargeError, match="cached vehicle API payload"):
        provider.fetch_vehicles()

    public_ip_provider = WarThunderVehiclesApiProvider(base_url="https://8.8.8.8/api/vehicles")
    assert public_ip_provider._base_url.startswith("https://8.8.8.8")
    assert WarThunderVehiclesApiProvider.normalize_capabilities(
        {"identifier": "us_test", "gun_stabilizer": {}, "modifications": {}, "weapons": {}},
        "snapshot",
    ) == ()


def test_component_fetch_keeps_full_list_but_prioritizes_bounded_details() -> None:
    base_url = "https://example.com/api/vehicles"
    empty = WarThunderVehiclesApiProvider(
        cache=_Cache({f"wt-vehicles-api:{base_url}": b"[]"}), base_url=base_url
    )
    with pytest.raises(ProviderPayloadError, match="no supported"):
        empty.fetch_vehicles()

    rows = {
        "vehicles": [
            {
                "identifier": identifier,
                "country": "usa",
                "vehicle_class": "light_tank",
                "rank": 1,
                "realistic_ground_br": 1.0,
            }
            for identifier in ("us_one", "us_two")
        ],
        "totalPages": 1,
    }
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        source_id = request.url.path.rsplit("/", 1)[-1]
        if source_id in {"us_one", "us_two"}:
            requested.append(source_id)
            return httpx.Response(200, json=_detail(source_id), request=request)
        return httpx.Response(200, json=rows, request=request)

    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(handler)
        ),
        base_url=base_url,
        retry_delay_seconds=0,
    )
    bundle = provider.fetch_operational_components(
        max_details=1, priority_vehicle_ids=("us_two",)
    )
    assert requested == ["us_two"]
    assert {row.vehicle_id for row in bundle.vehicles.records} == {"us_one", "us_two"}
    assert {row.vehicle_id for row in bundle.availability.records} == {"us_one", "us_two"}
    assert [row.vehicle_id for row in bundle.capabilities.records] == ["us_two"]


def test_component_detail_fallback_uses_br_then_id_not_list_order() -> None:
    rows = {
        "vehicles": [
            {
                "identifier": identifier,
                "country": "usa",
                "vehicle_class": "light_tank",
                "rank": 2,
                "realistic_ground_br": br,
            }
            for identifier, br in (("us_z", 3.7), ("us_b", 2.7), ("us_a", 2.7))
        ],
        "totalPages": 1,
    }
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        source_id = request.url.path.rsplit("/", 1)[-1]
        if source_id in {"us_z", "us_b", "us_a"}:
            requested.append(source_id)
            return httpx.Response(200, json=_detail(source_id), request=request)
        return httpx.Response(200, json=rows, request=request)

    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://example.com/api/vehicles",
        retry_delay_seconds=0,
    )
    provider.fetch_operational_components(max_details=2, priority_vehicle_ids=("us_z",))
    assert requested == ["us_z", "us_a"]

    requested.clear()
    provider.fetch_operational_components(
        max_details=2,
        priority_vehicle_ids=("us_z",),
        previously_fetched_ids=("us_z", "us_a"),
    )
    assert requested == ["us_z", "us_b"]


def test_operational_components_activate_after_persistence(tmp_path) -> None:
    list_payload = {
        "vehicles": [
            {
                "identifier": "us_m5a1_stuart",
                "country": "usa",
                "vehicle_class": "light_tank",
                "rank": 2,
                "realistic_ground_br": 2.7,
            },
            {
                "identifier": "us_m24_chaffee",
                "country": "usa",
                "vehicle_class": "light_tank",
                "rank": 2,
                "realistic_ground_br": 3.7,
            },
        ],
        "totalPages": 1,
    }
    details = {
        "us_m5a1_stuart": _detail("us_m5a1_stuart"),
        "us_m24_chaffee": _detail(
            "us_m24_chaffee",
            required_vehicle="us_m5a1_stuart",
            modifications=[{"icon": "modifications/scouting.png"}],
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        source_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json=details.get(source_id, list_payload),
            headers={"etag": "source-revision"},
            request=request,
        )

    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://example.com/api/vehicles",
        retry_delay_seconds=0,
    )
    bundle = provider.fetch_operational_components(max_br=40)
    repository = EvidenceRepository(create_database(tmp_path / "evidence.sqlite"))
    repository.import_vehicle_dataset(bundle.vehicles)
    repository.import_capability_snapshot(
        bundle.capabilities.snapshot,
        bundle.capabilities.records,
        raw_content=bundle.capabilities.raw_content,
    )
    repository.import_availability_snapshot(
        bundle.availability.snapshot,
        bundle.availability.records,
        raw_content=bundle.availability.raw_content,
    )
    repository.import_research_graph_snapshot(
        bundle.research_graph.snapshot,
        bundle.research_graph.records,
        raw_content=bundle.research_graph.raw_content,
    )

    resolved = repository.resolve_analysis_bundle()
    assert resolved.capability_snapshot is not None
    assert resolved.availability_snapshot is not None
    assert resolved.research_graph_snapshot is not None
    assert (
        resolved.capability_snapshot.snapshot_id == bundle.capabilities.snapshot.snapshot_id
    )
    assert (
        resolved.availability_snapshot.snapshot_id == bundle.availability.snapshot.snapshot_id
    )
    assert (
        resolved.research_graph_snapshot.snapshot_id
        == bundle.research_graph.snapshot.snapshot_id
    )


def test_reimport_backfills_missing_compatibility_key_for_legacy_vehicle_snapshot(tmp_path) -> None:
    list_payload = {
        "vehicles": [
            {
                "identifier": "us_m5a1_stuart",
                "country": "usa",
                "vehicle_class": "light_tank",
                "rank": 2,
                "realistic_ground_br": 2.7,
            }
        ],
        "totalPages": 1,
    }

    provider = WarThunderVehiclesApiProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=_detail("us_m5a1_stuart")
                    if request.url.path.rstrip("/").endswith("us_m5a1_stuart")
                    else list_payload,
                    headers={"etag": "source-revision"},
                    request=request,
                )
            )
        ),
        base_url="https://example.com/api/vehicles",
        retry_delay_seconds=0,
    )
    bundle = provider.fetch_operational_components(max_br=40)
    repository = EvidenceRepository(create_database(tmp_path / "evidence.sqlite"))
    legacy_snapshot = bundle.vehicles.snapshot.model_copy(update={"compatibility_key": None})
    repository.import_vehicle_snapshot(
        legacy_snapshot, bundle.vehicles.records, raw_content=bundle.vehicles.raw_content
    )

    result = repository.import_vehicle_dataset(bundle.vehicles)

    assert result.created is False
    assert repository.resolve_analysis_bundle().vehicle_snapshot.compatibility_key == (
        bundle.vehicles.snapshot.compatibility_key
    )
