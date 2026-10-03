from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wt_advisor.cli.main import create_app as create_cli
from wt_advisor.domain.models import VehicleStatus
from wt_advisor.runtime import RuntimeSettings, database_path
from wt_advisor.services.advisor import AdvisorService
from wt_advisor.web.app import create_app


def test_runtime_defaults_preserve_local_development() -> None:
    settings = RuntimeSettings.from_environment({})
    assert settings.database == Path("wt-advisor.sqlite")
    assert settings.bind_address == "127.0.0.1"
    assert settings.port == 8765
    assert settings.allowed_hosts == frozenset({"127.0.0.1", "localhost", "testserver", "::1"})
    with pytest.raises(FrozenInstanceError):
        settings.port = 1234  # type: ignore[misc]


def test_private_runtime_configuration_is_explicit_and_normalized() -> None:
    settings = RuntimeSettings.from_environment(
        {
            "WT_ADVISOR_DB": "/data/custom.sqlite",
            "WT_ADVISOR_BIND_ADDRESS": "0.0.0.0",
            "WT_ADVISOR_PORT": "8507",
            "WT_ADVISOR_ALLOWED_HOSTS": " Pi.LOCAL,100.117.157.57,fd7a:115c:a1e0::1,pi.local ",
        }
    )
    assert settings.database == Path("/data/custom.sqlite")
    assert settings.bind_address == "0.0.0.0"
    assert settings.port == 8507
    assert {"pi.local", "100.117.157.57", "fd7a:115c:a1e0::1"} <= settings.allowed_hosts


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "*",
        "*.local",
        "http://pi.local",
        "pi.local:8765",
        "bad_host",
        "-pi.local",
        "pi-.local",
        "pi..local",
        "pi.local,",
        "pi.local,,localhost",
        "user@pi.local",
        "@pi.local",
        "pi.local:",
        "[::1]:",
        "[::1]junk",
        "[::1]x:8765",
        "[::1][evil]",
        "pi.local?",
        "pi.local#",
        "pi.local/path",
        "pi.local?x",
        "pi.local#x",
        "999.1.1.1",
        "0.0.0.0",
        "::",
        "fe80::1%eth0",
        "[::1]",
        "a" * 64 + ".local",
        "pi.\nlocal",
    ],
)
def test_runtime_rejects_invalid_host_configuration(value: str) -> None:
    with pytest.raises(ValueError, match="WT_ADVISOR_ALLOWED_HOSTS"):
        RuntimeSettings.from_environment({"WT_ADVISOR_ALLOWED_HOSTS": value})


@pytest.mark.parametrize("hosts", [None, "127.0.0.1,localhost,::1,testserver"])
def test_all_interface_binding_requires_a_remote_host(hosts: str | None) -> None:
    environment = {"WT_ADVISOR_BIND_ADDRESS": "0.0.0.0"}
    if hosts is not None:
        environment = {**environment, "WT_ADVISOR_ALLOWED_HOSTS": hosts}
    with pytest.raises(ValueError, match="WT_ADVISOR_ALLOWED_HOSTS"):
        RuntimeSettings.from_environment(environment)


@pytest.mark.parametrize("value", ["", "localhost", "192.168.1.10", "::", "true"])
def test_runtime_rejects_unsupported_binding(value: str) -> None:
    with pytest.raises(ValueError, match="WT_ADVISOR_BIND_ADDRESS"):
        RuntimeSettings.from_environment({"WT_ADVISOR_BIND_ADDRESS": value})


@pytest.mark.parametrize("value", ["", "abc", "-1", "1023", "65536", "1.5"])
def test_runtime_rejects_invalid_environment_ports(value: str) -> None:
    with pytest.raises(ValueError, match="WT_ADVISOR_PORT"):
        RuntimeSettings.from_environment({"WT_ADVISOR_PORT": value})


@pytest.mark.parametrize("value", ["1024", "65535"])
def test_runtime_accepts_port_boundaries(value: str) -> None:
    assert RuntimeSettings.from_environment({"WT_ADVISOR_PORT": value}).port == int(value)


def test_explicit_port_overrides_environment() -> None:
    assert RuntimeSettings.from_environment({"WT_ADVISOR_PORT": "bad"}, port=9000).port == 9000


@pytest.mark.parametrize("value", ["", " ", "\x00"])
def test_runtime_rejects_empty_or_invalid_database_path(value: str) -> None:
    with pytest.raises(ValueError, match="WT_ADVISOR_DB"):
        database_path({"WT_ADVISOR_DB": value})


def test_database_override_does_not_require_dashboard_configuration() -> None:
    assert database_path(
        {
            "WT_ADVISOR_DB": "custom.sqlite",
            "WT_ADVISOR_BIND_ADDRESS": "0.0.0.0",
        }
    ) == Path("custom.sqlite")


@pytest.fixture
def private_client(tmp_path: Path) -> TestClient:
    settings = RuntimeSettings.from_environment(
        {
            "WT_ADVISOR_BIND_ADDRESS": "0.0.0.0",
            "WT_ADVISOR_ALLOWED_HOSTS": "pi.local,100.117.157.57,fd7a:115c:a1e0::1",
        }
    )
    return TestClient(
        create_app(
            AdvisorService.from_database(tmp_path / "private.sqlite"),
            settings=settings,
        )
    )


