# War Thunder Lineup Advisor — Milestone 1

## Goal

Build the foundation for a local War Thunder lineup-planning and recommendation application.

The project should combine:

1. **Current structured War Thunder vehicle data**
2. **Historical/global vehicle performance statistics**
3. **A deterministic rules-based lineup analysis and candidate-generation engine**
4. **An MCP interface that allows an LLM to inspect the evidence, compare alternatives, and make contextual recommendations**

The intended architecture is similar to an existing pattern used successfully in other projects:

**authoritative structured data → deterministic rules/calculations → MCP tools → LLM reasoning/explanation**

The LLM must not be responsible for determining vehicle BRs, inventing statistics, validating lineup legality, or calculating deterministic scores. Those belong in the application.

The LLM should eventually reason over the results to answer questions such as:

* What should my current lineup be?
* What should I research next?
* Is it worth moving from BR 2.7 to 3.0 yet?
* Which vehicle should this new unlock replace?
* What are my strongest available five vehicles without creating a bad BR spread?
* Give me a lineup emphasizing scouting/flanking.
* Give me a lineup that performs well statistically but still has good role coverage.
* Why does the rules engine prefer lineup A over lineup B?
* What changes if I want to avoid aircraft?
* What lineup should I work toward three unlocks from now?

Do not attempt to build the full final product in this milestone.

---

# Initial Scope

Implement an end-to-end vertical slice for:

* Nation: **USA**
* Mode: **Ground Realistic Battles**
* Ground vehicles initially
* Approximately BR **1.0–4.0**
* User crew slots: configurable, but test primarily with **5**
* Free tech-tree vehicles as the normal baseline
* Premium/event/pack vehicles represented in the schema but optionally excluded
* Aircraft/CAS architecture may be represented, but full air recommendation logic is not required yet

The implementation must remain generic enough to expand to every nation and BR without rewriting the core model.

---

# Reference Products / Concepts

Study these products conceptually before implementing:

## WT Lineup

WT Lineup provides useful lineup-oriented concepts such as:

* BR balance/spread
* backup depth
* role coverage
* anti-air coverage
* CAS coverage
* uptier resilience
* lineup warnings
* suggested additions

Do not clone its implementation or score blindly.

We want an independently implemented deterministic engine whose individual factors are inspectable.

## StatShark

StatShark exposes global War Thunder vehicle statistics including vehicle-level performance information.

Potential useful metrics include, where actually available:

* win rate
* battles/games
* kills
* deaths
* ground kills
* air kills
* kills per death
* kills per game/spawn where derivable and semantically valid

Treat these statistics carefully.

Different metrics may have different mode scopes. In particular, do not assume aircraft statistics represent aircraft performance specifically inside Ground RB unless the source explicitly provides that distinction.

Every imported statistical field needs provenance and scope metadata.

## War Thunder Roster Manager

Study the data-management approach used by War Thunder Roster Manager.

Useful concepts include:

* local vehicle database
* WarThunder Vehicles/community API data
* War Thunder Wiki supplementation
* curated overrides/supplements
* separate BRs by mode
* update/change tracking
* ownership
* lineup storage
* crew-training awareness

Do not vendor or copy third-party code without explicitly checking licensing first.

---

# Core Design Principle: Evidence Before Advice

The application should expose enough evidence that an LLM never has to guess.

A recommendation should conceptually be based on:

```text
Current game-data snapshot
        +
Performance-statistics snapshot
        +
User ownership/progression
        +
Lineup rules
        +
Candidate-generation engine
        ↓
Structured candidate evidence
        ↓
MCP
        ↓
LLM reasoning and explanation
```

Never collapse all of this into a single opaque recommendation score.

---

# 1. Project Architecture

Prefer a Python implementation consistent with a local MCP-oriented workflow.

Suggested structure:

```text
war-thunder-advisor/
    pyproject.toml
    README.md

    src/wt_advisor/
        domain/
            vehicles.py
            lineups.py
            stats.py
            users.py
            scoring.py

        data/
            providers/
            imports/
            normalization/
            snapshots/

        rules/
            br.py
            roles.py
            backups.py
            anti_air.py
            uptier.py
            performance.py

        services/
            vehicles.py
            statistics.py
            lineup_analysis.py
            lineup_generation.py
            progression.py

        storage/
            db.py
            migrations/

        mcp/
            server.py
            tools/

        cli/
            main.py

    tests/
```

