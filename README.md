# War Thunder Lineup Advisor

An evidence-first, deterministic local advisor for War Thunder lineups. Milestone 2 adds
independently versioned capabilities, availability, identity aliases, research prerequisites,
and statistics while preserving the frozen Milestone 1 behavior. The initial operational scope
is USA Ground Realistic Battles; community detail refresh prioritizes the current profile.

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

The acceptance fixture is deliberately frozen and network-free. Live provider refreshes create
new immutable snapshots but are not required to run tests.

For the ARM64 production image, private LAN/Tailscale Compose configuration, persistent
SQLite storage, and upgrade steps, see [deployment.md](docs/deployment.md).

## Usage

```powershell
wt-advisor data status --json
wt-advisor data import --source wt-api --json
wt-advisor data refresh-community --json
wt-advisor data reprocess-community --json
wt-advisor vehicles list --max-br 40 --json
wt-advisor lineup generate --profile acceptance --top 5 --json
wt-advisor progress next --profile acceptance --json
wt-advisor statistics inspect export.csv --provider statshark --purpose operational
wt-advisor statistics import export.csv --provider statshark --purpose operational
wt-advisor profile reconcile --profile acceptance --dry-run --json
wt-advisor acceptance --milestone 1
wt-advisor acceptance --milestone 2 --json
wt-advisor-mcp
wt-advisor advisor show --json
wt-advisor advisor refresh --json
```

## MCP tunnel

After configuring an external `wt-advisor` tunnel profile to launch
`wt-advisor-mcp`, start it from any PowerShell location with:

```powershell
.\scripts\start-mcp-tunnel.ps1
```

The launcher explicitly selects the operational
`wt-advisor-live-acceptance.sqlite` database for both MCP and dashboard processes. Pass
`-DatabasePath` with an existing absolute path to select another database. It also starts the bundled dashboard at
`http://127.0.0.1:8765` for the lifetime of the tunnel; pass `-DashboardPort` to use another
loopback port. The launcher expects `.venv\Scripts\wt-advisor.exe` and the tunnel client at
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

`data refresh-community`, the dashboard's **Refresh community evidence** button, and the
`refresh_community_evidence` MCP tool use the same bounded refresh operation. The tool is a
network-backed mutation and should be invoked only on an authorized refresh request. Valid
vehicle and capability evidence is published atomically; failed refreshes retain the previous
bundle. Individual invalid community-statistics rows are quarantined without blocking usable
rows. Qualifying WT Data Project Realistic Battles ground-vehicle rows can score as
**Community RB ground-vehicle proxy** evidence. Their actual `realistic_all_contexts` scope
remains visible, and their sample-adjusted deviation from neutral is halved. Verified Ground RB
evidence takes precedence; missing or excluded statistics remain unknown. CLI, dashboard, and MCP
evaluation responses report eligibility, proxy use, source provenance, and exclusion reasons.

`data reprocess-community` instead reinterprets the latest retained joined CSV against the
active catalog without contacting the vehicle API. Back up the selected SQLite database first;
the command publishes a revisioned statistics snapshot and is idempotent for the same source,
catalog, and alias mapping. Restart long-running dashboard or MCP processes to load it.

See [the architecture](docs/architecture.md), [data-source findings](docs/data-sources.md), and
[the Milestone 1 acceptance result](docs/acceptance.md). The property-based Milestone 2 gate is
documented in [acceptance-m2.md](docs/acceptance-m2.md).

## Local dashboard

Run `wt-advisor dashboard` to serve the bundled dashboard at `http://127.0.0.1:8765`.
The dashboard loads garage/progression independently of the advisor calculation. On first open,
or after garage, saved context, selected preset, evidence, or recommendation-policy changes, it
shows a visibly stale last answer while recomputing in a separate request. A failed refresh
retains the old answer and offers retry. The recommendation and research reasons are
deterministic; MCP and ChatGPT are not required.

Profile-specific preferred roles and duplicate-role avoidance can be saved in dashboard context.
Readiness and battle-rating frontier selection stay authoritative: preferences only order the
readiness-passing lineups at the already-selected BR, and objective lineup scores are never
modified. `duplicate_role_penalty` is a 0–10 **avoidance strength**, not a number of score
points: 0 disables the term and 10 applies the strongest available avoidance. Preferences are
bounded — they can overcome at most 3.0 points of objective evaluation, so a clearly stronger
lineup still wins. The card explains the recommendation in plain language and keeps every reason
code, metric and provenance field in its details view. A preference that no ready lineup at the
selected BR can satisfy is reported as a tradeoff, together with the ranked research target that
would change it. See [docs/architecture.md](docs/architecture.md) for the full semantics.
The dashboard is packaged with the Python distribution; it does not need Vite at runtime.
For frontend development and rebuilding its assets, use Node.js 22.22.2 (the supported range is
`^22.22.2`).
Local startup is loopback-only by default; private container access requires the explicit
configuration in [deployment.md](docs/deployment.md). The dashboard is never exposed through
the MCP tunnel. Saved presets are not garage
ownership; hypothetical analysis never changes vehicle state. Refresh after a revision conflict.
The community refresh button reports source failures briefly and does not alter stored evaluations.
