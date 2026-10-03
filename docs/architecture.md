# Architecture

The application follows one direction of dependency:

```text
providers / fixtures -> immutable snapshots -> domain services -> advisor snapshot
                                                         -> dashboard / CLI / optional MCP
                                      \-> pure rules -/
```

## Evidence and storage

SQLite is managed by Alembic through revision `0008`. Imported payloads create immutable dataset
snapshots and raw artifacts. Metadata, capabilities, resolved availability, identity aliases,
research graphs, and statistics are independently versioned components. User profiles and vehicle
statuses are stored separately and revisioned on mutation; alias reconciliation has dry-run,
optimistic-revision, supersession, and audit records. Marking a vehicle `owned` also promotes the
direct successors whose research prerequisites that ownership satisfies from `locked`/`unknown` to
`available_to_research`, in the same transaction and under one revision bump; the cascade is never
transitive and never demotes. A cascade entry is advisory: the repository re-checks inside the
write transaction that the target is known and still unrecorded. Cascade-producing service writes
also guard the status revision used for derivation, rejecting concurrent changes before any
promotion is persisted; explicitly supplied revision guards retain their existing behavior.

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

Capability status retains the legacy vehicle-record coverage count and separately
reports resolved field coverage for every capability used by scoring. It shows
catalog and owned/immediate-research scopes, per-field present/verified-absent/
unknown/conflicted counts, and bounded missing vehicle/capability pairs. The
legacy `vehicle.capabilities` set describes embedded vehicle metadata; clients
must use `provenance.capability_resolutions` for current M2 evidence. The active
bundle includes all selected compatible capability snapshot IDs. The newest
community-API snapshot and compatible curated revisions resolve by sourced
claim and source type; the newest verification date of the same claim
supersedes it, while contradictory different claims remain a conflict. Old
stored evaluations are not rewritten.

## Providers

- `FixtureProvider` supplies network-free acceptance evidence.
- `WarThunderVehiclesApiProvider` is a bounded, cached, retrying HTTP adapter. Detail records map
  only explicit scouting, artillery, smoke, vertical-stabilizer, and predecessor facts; omission is
  not false.
- Strict bounded JSON/YAML importers handle capability, availability, and research-graph evidence;
  curated capability imports retain per-observation verification date and source revision.
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

## Deterministic advisor snapshot

`AdvisorService.compute_advisor_snapshot` captures garage, saved context, selected preset, and
evidence identity together, then composes the existing lineup evaluator into one structured
answer. The highest readiness-passing BR determines the band; preferences only order candidates
within it. Research priorities use one-step forced-include lineups, report recovery targets when
no current lineup passes readiness, and never compare raw scores across BRs. Reasons are
deterministic codes with underlying analyses attached.

### Bounded preference ranking

Objective scoring is unchanged. Preferences contribute a separate, separately bounded term:

```text
recommendation_score  = objective_score + preference_adjustment
preference_adjustment = ROLE_CAP * role_satisfaction
                      - DUPLICATE_CAP * duplicate_strength * duplicate_load
```

`ROLE_CAP` (1.5) bounds the preferred-role term alone and `DUPLICATE_CAP` (1.5) the duplicate-role
term alone. `PREFERENCE_CAP` is the envelope on a single candidate's adjustment; the caps are not
summed into it. The binding invariant is `MAX_PREFERENCE_SWING` (`ROLE_CAP + DUPLICATE_CAP` = 3.0):
the largest objective deficit any preference can overcome, since reordering depends on the
*difference* between two candidates' adjustments rather than on either one alone.

Those numbers come from the live USA ground RB garage, measured over the population actually
ranked — the full readiness-passing set at each BR. Adjacent objective gaps inside such a pool have
a median of 0.004, p90 of 1.41 and p95 of 2.50, while the smallest per-BR top-to-bottom span is
7.32. A 3.0 swing therefore crosses roughly the lowest 98% of adjacent gaps while leaving a
bottom-of-pool lineup unable to reach the top of any BR. Measured over that garage the largest
objective score any winner actually gave up was 0.80, no Pareto-dominated candidate became primary
at any BR, and the winning lineup was identical at swings of 2.0, 3.0 and 4.0.

`duplicate_role_penalty` keeps its stored `int` 0–10 range and now means duplicate-role **avoidance
strength**: 0 disables the term and 10 drives it to `DUPLICATE_CAP`. It is not a cap on preference
influence in general and has no effect on the preferred-role term.

