# War Thunder Lineup Advisor — Milestone 2

## Operational Data & Advisor Quality

## Goal

Milestone 1 established a deterministic, evidence-first lineup engine with SQLite persistence, immutable snapshots, per-BR frontiers, readiness gates, CLI/MCP parity, hypothetical ownership, and live operational vehicle/BR data.

Milestone 2 should make the advisor **meaningfully useful for real War Thunder progression**, not merely mechanically correct.

The major deficiencies exposed by Milestone 1 live acceptance are:

1. Operational vehicles currently have incomplete factual capabilities.
2. Availability/researchability classifications from the community vehicle provider are not sufficient by themselves.
3. The operational vehicle snapshot has no research graph, so progression advice is unavailable.
4. There are no operational performance statistics, so statistical strength remains unknown.
5. Some lineup rules exhibit predictable artifacts because of the missing evidence, especially scouting and redundant-role behavior.
6. The current system has software acceptance but not systematic **advisor-quality acceptance**.

Milestone 2 should address those in that order.

Do not begin by retuning weights to compensate for missing data.

---

# Milestone 1 Baseline — Must Remain Passing

Preserve all Milestone 1 guarantees.

Current live operational evidence:

```text
vehicle snapshot:
wt-vehicles-api-20260919T030556Z

provider:
war_thunder_vehicles_community_api

purpose:
operational

statistics:
unavailable

research graph:
unavailable

schema revision:
0002

override revision:
m1-no-curated-corrections

ruleset hash:
750cb92729e6cb63ade14083148fc256aacd0ba2dab7acc42f027fdd1611d7a8
```

Current live USA / Ground RB / five-slot recommendation:

```text
us_m13_mgmc
us_m22
us_m3_gmc
us_m3_stuart
us_m3a1_stuart

BR: 2.3
score: 69.46666666666667
readiness: passed
```

Generation analysis ID:

```text
3a4f542d938ab4015c375c1d67dbd3f57cd7ec622730cedca55be8462639c227
```

Recommended-lineup analysis ID:

```text
45959829acf87d115a50e4ede6843bd0e17348b02c4fa0771a08f393f59071dd
```

The following Milestone 1 invariants must remain true:

* CLI and MCP use the same application services and DTOs.
* Same request + evidence + rules + user revision produces the same `analysis_id`.
* Cross-BR construction scores are never directly ranked.
* Missing statistics remain visibly unknown.
* Neutral statistical fallback is internal arithmetic only and never presented as observed average performance.
* Hypothetical ownership never mutates persisted user state.
* Research edges are never borrowed from an incompatible snapshot.
* Provider observations remain immutable.
* Overrides/enrichment never rewrite raw imported evidence.
* Canonical vehicle identity never depends on display name.
* Frozen acceptance remains reproducible and network-free.

---

# Milestone 2 Scope

Primary initial scope remains:

```text
Nation: USA
Mode: Ground Realistic
BR: approximately 1.0–4.0
Crew slots: 5
```

However, every schema and service should remain generic enough for later expansion to all nations and BRs.

Milestone 2 consists of five workstreams:

1. Capability enrichment
2. Availability and researchability resolution
3. Operational research graph
4. Operational performance statistics
5. Advisor-quality evaluation and targeted rule refinement

Implement them as gated vertical slices.

---

# 1. Capability Enrichment

## Problem

The operational community API currently provides useful metadata and Ground RB BRs, but Milestone 1 live acceptance returned:

```text
capabilities: []
```

for all inspected vehicles.

As a result:

* scouting may incorrectly appear absent;
* stabilizers are unknown;
* smoke is unknown;
* artillery/support utility is unknown;
* high-caliber HE is unknown;
* role inference is weakened;
* uptier-resilience proxies lack factual inputs.

For example, an M24 should be able to carry factual capability evidence such as scouting, smoke, and a vertical stabilizer when supported by an authoritative source.

## Architecture

Introduce a first-class **capability enrichment provider** rather than stuffing manually guessed capability flags into rules.

Possible model:

```text
CapabilityObservation
- vehicle_id
- capability
- value
- source_provider
- source_snapshot_id
- source_reference
- confidence/source_type
```

