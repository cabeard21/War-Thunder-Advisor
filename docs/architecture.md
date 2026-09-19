# Architecture

The application follows one direction of dependency:

```text
providers / fixtures -> immutable snapshots -> domain services -> CLI and MCP
                                      \-> pure rules -/
```

## Evidence and storage

SQLite is managed by Alembic from revision `0002`. Imported payloads create immutable dataset snapshots and raw artifacts. Canonical vehicle IDs, provider aliases, versioned metadata, mode-specific BR observations, capabilities, research edges, statistics, and overrides retain their snapshot references. User profiles and vehicle statuses are stored separately and revisioned on mutation.

Re-importing identical content is idempotent. The repository verifies the retained raw artifact's
size and checksum at its persistence boundary. Overrides resolve on top of imported facts and
retain both imported and replacement provenance; they never rewrite historical observations. BR
is represented on the ordered War Thunder ladder using integer tenths at the boundary.

Snapshot discovery and analytical activation are separate. Status reports the independently
newest stored vehicle and statistics snapshots even when they cannot be combined. Operational
analysis selects the newest operational vehicle snapshot first, then searches newest-first for
statistics with the same purpose/bundle scope whose canonical IDs are contained by that vehicle
snapshot. It never selects an older vehicle snapshot merely to make statistics fit. When no
statistics qualify, the active bundle contains vehicle evidence only, emits
`statistics_unavailable`, and keeps the statistical rule visibly unknown while using the disclosed
neutral composite contribution.

Every snapshot has an explicit `purpose` (`operational` or `acceptance`) and optional
`compatibility_key`. The frozen acceptance vehicle and synthetic statistics snapshots share an
acceptance-only key, so they bind to each other and cannot become evidence for live operational
data. Research edges are loaded only from the active vehicle snapshot; progression reports
`progression_unavailable` when that snapshot has no graph.

## Providers

- `FixtureProvider` supplies network-free acceptance evidence.
- `WarThunderVehiclesApiProvider` is a bounded, cached, retrying HTTP adapter independent of domain logic.
- JSON and strict-column CSV statistics importers normalize exported observations.
- YAML overrides require a revision, vehicle ID, field, typed value, reason, reference, and date.

See [data-sources.md](data-sources.md) for licensing, freshness, and StatShark findings.

## Analysis and generation

Rules are pure functions configured by `config/defaults.toml`. Each returns a visible score/status, effective composite contribution, evidence, warnings, and explanation. The ruleset hash and every data snapshot are included in the evidence context.

Candidate generation enumerates unordered combinations under a 500,000-combination guard, groups them by resulting BR, removes dominated score vectors, and returns deterministic per-BR frontiers. Cross-BR recommendation selects the highest frontier passing readiness gates; it never compares raw lineup scores across BRs.

One-step progression returns the best expanded-pool lineup and a lineup forced to include the unlock. Hypothetical ownership is request-local and does not mutate profile state.

## Interfaces

The Typer CLI and MCP 2.x stdio server are thin adapters over `AdvisorService`. MCP exposes:

- `get_data_status`
- `list_vehicles`
- `get_vehicle`
- `get_vehicle_statistics`
- `get_user_progress`
- `set_user_vehicle_status`
- `analyze_lineup`
- `compare_lineups`
- `generate_lineups`
- `suggest_lineup_additions`
- `evaluate_next_unlocks`

All tools except the idempotent profile mutation are annotated read-only. Structured responses use the same Pydantic DTOs as the CLI, allowing deterministic `analysis_id` parity tests.

## Local security boundary

The MCP server uses stdio and the application has no public HTTP listener or authentication surface. Inputs are schema-validated, SQL is parameterized through SQLAlchemy, import sizes and HTTP timeouts are bounded, and secrets are neither required nor stored. Live imports are explicit operations; normal tests and acceptance runs are network-free.