SQLite is appropriate for local durable state.

Use schema migrations from the beginning.

Keep domain logic independent from HTTP/MCP/UI transport.

---

# 2. Vehicle Data Model

Create a canonical internal vehicle identity.

Do not use display names as primary keys.

Example:

```text
Vehicle
- vehicle_id
- source_vehicle_id
- name
- nation
- vehicle_class
- rank
- research_parent_id
- research_cost
- purchase_cost
- availability_type
- is_premium
- is_event
- is_squadron
- is_pack
- capabilities
```

Battle ratings must be mode-specific:

```text
VehicleBattleRating
- vehicle_id
- mode
- battle_rating
- source
- snapshot_id
```

Never use one generic `br` field when the source distinguishes modes.

For this milestone, Ground RB is the important value.

---

# 3. Capabilities and Roles

Do not rely solely on War Thunder's official class designation.

Represent factual capabilities separately from inferred lineup roles.

Possible capabilities:

```text
SCOUTING
ARTILLERY
STABILIZER
RADAR
IRST
SAM
AUTOCANNON_AA
OPEN_TOP
SMOKE
RECON_DRONE
THERMALS
ATGM
HIGH_CALIBER_HE
```

Then have deterministic role classification such as:

```text
BRAWLER
GENERAL_MEDIUM
FLANKER
SCOUT
SNIPER
TANK_DESTROYER
HEAVY_ANCHOR
SPAA
ANTI_ARMOR_SPECIALIST
```

A vehicle may satisfy multiple roles.

Keep:

**source facts**

separate from:

**our inferred roles**

so role logic can evolve independently.

---

# 4. Data Provenance and Snapshots

This is critical.

Every imported dataset must create an immutable snapshot.

Example:

```text
DataSnapshot
- snapshot_id
- dataset_type
- provider
- provider_version
- retrieved_at
- source_revision
- checksum
- notes
```

Relevant dataset types might include:

```text
VEHICLE_METADATA
BATTLE_RATINGS
TECH_TREE
GLOBAL_STATISTICS
CURATED_OVERRIDES
```

Every recommendation result must report which snapshots were used.

Do not silently combine stale and current data.

---

# 5. Provider Abstraction

Do not couple domain logic directly to one website/API.

Define provider interfaces such as:

```python
class VehicleDataProvider:
    def fetch_vehicles(...) -> RawVehicleDataset: ...

class StatisticsProvider:
    def fetch_vehicle_statistics(...) -> RawStatisticsDataset: ...
```

Then normalize into the application's canonical schema.

Potential sources should be investigated during implementation, not assumed.

For vehicle metadata investigate:

* WarThunder Vehicles/community API
* official War Thunder Wiki
* other legitimate structured sources

For performance statistics investigate StatShark.

Important:

Before implementing automated StatShark acquisition, determine what method is technically available and appropriate.

Do not build brittle HTML scraping into core domain logic.

If automatic acquisition cannot be implemented cleanly in Milestone 1, create the provider interface and support importing a fixture/exported dataset.

The rest of the application must remain functional without live StatShark access.

---

# 6. Curated Override Layer

Game-data providers may lag or disagree.

Create a small explicit override mechanism.

Example:

```text
overrides/
    battle_ratings.yaml
    vehicle_metadata.yaml
```

Every override should contain:

```text
vehicle_id
field
value
reason
source/reference
added_at
```

Raw imported data must remain preserved.

Overrides are applied during normalization or resolution, not by mutating historical snapshots.

The application should be able to answer:

> Why does the application think this vehicle is BR 3.7?

with a traceable answer.

---

# 7. Vehicle Statistics Model

Represent raw statistical observations before creating derived ratings.

Example:

```text
VehicleStatistics
- vehicle_id
- snapshot_id
- mode_scope
- vehicle_scope
- sample_start
- sample_end
- battles
- wins
- losses
- win_rate
- kills
- ground_kills
- air_kills
- deaths
```

Fields may be nullable.

**Missing is not zero.**

Store the scope explicitly.

Examples:

```text
GROUND_REALISTIC_GROUND_VEHICLES
REALISTIC_ALL_CONTEXTS
AIR_REALISTIC
UNKNOWN_REALISTIC_SCOPE
```

Never compare statistics from incompatible scopes as though they measure the same thing.

---

# 8. Derived Metrics

Implement derived metrics separately.

Potential metrics:

```text
K/D = kills / deaths
Ground K/D = ground_kills / deaths
Kills per battle
Win-rate delta versus BR
K/D delta versus BR
Class-relative K/D
Nation/BR-relative win rate
```

Do not treat raw win rate or K/D as absolute vehicle quality.

Create normalized statistics relative to useful peer populations.

Example peer groups:

```text
same mode
same BR ± 0.3
same broad class
same time snapshot
```

Eventually this may produce a **Vehicle Performance Index**, but Milestone 1 should prioritize transparency over tuning a magic number.

If an initial performance score is implemented, expose every component.

Example:

```json
{
  "performance_score": 72.4,
  "components": {
    "win_rate_relative": 0.61,
    "kd_relative": 0.74,
    "kills_per_battle_relative": 0.68,
    "sample_confidence": 0.91
  }
}
```

---

# 9. Statistical Confidence

Sample size must matter.

A vehicle with:

```text
61% WR
180 battles
```

must not automatically outrank:

```text
56% WR
500,000 battles
```

Implement at least a basic confidence weighting or shrinkage mechanism.

Document the method.

It does not need to be statistically sophisticated in Milestone 1, but it must avoid treating tiny samples as equally authoritative.

Retain both:

* raw metric
* confidence-adjusted metric

---

# 10. User State

Represent user progression separately from game data.

Example:

```text
UserProfile
- profile_id
- preferred_mode
- crew_slots
```

Vehicle state:

```text
UserVehicleState
- vehicle_id
- status
```

Status enum:

```text
OWNED
RESEARCHING
UNLOCKED_NOT_PURCHASED
AVAILABLE_TO_RESEARCH
LOCKED
UNKNOWN
```

Also support:

```text
include_premiums
include_event_vehicles
include_pack_vehicles
```

Default to ordinary research-tree progression.

For the initial fixture/profile:

```text
nation = USA
mode = Ground RB
crew_slots = 5
all Rank I USA ground vehicles = OWNED
M3 Lee = RESEARCHING
```

---

# 11. Lineup Model

A lineup is ordered and slot-aware:

```text
Lineup
- nation
- mode
- slots[]
```

Each slot contains a vehicle ID.

Determine lineup matchmaking BR deterministically from current Ground RB BR data.

Do not let callers supply an arbitrary lineup BR if it conflicts with the contained vehicles.

---

# 12. Rules-Based Lineup Analysis

Implement analysis as independent rules/components.

Do not begin with one monolithic `score_lineup()` function.

Initial factors:

## BR Cohesion

Measure the difference between lineup BR and each vehicle's BR.

Flag weak backups that have been dragged too far upward.

## Backup Depth

Measure how many vehicles remain competitive near lineup BR.

For example, configurable windows such as:

```text
top BR
within 0.3
within 0.7
more than 0.7 below
```

Do not hard-code these as permanent truth; make thresholds configurable.

## Role Coverage

Measure role diversity.

For a five-slot ground lineup, useful coverage may include:

* general combat vehicle
* secondary combat vehicle
* mobile/flanking option
* anti-armor specialist
* anti-air

Do not require one exact template.

## Role Overlap

Detect excessive duplication where appropriate.

Three Shermans are not automatically bad; overlap is a signal rather than an automatic invalidation.

## Anti-Air

Detect whether meaningful SPAA exists.

Also distinguish:

* no SPAA available near BR
* SPAA exists but user has not researched it
* SPAA deliberately omitted

## Scouting

Recognize the strategic value of scouting-capable light vehicles.

## Uptier Resilience

Approximate whether major combat vehicles remain useful against +1.0 matchmaking.

Milestone 1 may use coarse heuristics.

The rule must expose why it reached its conclusion.

## Statistical Strength

Aggregate vehicle performance evidence.

Do not allow this factor to overwhelm lineup construction.

A statistically excellent collection of five vehicles can still be a poor lineup.

---

# 13. Score Output

Every rule should return something like:

```json
{
  "rule": "backup_depth",
  "score": 84,
  "status": "good",
  "evidence": {...},
  "warnings": [],
  "explanation": "..."
}
```

The overall analysis may provide a composite score for sorting candidates, but it must also expose all components.

Example:

```json
{
  "overall_score": 86,
  "components": {
    "br_cohesion": 94,
    "backup_depth": 88,
    "role_coverage": 82,
    "anti_air": 90,
    "scouting": 75,
    "uptier_resilience": 78,
    "statistical_strength": 81
  }
}
```