Capabilities remain facts, not inferred roles.

Possible capabilities include:

```text
SCOUTING
ARTILLERY
VERTICAL_STABILIZER
SMOKE_GRENADE
SMOKE_SCREEN
AIRSTRIKE
RADAR
IRST
SAM
ATGM
AUTOCANNON_AA
HIGH_CALIBER_HE
RECON_DRONE
THERMALS
LASER_RANGEFINDER
```

Do not populate every possible capability in this milestone.

Prioritize capabilities that actually affect current rules.

Initial required subset:

```text
SCOUTING
VERTICAL_STABILIZER
SMOKE
ARTILLERY
HIGH_CALIBER_HE
```

SPAA identity may continue to derive from factual vehicle class where appropriate.

## Source Investigation

Investigate the official/current War Thunder Wiki as a possible capability-enrichment source.

Do not assume bulk scraping is appropriate.

Determine:

* whether useful capability information is available through stable structured markup/data;
* terms/usage considerations;
* whether automated retrieval is technically robust;
* whether targeted enrichment is preferable to broad crawling.

If live automated acquisition cannot be cleanly supported:

* implement a validated enrichment JSON/YAML importer;
* create a small provenance-stamped USA 1.0–4.0 operational enrichment dataset;
* document the source/reference for every curated fact.

Do not fabricate capability values.

## Resolution

Resolved vehicle capabilities should combine compatible factual observations from multiple providers.

Do not make capability enrichment part of the raw community API vehicle snapshot.

Instead use a composite evidence bundle such as:

```text
vehicle metadata snapshot
+
capability snapshot
+
research graph snapshot
+
statistics snapshot
```

Each component remains independently versioned.

`get_data_status` should expose each active component.

## Missing Evidence

Distinguish:

```text
capability = false
```

from:

```text
capability = unknown/not observed
```

This is important.

The scouting rule must no longer treat lack of enrichment as verified absence.

If capability evidence is incomplete:

```text
visible scouting score: null/unknown
```

or another explicitly unknown representation is preferred over:

```text
0 / critical
```

unless the system has sufficient evidence that no vehicle actually has scouting.

Update other capability-driven rules similarly.

---

# 2. Availability / Researchability Resolution

## Problem

Milestone 1 demonstrated that upstream provider availability flags are not sufficient to determine normal research-tree progression.

Examples identified during live inspection include provider entries that appear as `research_tree` but may actually be:

* gift/special vehicles;
* alternate/internal variants;
* event variants;
* legacy variants;
* hidden duplicates.

The advisor must never tell the user to research a vehicle that cannot actually be normally researched.

## Model

Separate concepts that are currently too easy to conflate.

Suggested fields:

```text
acquisition_type
researchability
visibility
tree_membership
```

For example:

```text
acquisition_type:
- research
- premium
- pack
- event
- gift
- squadron
- marketplace
- reserve
- unknown

researchability:
- normal_tree
- foldered_tree
- directly_researchable
- non_researchable
- unknown
```

Do not force every upstream vehicle into a normal-tree/premium/event three-way classification.

## Resolution Layer

Resolve provider observation through a curated/secondary source layer.

Keep:

```text
provider says X
resolved operational value Y
reason/reference Z
```

Do not mutate historical provider observations.

## Alias Cleanup

Milestone 1 exposed stale profile alias state:

```text
us_m2a4_1st = owned
us_m2a4_first_tank_div = locked
```

Introduce a safe alias-migration/reconciliation mechanism.

Requirements:

* never delete history silently;
* map deprecated canonical IDs to current canonical IDs;
* detect collisions;
* define deterministic conflict resolution;
* expose migration/reconciliation evidence;
* preserve a migration audit record.

Example conflict:

```text
old alias = owned
canonical current ID = locked
```

Do not silently choose one.

Define policy explicitly.

For a safe default, prefer the more-progressed user status only when both IDs are confirmed aliases for the same actual vehicle and record the merge.

Add a dry-run reconciliation command before mutation.

Possible CLI:

```text
wt-advisor profile reconcile --profile acceptance --dry-run --json
```

---

# 3. Operational Research Graph

## Problem