A duplicate is a repeat of a vehicle's **canonical primary role**, one per vehicle class, so a tank
destroyer counts once rather than twice through both `tank_destroyer` and `sniper`. SPAA is exempt,
because `anti_air_rule` and the diagnostic specialist-redundancy rule already govern it, and backup
depth is untouched since it is already a weighted component of the objective score. Preferred-role
*satisfaction* still uses the full capability-aware role set, resolved through `rules.resolve_roles`
so the active ruleset decides. Duplicate counts saturate at 3, the observed maximum.

Readiness and BR-frontier selection remain authoritative and are evaluated first. A preference can
never promote an unready lineup, never change which battle rating is recommended, and never alter
`objective_score`; it only orders candidates inside the band the frontier already chose.

Preferences rank the **full readiness-passing set at the selected BR**, not the Pareto frontier.
Dominance compares only the objective rule-score vector, so it is blind to readiness and to role
composition and can prune a preference-satisfying lineup before preferences are ever applied — at
the live BR 3.0 case it cuts seven candidates to one. Widening re-admits only candidates that
already passed readiness and every hard constraint, so no hard invariant is affected; the
preference cap is what keeps materially inferior candidates from winning.

### Explanations

`services/explanations.py` maps reason codes and measured values to player-facing sentences,
deterministically and with no model. Structured codes and provenance remain the source of truth and
are unchanged in the payload. Explanations separate **strengths**, **tradeoffs** and **warnings**:
an unmet preference is always a tradeoff, never a strength and never a readiness blocker. A
preference that lost to a stronger lineup is worded differently from one that no ready lineup at
the selected BR can satisfy, and the latter carries a pointer to the ranked research target that
would change it. Alternatives report their differences, and deliberately omit any score delta
across battle ratings.

`role_redundancy` is zero-weight, diagnostic and SPAA-only, and its `GOOD` status is vacuous below
two SPAA, so it is no longer swept into the lineup's strengths. Reason codes are now mapped from
rules explicitly, which also stops a newly added rule from silently becoming a user-facing strength.

Completed answers are immutable `stored_evaluations` of kind `advisor_snapshot`. The read path
selects the latest answer and compares its captured source fingerprint to current garage,
context/preferences, selected preset, ruleset, evidence, and recommendation policy. Ranking policy
lives in code rather than the evidence ruleset, so `RECOMMENDATION_POLICY_VERSION` is fingerprinted
separately: any change that could alter recommendation ordering or its interpretation bumps it and
stales every snapshot stored under the previous policy, reported as `policy_changed`. A stale result
remains visible;
refresh is a separate bounded single-worker operation and never blocks garage/progression reads.
The dashboard starts a refresh when an answer is missing or stale, polls while computing, and
retains the previous answer if recomputation fails. The local worker is process-scoped; restarting
the application recovers the persisted answer and can start a new refresh.

## Interfaces

The dashboard, Typer CLI, and MCP 2.x stdio server consume `AdvisorService` directly. The
dashboard does not need MCP or an LLM. `advisor show`/`advisor refresh`, the local HTTP advisor
read/refresh endpoints, and MCP `get_advisor_snapshot` share the persisted result. Existing MCP
tools remain for compatibility; low-level orchestration tools are deprecation candidates only
after downstream clients have migrated. MCP also exposes:

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

## Test coverage status

`pyproject.toml` enforces `fail_under = 80` on the combined coverage report. CI additionally
runs `scripts/check_coverage.py`, requiring line and branch coverage independently to reach
80%. Python tests, lint/type checks, frontend unit/type/build checks, browser tests, and an
ARM64 container smoke test gate publication of pinned GHCR images. See
[deployment.md](deployment.md) for the runtime contract and private Compose example.

## Local security boundary

The MCP server uses stdio. The optional dashboard serves its API and bundled UI on
the configured local address (`127.0.0.1` by default) without authentication; it is
not intended for public exposure. Inputs are schema-validated, SQL is parameterized
through SQLAlchemy, import sizes and HTTP timeouts are bounded, and secrets are neither
required nor stored. Live imports are explicit operations; normal tests and acceptance
runs are network-free. Private containers explicitly opt into a non-loopback bind with an
exact allowed-host list, retaining same-origin write protection. This is a LAN/Tailscale
deployment boundary, not public authentication.
