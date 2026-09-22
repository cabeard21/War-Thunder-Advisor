import socket
import subprocess
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_mcp_tunnel_launcher_uses_safe_defaults_and_restores_location() -> None:
    launcher = (REPOSITORY_ROOT / "scripts" / "start-mcp-tunnel.ps1").read_text(
        encoding="utf-8"
    )

    assert 'Profile = "wt-advisor"' in launcher
    assert '[string]$DatabasePath' in launcher
    assert '"wt-advisor-live-acceptance.sqlite"' in launcher
    assert 'Test-Path -LiteralPath $resolvedDatabasePath -PathType Leaf' in launcher
    assert '$env:WT_ADVISOR_DB = $resolvedDatabasePath' in launcher
    assert '$env:WT_ADVISOR_DB = $originalDatabasePath' in launcher
    assert 'WTA_TUNNEL_CLIENT' in launcher
    assert 'D:\\ChatGPT_MCP_Tunnel\\tunnel-client.exe' in launcher
    assert 'Set-Location -LiteralPath $repositoryRoot' in launcher
    assert 'run --profile $Profile' in launcher
    assert 'Set-Location -LiteralPath $originalLocation' in launcher


def test_mcp_tunnel_launcher_runs_dashboard_for_tunnel_lifetime() -> None:
    launcher = (REPOSITORY_ROOT / "scripts" / "start-mcp-tunnel.ps1").read_text(
        encoding="utf-8"
    )

    assert "[ValidateRange(1024, 65535)]" in launcher
    assert "[int]$DashboardPort = 8765" in launcher
    assert '".venv\\Scripts\\wt-advisor.exe"' in launcher
    assert 'Start-Process' in launcher
    assert 'ArgumentList @("dashboard", "--port", $DashboardPort)' in launcher
    assert 'Invoke-WebRequest -Uri $dashboardUri' in launcher
    assert '$dashboardProcess.HasExited' in launcher
    assert 'Dashboard did not become ready at' in launcher
    assert 'Stop-Process -Id $dashboardProcess.Id' in launcher
    assert '-ErrorAction SilentlyContinue' in launcher


def test_batch_wrapper_forwards_arguments_to_the_powershell_launcher() -> None:
    wrapper = (REPOSITORY_ROOT / "start-mcp-tunnel.bat").read_text(encoding="utf-8")

    assert 'scripts\\start-mcp-tunnel.ps1' in wrapper
    assert '%*' in wrapper
    assert 'exit /b %ERRORLEVEL%' in wrapper


def test_launcher_starts_dashboard_then_propagates_tunnel_exit_and_cleans_up(
    tmp_path: Path,
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    fake_tunnel = tmp_path / "fake tunnel.cmd"
    fake_tunnel.write_text("@exit /b 23\n", encoding="utf-8")
    launcher = REPOSITORY_ROOT / "scripts" / "start-mcp-tunnel.ps1"
    database = tmp_path / "launcher.sqlite"
    database.touch()

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(launcher),
            "-TunnelClientPath",
            str(fake_tunnel),
            "-DashboardPort",
            str(port),
            "-DatabasePath",
            str(database),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 23, result.stderr
    for _ in range(20):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                break
        time.sleep(0.1)
    else:
        raise AssertionError("dashboard port remained open after the tunnel exited")


def test_launcher_refuses_an_occupied_dashboard_port(tmp_path: Path) -> None:
    database = tmp_path / "launcher.sqlite"
    database.touch()
    marker = tmp_path / "tunnel-started.txt"
    fake_tunnel = tmp_path / "fake tunnel.cmd"
    fake_tunnel.write_text(f'@echo started>"{marker}"\n', encoding="utf-8")
    launcher = REPOSITORY_ROOT / "scripts" / "start-mcp-tunnel.ps1"

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(launcher),
                "-TunnelClientPath",
                str(fake_tunnel),
                "-DashboardPort",
                str(port),
                "-DatabasePath",
                str(database),
            ],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    assert result.returncode != 0
    assert "already in use" in result.stderr
    assert not marker.exists()
