from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from wt_advisor.domain.models import GameMode, Nation, UserProfile, VehicleStatus
from wt_advisor.services.advisor import AdvisorService


def test_m3_preset_lifecycle_and_context_are_revisioned(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    created = service.create_preset("acceptance", name="  First lineup  ", slots=("us_m2a4",))
    assert created.name == "First lineup"
    assert created.revision == 1
    context = service.get_advisor_context("acceptance")
    selected = service.update_advisor_context(
        "acceptance", selected_preset_id=created.preset_id, expected_revision=context.revision
    )
    assert selected.selected_preset_id == created.preset_id
    updated = service.update_preset(
        "acceptance",
        created.preset_id,
        name="Second",
        slots=("us_m2a4",),
        required_vehicle_ids=(),
        excluded_vehicle_ids=(),
        expected_revision=created.revision,
    )
    assert updated.revision == 2
    with pytest.raises(ValueError, match="revision changed"):
        service.delete_preset("acceptance", created.preset_id, expected_revision="1")
    service.delete_preset("acceptance", created.preset_id, expected_revision=updated.revision)
    assert service.get_advisor_context("acceptance").selected_preset_id is None


def test_m3_plural_constraints_preserve_legacy_and_reject_ambiguity() -> None:
    service = AdvisorService.from_acceptance_fixture()
    legacy = service.generate_lineups(profile_id="acceptance", top_n=2)
    assert legacy == service.generate_lineups(profile_id="acceptance", top_n=2)
    with pytest.raises(ValueError, match="singular and plural"):
        service.generate_lineups(
            profile_id="acceptance",
            required_vehicle_id="us_m2a4",
            required_vehicle_ids=frozenset({"us_m2a4"}),
        )
    with pytest.raises(ValueError, match="overlap"):
        service.generate_lineups(
            profile_id="acceptance",
            required_vehicle_ids=frozenset({"us_m2a4"}),
            excluded_vehicle_ids=frozenset({"us_m2a4"}),
        )


def test_m3_vehicle_status_guard_is_optional_but_enforced_when_given(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    revision = service.get_user_progress("acceptance").revision
    service.set_user_vehicle_status(
        "acceptance", "us_m3_lee", VehicleStatus.OWNED, expected_revision=revision
    )
    with pytest.raises(ValueError, match="revision changed"):
        service.set_user_vehicle_status(
            "acceptance", "us_m3_lee", VehicleStatus.LOCKED, expected_revision=revision
        )


def test_m3_stored_evaluation_is_immutable_and_detects_garage_staleness(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    saved = service.evaluate_and_store(profile_id="acceptance", top_n=2)
    read = service.get_stored_evaluation("acceptance", saved["evaluation_id"])
    assert read["result_id"] == saved["result_id"]
    assert read["stale"] is False

    revision = service.get_user_progress("acceptance").revision
    service.set_user_vehicle_status(
        "acceptance", "us_m3_lee", VehicleStatus.OWNED, expected_revision=revision
    )
    stale = service.get_stored_evaluation("acceptance", saved["evaluation_id"])
    assert stale["result"] == saved["result"]
    assert stale["stale_reasons"] == ("profile_garage_state_changed",)


def test_m3_write_revision_is_atomic_and_cannot_be_combined_with_public_token(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    token = service.get_profile_write_revision("acceptance")
    service.set_user_vehicle_status(
        "acceptance", "us_m3_lee", VehicleStatus.OWNED, expected_write_revision=token
    )
    with pytest.raises(ValueError, match="write revision changed"):
        service.set_user_vehicle_status(
            "acceptance", "us_m3_lee", VehicleStatus.LOCKED, expected_write_revision=token
        )
    with pytest.raises(ValueError, match="mutually exclusive"):
        service.set_user_vehicle_status(
            "acceptance",
            "us_m3_lee",
            VehicleStatus.LOCKED,
            expected_revision=service.get_user_progress("acceptance").revision,
            expected_write_revision=service.get_profile_write_revision("acceptance"),
        )


def test_m3_two_independent_services_allow_exactly_one_guarded_write(tmp_path) -> None:
    database = tmp_path / "advisor.sqlite"
    first = AdvisorService.from_database(database)
    second = AdvisorService.from_database(database)
    revision = first.get_user_progress("acceptance").revision

    first.set_user_vehicle_status(
        "acceptance", "us_m3_lee", VehicleStatus.OWNED, expected_revision=revision
    )
    with pytest.raises(ValueError, match="profile revision changed"):
        second.set_user_vehicle_status(
            "acceptance", "us_m3_lee", VehicleStatus.LOCKED, expected_revision=revision
        )
    assert (
        first.get_user_progress("acceptance").vehicle_statuses["us_m3_lee"]
        is VehicleStatus.OWNED
    )


def test_m3_simultaneous_clients_atomically_guard_public_revision(tmp_path) -> None:
    database = tmp_path / "advisor.sqlite"
    clients = (AdvisorService.from_database(database), AdvisorService.from_database(database))
    revision = clients[0].get_user_progress("acceptance").revision
    ready = Barrier(2)

    def write(client: AdvisorService, status: VehicleStatus) -> str:
        ready.wait()
        try:
            client.set_user_vehicle_status(
                "acceptance", "us_m3_lee", status, expected_revision=revision
            )
        except ValueError as error:
            assert "revision changed" in str(error)
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            pool.map(write, clients, (VehicleStatus.OWNED, VehicleStatus.LOCKED))
        )

    assert sorted(outcomes) == ["conflict", "saved"]


def test_m3_hypothetical_evaluation_and_saved_preset_do_not_mutate_garage(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    before = service.get_user_progress("acceptance")
    result = service.evaluate_and_store(
        profile_id="acceptance",
        hypothetical_owned=frozenset({"us_m3_lee"}),
        top_n=2,
    )
    service.create_preset("acceptance", name="Hypothetical", slots=("us_m3_lee",))
    after = service.get_user_progress("acceptance")

    assert result["effective_inputs"]["hypothetical_owned"] == ["us_m3_lee"]
    assert after.model_dump(mode="json") == before.model_dump(mode="json")


def test_m3_evaluation_calculates_only_from_captured_transaction_snapshot(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "advisor.sqlite"
    service = AdvisorService.from_database(database)
    writer = AdvisorService.from_database(database)
    expected = service.generate_lineups(profile_id="acceptance", top_n=2)
    repository = service._repository
    assert repository is not None
    original_capture = repository.capture_evaluation_snapshot

    def capture_then_write(profile_id: str, *, preset_id: str | None = None):
        captured = original_capture(profile_id, preset_id=preset_id)
        writer.set_user_vehicle_status("acceptance", "us_m2a4", VehicleStatus.LOCKED)
        return captured

    monkeypatch.setattr(repository, "capture_evaluation_snapshot", capture_then_write)
    saved = service.evaluate_and_store(profile_id="acceptance", top_n=2)

    assert saved["result_id"] == expected.analysis_id
    assert service.get_stored_evaluation("acceptance", saved["evaluation_id"])[
        "stale_reasons"
    ] == ("profile_garage_state_changed",)


def test_m3_staleness_tracks_saved_preset_revision_not_viewed_context(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    source = service.create_preset("acceptance", name="Source", slots=("us_m2a4",))
    other = service.create_preset("acceptance", name="Other", slots=("us_m2a4",))
    saved = service.evaluate_and_store(
        profile_id="acceptance", top_n=2, preset_id=source.preset_id
    )
    context = service.get_advisor_context("acceptance")
    service.update_advisor_context(
        "acceptance",
        selected_preset_id=other.preset_id,
        expected_revision=context.revision,
    )
    assert service.get_stored_evaluation("acceptance", saved["evaluation_id"])["stale"] is False

    service.update_preset(
        "acceptance",
        source.preset_id,
        name="Source revised",
        slots=("us_m2a4",),
        required_vehicle_ids=(),
        excluded_vehicle_ids=(),
        expected_revision=source.revision,
    )
    assert service.get_stored_evaluation("acceptance", saved["evaluation_id"])[
        "stale_reasons"
    ] == ("source_preset_changed",)


def test_m3_staleness_tracks_saved_context_constraints(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    saved = service.evaluate_and_store(profile_id="acceptance", top_n=2)
    context = service.get_advisor_context("acceptance")

    service.update_advisor_context(
        "acceptance",
        selected_preset_id=None,
        target_br=20,
        required_vehicle_ids=("us_m2a4",),
        excluded_vehicle_ids=("us_m22",),
        expected_revision=context.revision,
    )

    assert service.get_stored_evaluation("acceptance", saved["evaluation_id"])[
        "stale_reasons"
    ] == ("effective_constraints_changed",)


def test_m3_context_rejects_preset_owned_by_another_profile(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    repository = service._repository
    assert repository is not None
    repository.create_profile(
        UserProfile(
            profile_id="other",
            nation=Nation.USA,
            preferred_mode=GameMode.GROUND_REALISTIC,
            crew_slots=3,
        )
    )
    foreign = repository.create_preset(
        "other",
        preset_id="foreign-preset",
        name="Foreign",
        slots=("us_m2a4",),
        required_vehicle_ids=(),
        excluded_vehicle_ids=(),
    )

    context = service.get_advisor_context("acceptance")
    with pytest.raises(LookupError, match="unknown preset"):
        service.update_advisor_context(
            "acceptance",
            selected_preset_id=foreign.preset_id,
            expected_revision=context.revision,
        )


def test_m3_preset_constraints_are_captured_and_used_for_evaluation(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    preset = service.create_preset(
        "acceptance",
        name="Pinned source",
        slots=("us_m2a4",),
        required_vehicle_ids=("us_m3_lee",),
        excluded_vehicle_ids=("us_m22",),
    )

    saved = service.evaluate_and_store(
        profile_id="acceptance",
        top_n=2,
        hypothetical_owned=frozenset({"us_m3_lee"}),
        preset_id=preset.preset_id,
        allow_partial=True,
    )

    assert saved["effective_inputs"]["preset_slots"] == ["us_m2a4"]
    assert saved["effective_inputs"]["required_vehicle_ids"] == ["us_m2a4", "us_m3_lee"]
    assert saved["effective_inputs"]["excluded_vehicle_ids"] == ["us_m22"]
    candidates = [
        candidate
        for group in saved["result"]["groups"]
        for candidate in group["candidates"]
    ]
    assert candidates
    for candidate in candidates:
        slots = candidate["analysis"]["lineup"]["slots"]
        assert {"us_m2a4", "us_m3_lee"}.issubset(slots)
        assert "us_m22" not in slots