Milestone 1 operational progression currently returns:

```text
progression_unavailable
active vehicle snapshot has no research graph evidence
```

This behavior is correct, but Milestone 2 should supply compatible research-graph evidence.

## Design

Research graph evidence should be its own immutable dataset:

```text
ResearchGraphSnapshot
- snapshot_id
- provider
- source_revision
- retrieved_at
- purpose
- checksum
```

Edges:

```text
ResearchEdge
- nation
- mode/domain
- parent_vehicle_id
- child_vehicle_id
- edge_type
- snapshot_id
```

Possible edge types:

```text
normal
folder
branch_unlock
required_predecessor
```

Do not assume the tree is always a simple parent-child chain.

## Source Investigation

Investigate authoritative/current sources for tech-tree relationships.

Potential options may include:

* public game-file/datamine-derived structures;
* official Wiki research-order/tree data;
* another stable documented source.

Do not copy bundled data from third-party projects unless licensing explicitly permits it.

If automated acquisition cannot be established cleanly:

* ship a validated research-graph import interface;
* create a provenance-stamped USA Ground RB 1.0–4.0 graph sufficient for operational acceptance;
* keep automatic acquisition as a documented follow-up.

## Snapshot Compatibility

A research graph may update on a different cadence than BR metadata.

Do not require identical snapshot IDs.

Instead define compatibility explicitly using:

* provider/source revision compatibility;
* vehicle identity coverage;
* collection timestamps;
* purpose;
* optional compatibility key.

`get_data_status` should report:

```text
research_graph_status:
available / stale / incompatible / unavailable
```

and explain why.

## Progression Logic

Once available, `evaluate_next_unlocks` should become operational.

For each directly reachable unlock return:

```text
vehicle
research cost
current status
research prerequisites
best current lineup
best expanded-pool lineup
best forced-include lineup
resulting BR
readiness
component deltas
cross-BR comparability
```

Keep the distinction between:

```text
the vehicle can be researched next
```

and:

```text
the vehicle should immediately enter the active lineup
```

A research unlock can be valuable even if it is not immediately adopted.

---

# 4. Operational Performance Statistics

## Goal

Make statistical strength useful while preserving Milestone 1's evidence discipline.

## StatShark

Re-check whether StatShark now provides any:

* documented API;
* permitted export;
* user-downloadable CSV/JSON;
* stable supported acquisition mechanism.

Do not:

* bypass anti-bot controls;
* scrape brittle rendered HTML;
* reverse-engineer private endpoints merely because they exist.

If no permitted automation exists, retain manual import as the supported path.

That is acceptable for Milestone 2.

## Import UX

Improve the existing statistics import workflow so a manually obtained export can be operationally useful.

Possible CLI:

```text
wt-advisor statistics inspect file.csv
wt-advisor statistics import file.csv --provider statshark --purpose operational
```

`inspect` should validate without mutation and show:

* recognized columns;
* vehicle-match rate;
* unknown source IDs;
* duplicate observations;
* sample period;
* scope;
* metric coverage;
* prospective snapshot ID.

## Canonical Identity

Statistics must map through provider aliases to canonical vehicle IDs.

Do not match by display name unless the importer explicitly enters an unresolved/manual-review state.

Unknown identities must be surfaced.

## Required Metrics

Support at least, where supplied:

```text
win_rate
kills
deaths
kd
battles
kills_per_battle
```

Retain raw counts whenever possible.

Prefer deriving ratios from counts when both are available.

## Scope

Every observation must explicitly define scope.

Examples:

```text
ground_realistic_ground_vehicle
realistic_ground_vehicle
all_realistic_vehicle
unknown_realistic_scope
```

Never silently treat mixed/unknown scopes as Ground RB.

Aircraft/CAS statistics remain out of scope for Milestone 2 unless a compatible ground-battle-specific source is explicitly available.

## Confidence

Preserve the Milestone 1 bounded statistical design:

* peer-relative;
* confidence adjusted;
* sample-size aware;
* visible raw/adjusted values;
* maximum 10% composite influence unless a new ruleset explicitly changes it.

Do not increase statistics weight yet.

## Temporal Compatibility

