# War Thunder Lineup Advisor

An evidence-first, deterministic local advisor for War Thunder lineups. Milestone 1 covers
USA Ground Realistic Battles through approximately BR 4.0 and exposes the same application
services through a CLI and a local stdio MCP server.

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
wt-advisor acceptance
wt-advisor-mcp
```

Battle ratings in structured input/output use integer tenths (`27` means BR 2.7). The human CLI
formats them as display values where appropriate. Set `WT_ADVISOR_DB` to select the durable SQLite
database; the default is `wt-advisor.sqlite` in the working directory.
Live imports are bounded, HTTPS-only, and become active on the next service load after their
vehicle identities are checked against the active statistics snapshot. If a refreshed vehicle
snapshot has no research edges, lineup analysis remains available but progression evaluation is
reported as unavailable rather than reusing a stale tree.

See [the architecture](docs/architecture.md), [data-source findings](docs/data-sources.md), and
[the generated acceptance result](docs/acceptance.md).
