"""Community statistics are useful context, never invented Ground RB evidence."""

from datetime import UTC, date, datetime

import httpx
import pytest

from wt_advisor.data.providers.community_statistics import (
    CommunityStatisticsError,
    CommunityStatisticsProvider,
)
from wt_advisor.domain.models import KillTargetDefinition, StatisticsScope


def _provider(csv_text: str, *, cache=None):
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=[
                {"name": "2026-06-01.csv", "type": "file", "size": 200},
                {"name": "2026-09-20.csv", "type": "file", "size": 500},
            ], headers={"etag": '"revision-1"'})
        return httpx.Response(200, text=csv_text)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    return CommunityStatisticsProvider(client=client, cache=cache), calls


def test_partial_rows_are_quarantined_and_rb_scope_is_retained_for_proxy():
    csv_text = ("name,nation,cls,rb_battles,rb_win_rate,rb_ground_frags_per_battle\n"
                "m24,USA,Ground_vehicles,140,53.5,1.2\n"
                "unknown,USA,Ground_vehicles,100,51,0.8\n"
                "m3,USA,Ground_vehicles,bad,49,0.4\n"
                "air,USA,Aviation,100,60,1\n")
    provider, _ = _provider(csv_text)
    result = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24", "m3": "us_m3_lee"},
        canonical_vehicle_ids={"us_m24", "us_m3_lee"},
        now=datetime(2026, 9, 21, tzinfo=UTC),
    )
    assert result.accepted_count == 1
    assert result.quarantine_counts == {
        "unresolved_identity": 1, "invalid_metric": 1, "wrong_scope": 1,
    }
    assert result.dataset is not None
    assert result.dataset.records[0].vehicle_id == "us_m24"
    assert result.dataset.records[0].reported_win_rate == .535
    assert result.dataset.records[0].mode_scope == StatisticsScope.REALISTIC_ALL_CONTEXTS
    assert result.eligible_count == 1
    assert result.age_days == 1


def test_duplicate_canonical_identity_quarantines_both_rows():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_win_rate\n"
        "m24,USA,Ground_vehicles,40,50\n"
        "chaffee,USA,Ground_vehicles,40,50\n"
    )
    result = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24", "chaffee": "us_m24"},
        canonical_vehicle_ids={"us_m24"},
    )
    assert result.accepted_count == 0
    assert result.quarantine_counts == {"duplicate_identity": 2}
    assert result.dataset is None


def test_bounded_download_and_cache_reuse():
    class Cache:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    cache = Cache()
    provider, calls = _provider(
        "name,nation,cls,rb_battles\nm24,USA,Ground_vehicles,5\n", cache=cache,
    )
    provider.fetch_statistics(identity_aliases={"m24": "us_m24"}, canonical_vehicle_ids={"us_m24"})
    provider.fetch_statistics(identity_aliases={"m24": "us_m24"}, canonical_vehicle_ids={"us_m24"})
    assert len(calls) == 3  # Always rediscover newest date; cache only immutable dated CSV.


def test_content_checksum_and_compatibility_are_preserved():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_win_rate\nm24,USA,Ground_vehicles,40,50\n",
    )
    result = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24"}, canonical_vehicle_ids={"us_m24"},
        compatibility_key="vehicles-revision-1", now=datetime(2026, 12, 31, tzinfo=UTC),
    )
    assert result.dataset is not None
    assert result.dataset.snapshot.compatibility_key == "vehicles-revision-1"
    assert result.dataset.snapshot.sample_end.isoformat() == "2026-09-20"
    assert result.age_days == 102
    from hashlib import sha256

    assert result.dataset.snapshot.checksum == sha256(result.dataset.raw_content).hexdigest()
    assert result.eligible_count == 0


def test_same_source_with_changed_catalog_or_alias_map_gets_new_normalization_identity():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_win_rate\n"
        "m24,USA,Ground_vehicles,40,50\n"
    )
    common = {"canonical_vehicle_ids": {"us_m24", "us_m3_lee"}}
    first = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24"}, compatibility_key="catalog-a", **common,
    )
    changed_catalog = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24"}, compatibility_key="catalog-b", **common,
    )
    changed_alias = provider.fetch_statistics(
        identity_aliases={"m24": "us_m3_lee"}, compatibility_key="catalog-a", **common,
    )
    assert first.dataset is not None
    assert changed_catalog.dataset is not None
    assert changed_alias.dataset is not None
    assert len({
        x.dataset.snapshot.snapshot_id for x in (first, changed_catalog, changed_alias)
    }) == 3
    assert len({x.dataset.snapshot.checksum for x in (first, changed_catalog, changed_alias)}) == 1


def test_invalid_nonfinite_ratio_and_zero_battles_are_quarantined():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_win_rate\n"
        "m24,USA,Ground_vehicles,0,50\n"
        "m3,USA,Ground_vehicles,100,nan\n",
    )
    result = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24", "m3": "us_m3_lee"},
        canonical_vehicle_ids={"us_m24", "us_m3_lee"},
    )
    assert result.dataset is not None
    assert result.dataset.records[0].vehicle_id == "us_m3_lee"
    assert result.dataset.records[0].reported_win_rate is None
    assert result.quarantine_counts == {"invalid_metric": 1, "invalid_rb_win_rate": 1}


def test_real_joined_schema_uses_exact_source_name_and_decimal_battle_count():
    # Shape and example identity from upstream joined/2024-06-22.csv.
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_win_rate,rb_ground_frags_per_battle,wk_name\n"
        "us_m24_chaffee,USA,Ground_vehicles,1232.0,59.57,2.42,M24\n"
    )
    result = provider.fetch_statistics(
        identity_aliases={"us_m24_chaffee": "us_m24"},
        canonical_vehicle_ids={"us_m24"},
    )
    assert result.accepted_count == 1
    assert result.dataset is not None
    assert result.dataset.records[0].battles == 1232
    assert result.dataset.records[0].vehicle_id == "us_m24"