Performance statistics may describe an older balance period than the current BR snapshot.

Expose:

```text
statistics_sample_end
vehicle_snapshot_date
age_gap
compatibility/freshness warning
```

Do not require same-day evidence.

Do surface potentially meaningful staleness.

---

# 5. Advisor-Quality Acceptance Framework

## Goal

Milestone 1 proves software correctness.

Milestone 2 must begin proving recommendation usefulness.

Do not rely on one opaque assertion such as:

```text
recommended lineup looks good
```

Build deterministic advisor-quality scenarios.

## Golden Scenario Format

Create scenario files containing:

```text
profile state
available vehicle pool
target/current progression state
expected properties
known alternatives
evidence notes
```

The expected result should usually specify **properties**, not an exact lineup ID, unless exact choice is genuinely required.

Example:

```text
USA Ground RB
5 crews
Rank I owned
M3 Lee researching
```

Expected properties might include:

```text
exactly one useful SPAA is sufficient unless evidence supports a second;
do not claim missing scouting when capability evidence is unknown;
M13/M15 may remain tied absent performance evidence;
do not recommend inaccessible premium/event vehicles;
M3 Lee hypothetical analysis must remain cross-BR aware.
```

## Initial Golden Cases

Create at least these scenarios.

### A. Rank I USA current state

Current live acceptance state.

Purpose:

* role diversity;
* SPAA redundancy;
* capability-known vs capability-unknown;
* M13/M15 tie behavior.

### B. M3 Lee newly owned

Purpose:

* BR 2.7 transition;
* depth/cohesion readiness;
* forced-include vs expanded-pool distinction;
* whether staying at lower BR remains a useful alternative.

### C. M5A1 + M3 Lee + M16 owned

Purpose:

* first coherent 2.7 lineup;
* whether 2.7 becomes a stronger progression frontier.

### D. One isolated higher-BR vehicle

Example:

```text
one 3.0 vehicle
mostly 2.0–2.3 backups
```

Purpose:

* readiness gates should resist premature BR escalation.

### E. Mature 3.3 lineup

Purpose:

* useful combination of medium/TD/SPAA/mobility;
* ensure duplicate utility roles do not dominate purely because they satisfy categories.

### F. Mature 3.7/4.0 lineup

Purpose:

* scouting-capable M24 evidence;
* stabilizer capability;
* Sherman/general-medium roles;
* role overlap should be evidence, not an automatic penalty.

---

# 6. Redundant-Role and Duplicate-SPAA Behavior

## Problem

Milestone 1 produced a BR 1.7 candidate containing both:

```text
M13
M15
```

and rewarded it strongly because AA and role coverage were both satisfied.

This is mathematically consistent but may not represent a useful five-slot Ground RB lineup.

Do not simply add a hard penalty for duplicate SPAA.

Instead model **marginal lineup value**.

Possible approach:

```text
first meaningful SPAA:
high role value

second near-equivalent SPAA:
small redundancy value unless:
- substantial statistical advantage;
- meaningfully different capability;
- lineup lacks competitive alternatives;
- user explicitly requests heavy AA coverage
```

Represent this through rule evidence rather than hidden candidate pruning.

Possible new rule:

```text
role_redundancy
```

or extend role coverage with marginal utility.

Do not penalize duplicate mediums/tanks automatically.

For example, multiple Shermans may be entirely appropriate because they are useful general combat backups.

Redundancy should depend on role specificity and substitute similarity.

---

# 7. Scouting Rule Semantics

Milestone 1 treated no recorded scouting capability as:

```text
score = 0
status = critical
```

That is inappropriate when capability evidence itself is missing.

Implement three states:

```text
present
verified_absent
unknown
```

Possible scoring:

```text
present:
visible score 100

verified_absent:
visible score 0 or configured value

unknown:
visible score null
effective neutral 50
warning capability_evidence_incomplete
```

Do not make scouting absence a readiness failure unless the ruleset explicitly calls for it.

The same pattern should be reusable for other evidence-dependent rules.

---

# 8. Uptier Resilience Improvements

Current uptier resilience is explicitly a proxy.

Keep that label.

Enhance it only where factual capabilities now permit stronger evidence.

