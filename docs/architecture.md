# Architecture

The application follows one direction of dependency:

```text
providers / fixtures -> immutable snapshots -> domain services -> CLI and MCP
                                      \-> pure rules -/
```

## Evidence and storage

SQLite is managed by Alembic through revision `0004`. Imported payloads create immutable dataset
snapshots and raw artifacts. Metadata, capabilities, resolved availability, identity aliases,
research graphs, and statistics are independently versioned components. User profiles and vehicle
statuses are stored separately and revisioned on mutation; alias reconciliation has dry-run,
optimistic-revision, supersession, and audit records.

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
data. Research graphs have explicit all/any prerequisite groups and compatibility metadata.
Progression reports `progression_unavailable` when no compatible graph exists and never borrows an
unrelated tree. `get_data_status` reports availability, freshness, coverage, gaps, and the selected
snapshot for every component.

## Providers

- `FixtureProvider` supplies network-free acceptance evidence.
- `WarThunderVehiclesApiProvider` is a bounded, cached, retrying HTTP adapter. Detail records map
  only explicit scouting, artillery, smoke, vertical-stabilizer, and predecessor facts; omission is
  not false.
- Strict bounded JSON/YAML importers handle capability, availability, and research-graph evidence.
- JSON and CSV statistics inspection validates identity match rate, duplicates, scope, sample
  period, and metric coverage before import.
- YAML overrides require a revision, vehicle ID, field, typed value, reason, reference, and date.

See [data-sources.md](data-sources.md) for licensing, freshness, and StatShark findings.

## Analysis and generation

Rules are pure functions with frozen `m1-baseline-v1` and explicit
`m2-capability-aware-v1` configurations. M2 capability rules use present, verified-absent, unknown,
and conflicted states. Specialist/SPAA redundancy is diagnostic and zero-weight; it cannot affect
score, readiness, or ordering. The ruleset hash and every selected snapshot are included in the
evidence context.

Candidate generation enumerates unordered combinations under a 500,000-combination guard, groups them by resulting BR, removes dominated score vectors, and returns deterministic per-BR frontiers. Cross-BR recommendation selects the highest frontier passing readiness gates; it never compares raw lineup scores across BRs.

`lower_br_alternative` is an optional, readiness-passing frontier rather than a fallback for
the least-deficient candidate. When a request supplies `target_br`, it can only refer to a
frontier strictly below that target, and therefore is `null` when the request's scoped search
contains only the target BR. Without `target_br`, it must be strictly below the recommended BR,
or below the highest evaluated BR when no frontier passes readiness. The service never expands a
targeted search merely to manufacture this alternative; failed frontiers remain available in
`groups` with their diagnostics.

One-step progression returns the best expanded-pool and forced-include lineups plus adoption,
frontier, role, readiness, research-cost, and cross-BR facts. Hypothetical ownership is request-local
and does not mutate profile state.

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
