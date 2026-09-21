from fastapi.testclient import TestClient

from wt_advisor.services.advisor import AdvisorService
from wt_advisor.web.app import create_app


def test_local_dashboard_api_returns_data_and_rejects_external_host() -> None:
    client = TestClient(create_app(AdvisorService.from_acceptance_fixture()))
    assert client.get("/api/data-status").status_code == 200
    assert client.get("/api/data-status", headers={"host": "example.com"}).status_code == 400


def test_packaged_dashboard_index_is_served_without_vite() -> None:
    client = TestClient(create_app(AdvisorService.from_acceptance_fixture()))
    response = client.get("/")
    assert response.status_code == 200
    assert "War Thunder" in response.text


def test_stored_evaluation_http_read_is_passive_and_stale_is_structured(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    client = TestClient(create_app(service))
    created = client.post("/api/evaluations", json={"profile_id": "acceptance", "top_n": 2})
    assert created.status_code == 200
    evaluation_id = created.json()["data"]["evaluation_id"]
    fetched = client.get(f"/api/profiles/acceptance/evaluations/{evaluation_id}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["stale"] is False


def test_real_backend_http_workflow_persists_preset_context_and_reports_conflicts(tmp_path) -> None:
    database = tmp_path / "advisor.sqlite"
    service = AdvisorService.from_database(database)
    client = TestClient(create_app(service))
    progress = client.get("/api/profiles/acceptance/progress").json()["data"]

    changed = client.patch(
        "/api/profiles/acceptance/vehicles/us_m3_lee",
        json={"status": "owned", "expected_revision": progress["revision"]},
    )
    assert changed.status_code == 200
    conflict = client.patch(
        "/api/profiles/acceptance/vehicles/us_m3_lee",
        json={"status": "locked", "expected_revision": progress["revision"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"

    preset_response = client.post(
        "/api/profiles/acceptance/presets",
        json={"name": "  Browser workflow  ", "slots": ["us_m2a4"]},
    )
    assert preset_response.status_code == 200
    preset = preset_response.json()["data"]
    context = client.get("/api/profiles/acceptance/context").json()["data"]
    context_response = client.put(
        "/api/profiles/acceptance/context",
        json={
            "expected_revision": str(context["revision"]),
            "selected_preset_id": preset["preset_id"],
            "required_vehicle_ids": ["us_m2a4"],
            "excluded_vehicle_ids": ["us_m3_lee"],
        },
    )
    assert context_response.status_code == 200

    restarted = TestClient(create_app(AdvisorService.from_database(database)))
    persisted = restarted.get("/api/profiles/acceptance/context").json()["data"]
    assert persisted["selected_preset_id"] == preset["preset_id"]
    assert persisted["required_vehicle_ids"] == ["us_m2a4"]
    assert persisted["excluded_vehicle_ids"] == ["us_m3_lee"]


def test_http_unsatisfiable_constraints_are_structured(tmp_path) -> None:
    client = TestClient(create_app(AdvisorService.from_database(tmp_path / "advisor.sqlite")))
    response = client.post(
        "/api/evaluate",
        json={
            "profile_id": "acceptance",
            "excluded_vehicle_ids": [
                "us_m2a4",
                "us_m2a4_1st",
                "us_m3_stuart",
                "us_m3_gmc",
                "us_m13_mgmc",
                "us_m15_cgmc",
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsatisfiable_constraints"
    assert response.json()["error"]["details"]["action"]
