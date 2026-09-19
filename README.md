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