Possible useful inputs:

```text
scouting
mobility class
stabilizer
weapon/firepower role
smoke
anti-armor specialization
SPAA utility
```

Do not build a fake armor simulator or penetration model in this milestone.

If introducing new heuristics, expose every component.

---

# 9. Research-Value Analysis

Once the research graph works, improve `evaluate_next_unlocks`.

The important question is not merely:

```text
What can I research?
```

but:

```text
Which reachable unlock improves my practical lineup/progression options?
```

Add transparent values such as:

```text
adopted_immediately
opens_new_ready_frontier
improves_existing_frontier
fills_missing_role
provides_stronger_backup
research_cost
```

Do NOT collapse this into one universal "research priority score" yet.

Return structured facts for LLM reasoning.

For example:

```json
{
  "vehicle_id": "us_m5a1",
  "directly_researchable": true,
  "research_cost": 7900,
  "best_current_br": 23,
  "expanded_best_br": 27,
  "new_frontier_readiness": true,
  "forced_include_adopted": true,
  "role_changes": ["mobility"]
}
```

The LLM can then explain why an unlock is valuable.

---

# 10. MCP Improvements

Preserve the existing 11 tools unless a new tool materially improves separation of concerns.

Existing tools:

```text
get_data_status
list_vehicles
get_vehicle
get_vehicle_statistics
get_user_progress
set_user_vehicle_status
analyze_lineup
compare_lineups
generate_lineups
suggest_lineup_additions
evaluate_next_unlocks
```

Possible additions, only if justified:

```text
get_research_graph_status
list_researchable_next
```

Prefer extending existing structured responses before adding redundant tools.

## Tool Schema Clarity

Milestone 1 acceptance tripped over MCP BR input:

```text
2.7 → INVALID_ARGUMENT
27 → valid
```

Structured API BRs intentionally use integer tenths.

Make this impossible to miss in MCP schemas/descriptions.

Example description:

```text
Maximum BR in integer tenths.
Examples:
10 = 1.0
23 = 2.3
27 = 2.7
40 = 4.0
```

Add schema examples if supported by the SDK.

---

# 11. Data Status / Evidence Bundle

Expand `get_data_status` to clearly expose:

```text
active vehicle snapshot
active capability snapshot
active research graph snapshot
active statistics snapshot
active override revision
ruleset revision/hash
bundle ID
```

For each optional component report:

```text
status
purpose
provider
freshness
compatibility
reason
```

Example:

```json
{
  "capabilities": {
    "status": "available",
    "snapshot_id": "...",
    "coverage": {
      "vehicles_total": 161,
      "vehicles_with_any_capability": 45
    }
  }
}
```

Partial enrichment is allowed.

Do not imply completeness.

---

# 12. Evidence Coverage Metrics

Add useful operational diagnostics.

Examples:

```text
vehicle metadata coverage
Ground RB BR coverage
availability resolution coverage
capability coverage
research graph coverage
statistics coverage
statistics identity match rate
```

This will help distinguish:

```text
advisor believes X
```

from:

```text
advisor lacks enough evidence to evaluate X
```

Expose coverage through `data status` or a dedicated diagnostic DTO.

---

# 13. Live Data Refresh Safety

Preserve fixes discovered during Milestone 1 live testing.

Regression requirements:

* normalize provider request parameters correctly;
* HTTP 200 + empty vehicle collection is not accepted as a successful operational snapshot unless explicitly expected;
* live provider canonical aliases remain stable;
* incompatible optional evidence does not crash service startup;
* acceptance-purpose data never silently attaches to operational bundles;
* raw failed/invalid snapshots remain immutable for diagnosis.

Add provider contract tests for:

```text
wrong-case country filter returns []
```

or equivalent upstream edge behavior so this specific bug cannot return.

---

# 14. Database and Migration Requirements

Add Alembic migrations as needed.

Possible additions:

```text
capability snapshot metadata
research graph snapshot metadata
acquisition/researchability fields
canonical alias migrations
profile reconciliation audit
statistics import diagnostics
evidence coverage
```

Do not mutate old Milestone 1 rows in place where a versioned observation or migration/audit record is more appropriate.