def test_exact_source_identity_does_not_guess_from_colliding_display_name():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,wk_name\n"
        "unknown_tank,USA,Ground_vehicles,20.0,M24\n"
    )
    result = provider.fetch_statistics(
        identity_aliases={"us_m24_chaffee": "us_m24"},
        canonical_vehicle_ids={"us_m24"},
    )
    assert result.dataset is None
    assert result.quarantine_counts == {"unresolved_identity": 1}


def test_oversized_download_is_rejected_before_parsing():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 2_000_001)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = CommunityStatisticsProvider(client=client)
    with pytest.raises(CommunityStatisticsError, match="byte limit"):
        provider.fetch_statistics(identity_aliases={}, canonical_vehicle_ids=set())


def test_ground_ratios_keep_zero_and_missing_distinct():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_win_rate,rb_ground_frags_per_battle,"
        "rb_ground_frags_per_death\n"
        "m24,USA,Ground_vehicles,20,0,0,0\n"
        "m3,USA,Ground_vehicles,21,NA,null,\n"
    )
    result = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24", "m3": "us_m3_lee"},
        canonical_vehicle_ids={"us_m24", "us_m3_lee"},
        now=datetime(2026, 9, 21, tzinfo=UTC),
    )
    assert result.dataset is not None
    records = {item.vehicle_id: item for item in result.dataset.records}
    assert records["us_m24"].reported_win_rate == 0
    assert records["us_m24"].reported_kills_per_battle == 0
    assert records["us_m24"].reported_kd == 0
    assert records["us_m24"].kill_target_definition == KillTargetDefinition.GROUND_TARGETS
    assert records["us_m3_lee"].reported_win_rate is None
    assert records["us_m3_lee"].reported_kills_per_battle is None
    assert records["us_m3_lee"].reported_kd is None
    assert result.eligible_count == 1


def test_huge_decimal_that_overflows_float_is_rejected_per_metric():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_win_rate,rb_ground_frags_per_battle\n"
        "m24,USA,Ground_vehicles,40,50,1e999\n"
    )
    result = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24"}, canonical_vehicle_ids={"us_m24"},
    )
    assert result.dataset is not None
    row = result.dataset.records[0]
    assert row.reported_win_rate == 0.5
    assert row.reported_kills_per_battle is None
    assert result.quarantine_counts == {"invalid_rb_ground_frags_per_battle": 1}


def test_invalid_metric_does_not_discard_other_metrics():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_win_rate,rb_ground_frags_per_battle,"
        "rb_ground_frags_per_death\n"
        "m24,USA,Ground_vehicles,20,60,-1,1.25\n"
    )
    result = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24"}, canonical_vehicle_ids={"us_m24"},
    )
    assert result.dataset is not None
    record = result.dataset.records[0]
    assert record.reported_win_rate == .6
    assert record.reported_kills_per_battle is None
    assert record.reported_kd == 1.25
    assert result.quarantine_counts == {"invalid_rb_ground_frags_per_battle": 1}


def test_nonfinite_win_rate_and_out_of_range_rate_are_metric_local():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_win_rate,rb_ground_frags_per_death\n"
        "m24,USA,Ground_vehicles,20,nan,1\n"
        "m3,USA,Ground_vehicles,20,110,2\n"
    )
    result = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24", "m3": "us_m3_lee"},
        canonical_vehicle_ids={"us_m24", "us_m3_lee"},
    )
    assert result.accepted_count == 2
    assert result.quarantine_counts == {"invalid_rb_win_rate": 2}
    assert all(record.reported_win_rate is None for record in result.dataset.records)
    assert {record.reported_kd for record in result.dataset.records} == {1, 2}


def test_retained_bytes_reprocess_without_network_and_preserve_source_revision():
    provider, calls = _provider("unused")
    content = (
        b"name,nation,cls,rb_battles,rb_ground_frags_per_death\n"
        b"m24,USA,Ground_vehicles,20,1.5\n"
    )
    result = provider.fetch_statistics(
        identity_aliases={"m24": "us_m24"}, canonical_vehicle_ids={"us_m24"},
        retained_content=content, observation_date=date(2026, 9, 20),
        source_revision="retained-revision",
    )
    assert calls == []
    assert result.dataset is not None
    assert result.dataset.raw_content == content
    assert result.dataset.snapshot.source_revision == "retained-revision"
    assert result.dataset.records[0].reported_kd == 1.5


def test_exact_source_aliases_keep_m3_gmc_and_m2a4_variant_distinct():
    provider, _ = _provider(
        "name,nation,cls,rb_battles,rb_ground_frags_per_death\n"
        "us_halftrack_m3_75mm_gmc,USA,Ground_vehicles,20,2\n"
        "us_m2a4_1st_armor_div,USA,Ground_vehicles,30,3\n"
        "us_m2a4,USA,Ground_vehicles,40,4\n"
    )
    result = provider.fetch_statistics(
        identity_aliases={
            "us_halftrack_m3_75mm_gmc": "us_m3_gmc",
            "us_m2a4_1st_armor_div": "us_m2a4_first_tank_div",
            "us_m2a4": "us_m2a4",
        },
        canonical_vehicle_ids={"us_m3_gmc", "us_m2a4_first_tank_div", "us_m2a4"},
    )
    assert result.accepted_count == 3
    assert {row.vehicle_id: row.reported_kd for row in result.dataset.records} == {
        "us_m3_gmc": 2, "us_m2a4_first_tank_div": 3, "us_m2a4": 4,
    }
