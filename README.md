# War Thunder Lineup Advisor

An evidence-first, deterministic local advisor for War Thunder lineups. Milestone 2 adds
independently versioned capabilities, availability, identity aliases, research prerequisites,
and statistics while preserving the frozen Milestone 1 behavior. The initial operational scope
is USA Ground Realistic Battles through approximately BR 4.0.

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

The acceptance fixture is deliberately frozen and network-free. Live provider refreshes create
new immutable snapshots but are not required to run tests.

## Usage

```powershell
wt-advisor data status --json
wt-advisor data import --source wt-api --json
wt-advisor vehicles list --max-br 40 --json
wt-advisor lineup generate --profile acceptance --top 5 --json
wt-advisor progress next --profile acceptance --json
wt-advisor statistics inspect export.csv --provider statshark --purpose operational
wt-advisor statistics import export.csv --provider statshark --purpose operational
wt-advisor profile reconcile --profile acceptance --dry-run --json
wt-advisor acceptance --milestone 1
wt-advisor acceptance --milestone 2 --json
wt-advisor-mcp
```

## MCP tunnel

After configuring an external `war-thunder-advisor` tunnel profile to launch
`wt-advisor-mcp`, start it from any PowerShell location with:

```powershell
.\scripts\start-mcp-tunnel.ps1
```

The launcher changes to the repository root before starting the profile, so the default
`wt-advisor.sqlite` location remains predictable. It expects the tunnel client at
`D:\ChatGPT_MCP_Tunnel\tunnel-client.exe`; override that with `WTA_TUNNEL_CLIENT` or
`-TunnelClientPath`. Use `-Profile` for a differently named tunnel profile. Credentials belong
in the tunnel client's runtime environment, never in this repository. `start-mcp-tunnel.bat`
provides the same launcher for double-click or Command Prompt use.

Battle ratings in structured input/output use integer tenths (`27` means BR 2.7). The human CLI
formats them as display values where appropriate. Set `WT_ADVISOR_DB` to select the durable SQLite
database; the default is `wt-advisor.sqlite` in the working directory.
Live imports are bounded and HTTPS-only. The community API produces separate metadata,
capability, and required-predecessor snapshots; omissions remain unknown. Availability resolution
and identity aliases use validated curated imports, and performance statistics use validated
manual JSON/CSV imports. Components become active only when their purpose, compatibility key, and
identity coverage match; incompatible evidence is reported rather than silently reused.

See [the architecture](docs/architecture.md), [data-source findings](docs/data-sources.md), and
[the Milestone 1 acceptance result](docs/acceptance.md). The property-based Milestone 2 gate is
documented in [acceptance-m2.md](docs/acceptance-m2.md).

## Local dashboard

Run `wt-advisor dashboard` to serve the bundled dashboard at `http://127.0.0.1:8765`.
The dashboard is packaged with the Python distribution; it does not need Vite at runtime.
For frontend development and rebuilding its assets, use Node.js 22.22.2 (the supported range is
`^22.22.2`).
It is loopback-only and is never exposed through the MCP tunnel. Saved presets are not garage
ownership; hypothetical analysis never changes vehicle state. Refresh after a revision conflict.