Migration tests must include upgrading a Milestone 1 `0002` database.

The preserved live-acceptance database should be included as a regression scenario if practical without committing private/local data.

Otherwise construct an equivalent test database programmatically.

---

# 15. Dashboard

Do not build a polished dashboard in Milestone 2.

A simple diagnostic view may be added only if it materially helps validate:

* research graph;
* capability coverage;
* statistics import;
* profile reconciliation.

CLI and MCP remain the primary interfaces.

---

# 16. Aircraft / CAS

Full aircraft lineup planning remains out of scope.

Do not include aircraft merely because Ground RB allows them.

Architect schemas so mixed lineups can be introduced later.

Milestone 2 remains focused on ground vehicle recommendation quality.

---

# 17. Ruleset Changes

Do not overwrite the Milestone 1 ruleset.

Any behavioral changes create a new ruleset revision.

Examples:

```text
m2-capability-aware-v1
m2-redundancy-aware-v1
```

Keep the old Milestone 1 ruleset available for regression tests.

Every analysis continues to report the ruleset hash.

---

# 18. Testing

Maintain:

* pytest
* ≥80% line coverage
* ≥80% branch coverage
* Ruff
* strict mypy
* dependency audit
* deterministic repeat-run testing
* MCP/CLI parity

Add tests for:

## Capabilities

* present
* false
* unknown
* partial snapshot coverage
* conflicting provider/override facts
* scouting semantics
* stabilizer facts

## Availability

* normal research vehicle
* premium
* pack
* event
* gift/special
* provider misclassification corrected by resolved evidence
* unknown classification

## Aliases

* deprecated provider alias
* profile old canonical ID
* dry-run reconciliation
* collision/conflict
* idempotent completed reconciliation

## Research graph

* simple chain
* branching
* foldered vehicles if represented
* unavailable graph
* stale graph
* incompatible graph
* directly reachable unlock
* locked indirect vehicle

## Statistics

* JSON
* CSV
* missing metrics
* unknown vehicle identity
* partial identity match
* scope mismatch
* sample-size shrinkage
* temporal staleness
* no operational statistics

## Advisor quality

* duplicate SPAA
* multiple general mediums
* one isolated high-BR vehicle
* mature 2.7 lineup
* M24 scouting capability
* unknown capability does not equal false

## MCP

* integer-tenths BR descriptions/examples
* all prior tools maintain parity
* evidence bundle contains all active sources

---

# 19. Milestone 2 Acceptance Scenario

Use the live USA profile from Milestone 1.

Profile:

```text
USA
Ground Realistic
5 crews
premiums false
events false
packs false
M3 Lee researching
normal Rank I vehicles owned
```

## Phase A — Data

Acceptance must demonstrate:

1. operational vehicle/BR snapshot;
2. operational capability evidence;
3. resolved availability/researchability evidence;
4. operational research graph;
5. optional operational statistics if legitimately available/imported.

Report coverage for each.

## Phase B — Current lineup

Generate current frontiers.

Verify:

* no rule interprets unknown capability as verified absence;
* duplicate SPAA behavior is explainable;
* current recommendation and lower-BR alternatives expose all evidence.

Do not require the exact Milestone 1 lineup if improved evidence legitimately changes the result.

## Phase C — M3 Lee

Evaluate M3 Lee hypothetical ownership.

Verify:

* profile state remains unchanged;
* 2.7 candidate/frontier is evaluated;
* researchability/graph evidence is shown;
* cross-BR comparisons remain non-numeric.

## Phase D — Research

`evaluate_next_unlocks` must now be available.

Report directly reachable unlocks with:

* graph evidence;
* RP cost;
* expanded-pool lineup;
* forced-include lineup;
* readiness changes;
* whether the unlock opens a higher ready frontier.

## Phase E — Capabilities

Inspect at least one known capability-rich vehicle, preferably M24.

The application should expose factual capability evidence sufficient for relevant rules, not merely infer everything from `light_tank`.

## Phase F — Statistics

If operational statistics were imported:

* retrieve raw statistics for at least three vehicles;
* show peer-relative/confidence-adjusted values;
* verify bounded influence;
* verify scope and period provenance.