Weights must be configuration, not hidden constants.

---

# 14. Candidate Lineup Generation

Implement a deterministic candidate generator.

Inputs:

```text
nation
mode
crew_slots
available vehicle pool
optional target BR
ownership restrictions
premium/event restrictions
preferences
```

Do not brute-force enormous trees blindly.

For the initial USA 1.0–4.0 fixture, exhaustive enumeration may be acceptable for testing, but design the service so pruning/beam-search/constraint techniques can be added later.

Generator responsibilities:

1. produce valid candidate combinations
2. determine lineup BR
3. analyze each candidate
4. sort by deterministic composite score
5. retain component evidence
6. return top N

Do not use an LLM during candidate generation.

---

# 15. Suggest-Next-Vehicle Logic

This is an important WT Lineup-like capability.

Given an existing lineup:

```text
[M3 Lee, M5A1, M3A1 Stuart, M22]
```

and one empty slot, evaluate eligible additions.

Return:

```text
candidate vehicle
resulting lineup
overall delta
component deltas
new warnings
resolved warnings
```

This allows the LLM to say something more meaningful than:

> Add M16.

It can say:

> Adding the M16 preserves the same 2.7 matchmaking BR while filling the lineup's missing anti-air role and improving role coverage without weakening backup depth.

The deterministic engine supplies those facts.

---

# 16. Compare Lineups

Implement first-class lineup comparison.

Input:

```text
lineup A
lineup B
```

Return component deltas:

```text
BR cohesion
backup depth
roles
AA
scouting
uptier resilience
statistics
```

Do not return only "A is better."

This evidence is intended for LLM interpretation.

---

# 17. Progression / Research Graph — Foundation Only

Create the domain interface now, even if full optimization is deferred.

Future goal:

Given current ownership/research state, evaluate how each reachable research unlock changes the strongest available lineup.

Conceptually:

```text
current lineup
    ↓ unlock X
best reachable lineup after X
    ↓
lineup improvement / RP cost
```

Milestone 1 does not need full multi-step optimization.

Implement one-step analysis if practical:

```text
evaluate_next_unlocks()
```

For each directly researchable vehicle:

```text
vehicle
research cost
best lineup before
best lineup after
score delta
component deltas
```

---

# 18. MCP Server

Expose the deterministic application through MCP.

Initial tools should include:

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

If mutation tools are implemented, keep read and write operations clearly distinct.

Tool responses must be structured rather than prose-heavy.

---

# 19. Required MCP Evidence

`get_data_status` should expose:

```text
vehicle snapshot ID
vehicle snapshot date
BR snapshot ID
BR snapshot date
statistics snapshot ID
statistics period
statistics provider
active override revision
database schema revision
```

`get_vehicle` should expose both resolved values and provenance where useful.

`analyze_lineup` should expose:

```text
resolved vehicle IDs
resolved BRs
lineup BR
rule results
statistics snapshot
overall score
warnings
strengths
```

The LLM should always be able to establish what data revision produced an answer.

---

# 20. CLI

Provide a small CLI for development and acceptance testing.

Example commands:

```text
wt-advisor data status
wt-advisor vehicles list --nation usa --max-br 4.0
wt-advisor vehicle show us_m3_lee
wt-advisor lineup analyze us_m3_lee us_m5a1 ...
wt-advisor lineup generate --nation usa --br 2.7 --slots 5
wt-advisor lineup suggest-additions ...
wt-advisor progress next
```

Do not make a dashboard a Milestone 1 dependency.

A UI can come later.

---

# 21. Testing

Unit-test each rules component independently.

At minimum test:

### BR calculation

Highest relevant Ground RB BR correctly determines lineup matchmaking BR.

### BR spread

A low-BR vehicle dragged upward is recognized.

### Backup depth

Near-top vehicles count appropriately.

### Role coverage

Adding SPAA/scouting/etc. changes expected rule output.

### Missing data

Missing statistics remain `null`/unknown rather than becoming zero.

### Stats confidence

Small samples do not receive the same confidence as large samples.

### Snapshot isolation

An analysis references one explicit set of snapshots.

### Overrides

A curated BR override changes the resolved value without modifying imported raw data.

### Candidate generation

No invalid nation/mode vehicles appear.

### Determinism

Same data + same config + same user state produces identical candidate results.

---

# 22. Milestone 1 USA Acceptance Fixture

Create a reproducible fixture covering the early U.S. Ground RB tree through approximately BR 4.0.