def test_health_does_not_contact_providers_or_calculate_advice() -> None:
    service = AdvisorService.from_acceptance_fixture()
    service.data_status = Mock(side_effect=AssertionError("health must not inspect evidence"))
    service.refresh_community_evidence = Mock(side_effect=AssertionError("health must not refresh"))
    service.compute_advisor_snapshot = Mock(side_effect=AssertionError("health must not calculate"))
    assert TestClient(create_app(service)).get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "host",
    [
        "pi.local:8765",
        "PI.LOCAL:8765",
        "100.117.157.57:8765",
        "[fd7a:115c:a1e0::1]:8765",
        "localhost:8765",
    ],
)
def test_health_allows_configured_hosts_and_remains_cheap(
    private_client: TestClient,
    host: str,
) -> None:
    response = private_client.get("/health", headers={"host": host})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "host",
    [
        "evil.test",
        "pi.local.evil.test",
        "pi.local:bad",
        "pi.local:65536",
        "pi.local:0",
        " pi.local",
        "[127.0.0.1]:8765",
        "user@pi.local",
        "@pi.local",
        "pi.local:",
        "[::1]:",
        "[::1]junk",
        "[::1]x:8765",
        "[::1][evil]",
        "pi.local?",
        "pi.local#",
        "pi.local/path",
        "[broken",
        "",
    ],
)
def test_health_rejects_unconfigured_or_malformed_hosts(
    private_client: TestClient,
    host: str,
) -> None:
    response = private_client.get("/health", headers={"host": host})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "host_rejected"


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.test",
        "http://pi.local:8507",
        "https://pi.local:8765",
        "http://pi.local:8765/path",
        "http://pi.local:8765?x",
        "http://pi.local:8765#x",
        "http://[broken",
        "null",
        "http://pi.local:bad",
    ],
)
def test_private_write_rejects_cross_origin_and_malformed_origin(
    private_client: TestClient,
    origin: str,
) -> None:
    response = private_client.post(
        "/api/profiles/acceptance/presets",
        json={"name": "Private preset"},
        headers={"host": "pi.local:8765", "origin": origin},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_rejected"


def test_private_write_accepts_same_origin_and_rejects_cross_site(
    private_client: TestClient,
) -> None:
    response = private_client.post(
        "/api/profiles/acceptance/presets",
        json={"name": "Private preset"},
        headers={"host": "pi.local:8765", "origin": "http://pi.local:8765"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Private preset"
    response = private_client.post(
        "/api/profiles/acceptance/presets",
        json={"name": "Cross-site"},
        headers={"host": "pi.local:8765", "sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403


def test_default_health_rejects_private_hosts_without_opt_in() -> None:
    client = TestClient(
        create_app(
            AdvisorService.from_acceptance_fixture(),
            settings=RuntimeSettings.from_environment({}),
        )
    )
    assert client.get("/health").status_code == 200
    assert client.get("/health", headers={"host": "pi.local:8765"}).status_code == 400


def test_http_database_override_persists_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "mounted" / "custom.sqlite"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WT_ADVISOR_DB", str(target))
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        response = client.post("/api/profiles/acceptance/presets", json={"name": "Persisted"})
        assert response.status_code == 200
    with TestClient(create_app()) as restarted:
        presets = restarted.get("/api/profiles/acceptance/presets").json()["data"]
        assert any(preset["name"] == "Persisted" for preset in presets)
    assert target.is_file()
    assert not (tmp_path / "wt-advisor.sqlite").exists()


def test_cli_and_mcp_share_database_path_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wt_advisor.mcp import server

    target = tmp_path / "mounted" / "shared.sqlite"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WT_ADVISOR_DB", str(target))
    result = CliRunner().invoke(create_cli(), ["data", "status", "--json"])
    assert result.exit_code == 0, result.output
    service = AdvisorService.from_database(target)
    service.set_user_vehicle_status("acceptance", "us_m3_lee", VehicleStatus.OWNED)
    create_server = Mock()
    monkeypatch.setattr(server, "create_server", create_server)
    server.main()
    captured = create_server.call_args.args[0]
    assert captured.get_user_progress("acceptance").vehicle_statuses["us_m3_lee"] == "owned"
    create_server.return_value.run.assert_called_once_with("stdio")
    assert not (tmp_path / "wt-advisor.sqlite").exists()


@pytest.mark.parametrize("arguments,expected_port", [([], 9000), (["--port", "9001"], 9001)])
def test_cli_dashboard_uses_private_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected_port: int,
) -> None:
    run = Mock()
    monkeypatch.setattr("wt_advisor.cli.main.uvicorn.run", run)
    monkeypatch.setenv("WT_ADVISOR_BIND_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("WT_ADVISOR_ALLOWED_HOSTS", "pi.local")
    monkeypatch.setenv("WT_ADVISOR_PORT", "9000")
    result = CliRunner().invoke(
        create_cli(AdvisorService.from_acceptance_fixture()),
        [
            "dashboard",
            *arguments,
        ],
    )
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs == {
        "host": "0.0.0.0",
        "port": expected_port,
        "proxy_headers": False,
    }


def test_cli_invalid_runtime_fails_before_creating_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WT_ADVISOR_BIND_ADDRESS", "0.0.0.0")
    monkeypatch.delenv("WT_ADVISOR_ALLOWED_HOSTS", raising=False)
    result = CliRunner().invoke(create_cli(), ["dashboard"])
    assert result.exit_code != 0
    assert "WT_ADVISOR_ALLOWED_HOSTS" in result.output
    assert not (tmp_path / "wt-advisor.sqlite").exists()