If no legitimate operational stats source/export is available:

* mark this portion pending;
* demonstrate validated import workflow with non-synthetic manually supplied acceptance data if available;
* do not fail the rest of Milestone 2 solely because StatShark automation remains unavailable.

---

# 20. Advisor-Quality Review

After deterministic acceptance, run an LLM-assisted review through `WT_Lineup`.

Prompt the LLM to:

* inspect application evidence;
* compare top candidates;
* identify apparent scoring artifacts;
* distinguish evidence ties from real differences;
* compare current versus M3 Lee hypothetical progression;
* suggest rule changes only where concrete scenarios reveal a problem.

Do not allow the LLM to alter rules or data.

Any tuning proposal must become:

```text
documented hypothesis
→ deterministic ruleset change
→ scenario tests
→ acceptance comparison
```

No ad hoc prompt-only fixes.

---

# 21. Non-Goals

Do NOT spend Milestone 2 primarily on:

* polished UI
* account login
* live game-process reading
* overlays
* automatic crew assignment
* aircraft/CAS optimization
* map tactics
* player-specific telemetry
* ML ranking
* embeddings/vector search
* opaque AI scoring
* all nations
* top-tier completeness

USA Ground RB through ~4.0 should become trustworthy first.

---

# 22. Required Deliverables

Milestone 2 should produce:

1. Capability enrichment architecture/provider/import path
2. Provenance-aware operational capability snapshot
3. Capability coverage diagnostics
4. Availability/researchability resolution model
5. Curated/secondary corrections where needed
6. Profile alias reconciliation workflow
7. Operational research graph provider/importer
8. Working `evaluate_next_unlocks` on live USA data
9. Improved statistics import workflow
10. Updated StatShark feasibility report
11. Operational performance snapshot if legitimately obtainable
12. Evidence coverage reporting
13. Capability-aware rules
14. Redundancy-aware lineup logic
15. New versioned ruleset
16. Golden advisor-quality scenarios
17. Updated MCP descriptions/schema examples
18. Alembic migration(s)
19. Updated architecture/data-source docs
20. Milestone 2 acceptance report
21. Full test/security/type/lint verification

---

# 23. Completion Report

At completion report:

## Data Sources

For each source:

```text
provider
purpose
retrieval method
license/usage notes
snapshot ID
revision/date
coverage
known gaps
```

## Capability Evidence

Report:

* vehicles enriched;
* capability types;
* unknown coverage;
* examples;
* provenance.

## Availability

Report:

* classification corrections;
* source/reason;
* unresolved cases.

## Research Graph

Report:

* provider;
* edge count;
* covered USA vehicles;
* unresolved/ambiguous edges;
* resulting next-unlock behavior.

## Statistics

Report:

* acquisition/import method;
* scope;
* period;
* vehicle coverage;
* identity-match rate;
* missing data;
* confidence behavior.

## Rules

List differences between Milestone 1 and Milestone 2 rulesets.

Explain each change with a tested scenario.

## Advisor Quality

For every golden scenario report:

```text
top frontier(s)
recommendation
lower-BR alternative
important warnings
unexpected artifacts
whether expected properties passed
```

## MCP

Report:

* tool changes;
* schema changes;
* CLI/MCP parity;
* deterministic IDs.

## Verification

Report:

* tests;
* line coverage;
* branch coverage;
* Ruff;
* strict mypy;
* dependency/security audit;
* migrations;
* deterministic repeat;
* live MCP acceptance.

---

# Engineering Principle

Milestone 2 should optimize **evidence quality before recommendation cleverness**.

Prefer:

```text
"I do not have verified scouting evidence for this vehicle."
```

over:

```text
"This vehicle cannot scout."
```

Prefer:

```text
"This unlock opens a BR 2.7 readiness-passing frontier."
```

over:

```text
"This is the best tank to research."
```

Prefer:

```text
"M13 and M15 are tied under current evidence."
```

over inventing a distinction the application cannot support.

The deterministic engine remains authoritative for facts, legal/research relationships, scoring, and candidate construction.

The LLM remains responsible for contextual reasoning, tradeoff interpretation, and explanation.