It should include enough vehicles to exercise:

* Stuart family
* M22
* M3 GMC
* M15/M16 anti-air progression
* M3 Lee
* M5A1
* M4A3 (105)
* M10 GMC
* early 75 mm Shermans
* M24 if it falls inside the resolved current BR cutoff

Do not hard-code BR values in tests unless they are fixture-specific.

Fixture data must state its snapshot/revision.

---

# 23. Required Acceptance Scenario

Create this user state:

```text
Nation: USA
Mode: Ground RB
Crew slots: 5

Owned:
all applicable Rank I USA ground vehicles

Researching:
M3 Lee

Premium/event vehicles:
excluded
```

Demonstrate:

1. Current best candidate lineups from owned vehicles.
2. Analysis of the current recommended lineup.
3. M3 Lee as a hypothetical newly owned vehicle.
4. Best candidate lineup after adding the M3 Lee.
5. The exact lineup-component deltas caused by that unlock.
6. Suggested next additions.
7. Available performance statistics and their provenance.
8. Any missing/low-confidence statistics explicitly marked as such.
9. MCP retrieval of all the same evidence without recalculation inconsistencies.

The test is successful even if the engine's preferred lineup differs from WT Lineup.

The requirement is that the result is deterministic, explainable, sourced, and sensible.

---

# 24. Data Freshness

This project exists partly because stale BR information is unacceptable.

Implement a clear stale-data policy.

At minimum:

```text
fresh
aging
stale
unknown
```

Do not automatically refuse to operate on stale data, but surface it prominently.

Future imports should be diffable.

Example:

```text
M4A1:
Ground RB BR
3.7 -> 4.0
```

Saved lineups affected by changed BRs should eventually be discoverable.

The schema should support this later.

---

# 25. Important Statistical Caveats

Never imply that vehicle statistics prove intrinsic vehicle quality.

Vehicle WR/KD can reflect:

* player skill distribution
* nation popularity
* lineup quality
* premiums/event ownership
* recent BR changes
* sample size
* patch balance
* matchmaking environment
* player behavior
* mode aggregation

Preserve raw data and make normalized/derived values explicit.

Do not hide these uncertainties behind an LLM-generated ranking.

---

# 26. Non-Goals for Milestone 1

Do NOT spend substantial time yet on:

* polished dashboard
* automatic War Thunder account scraping
* reading the live game process
* modifying War Thunder
* overlays
* map recommendations
* player-specific performance modeling
* crew skill optimization
* SL economy optimization
* CAS loadout optimization
* every nation
* every BR
* naval
* arcade
* simulator
* machine-learned lineup scoring
* vector databases
* autonomous agents

Get the data → rules → candidate → MCP path correct first.

---

# 27. Deliverables

Milestone 1 should produce:

1. Working Python project
2. SQLite schema + migrations
3. Canonical vehicle model
4. Snapshot/provenance model
5. At least one working vehicle-data importer/provider
6. Statistics-provider abstraction
7. StatShark feasibility findings and, if practical, initial importer
8. Curated override system
9. USA Ground RB 1.0–4.0 fixture/current dataset
10. Rules-based lineup analyzer
11. Candidate lineup generator
12. Suggest-addition service
13. Lineup comparison service
14. Initial one-step research evaluation
15. MCP server and tools
16. CLI
17. Tests
18. Acceptance report

---

# 28. Completion Report

At completion, report:

## Architecture

* modules added
* storage design
* provider abstractions
* snapshot strategy

## Data

* vehicle source(s)
* source versions/dates
* BR source
* exact StatShark acquisition method or blocker
* curated overrides
* data freshness behavior

## Rules

For every lineup rule:

* definition
* weighting
* thresholds
* assumptions

## MCP

List every tool and its request/response purpose.

## Acceptance

Run the USA five-slot scenario above and report:

* current candidate lineup(s)
* resulting lineup with M3 Lee
* rule scores before/after
* statistics evidence
* warnings
* snapshot IDs
* suggested next research choices

## Verification

Report:

* unit tests
* integration tests
* MCP smoke tests
* deterministic repeat-run check
* any unresolved data-quality issues

---

# Engineering Principle

Prefer:

**transparent imperfect evidence**

over:

**opaque apparently-smart recommendations**

The end state should allow an LLM to be intelligent about player goals and tradeoffs while the application remains authoritative about game state, statistics, legality, and deterministic analysis.
