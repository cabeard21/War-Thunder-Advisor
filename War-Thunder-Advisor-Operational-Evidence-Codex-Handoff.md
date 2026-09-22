# Codex handoff: Operational evidence completion and coverage diagnostics

## Task

Implement the next evidence-quality improvement in the War Thunder Lineup Advisor. The running advisor lacks usable operational performance statistics and has incomplete capability evidence, while its coverage summary makes capabilities look complete.

Work in the existing repository, historically `D:\Repos\war_thunder_advisor`; confirm the actual checkout and read its `AGENTS.md` and relevant architecture documents before changing code. Implement and verify the work, not just a proposal. Reuse existing adapters, importers, snapshot selection, capability resolution, and diagnostics where possible.

The user plays USA Ground Realistic with five crew slots. Prioritize their current owned vehicles and immediate research options, then the supported USA Ground RB catalog. Discover current progress from the configured profile rather than hard-coding this handoff's snapshot.

## Observed live evidence — September 21, 2026

These findings came directly from the running WT_Lineup MCP. They are a baseline to reproduce, not assumptions about current repository internals.

| Area | Observed result |
| --- | --- |
| Schema | `0005` |
| Active vehicle snapshot | `wt-vehicles-api-20260919T030556Z`, operational, community API |
| Active capability snapshot | `wt-vehicles-api-capabilities-20260919T055326249370Z`, operational |
| Capability coverage | 161/161 vehicles, 100%, no listed gaps |
| Active statistics | No selected statistics snapshot; status `unavailable` |
| Newest stored statistics reported | `fixture-usa-ground-rb-statistics-m1`, provider `committed_acceptance_fixture`, purpose `acceptance` |
| Compatibility reason | `snapshot purpose mismatch: operational vehicle evidence cannot use acceptance statistics` |
| Identity aliases | Unavailable |
| Research graph | Available, reported coverage 17/161 (10.56%) |
| Profile returned | `acceptance-usa-ground-rb`, USA, Ground Realistic, five slots |

The status response identifies the newest stored statistics snapshot, not a complete inventory of every stored snapshot. Inspect the database before claiming that no other statistics datasets exist. The profile's name does not establish that it is disposable: it currently contains the user's progress and must be preserved.

`get_vehicle("us_m3_stuart")` returned:

- Base `vehicle.capabilities: []`.
- Resolved `artillery: present`, sourced from the community API's `#modifications` field.
- Resolved `vertical_stabilizer: present`, sourced from `#gun_stabilizer.has_vertical`.
- No scouting resolution in that returned list.
- Source references use `community-api:/api/vehicles/us_m3_stuart`.

`get_vehicle_statistics("us_m3_stuart")` returned an empty result.

Diagnostic lineup used:

```json
["us_m3_stuart", "us_m3a1_stuart", "us_m2_medium", "us_m8_scott", "us_m13_mgmc"]
```

Its analysis returned:

- Scouting `unknown` for all five vehicles, raw score `null`, composite fallback `effective_score: 50`, warning `capability_evidence_incomplete`.
- Statistical strength raw score `null`, composite fallback `effective_score: 50`, with null metrics and warning `statistics missing or incompatible`.
- An uptier-resilience proxy using a mix of vehicle class and resolved capabilities, with numerous unknown capability inputs.

This lineup was a diagnostic probe, not a recommended lineup. Its separate BR-cohesion/readiness failure is outside this task.

## Guardrails and scope

- Preserve user progress, ownership states, presets, advisor context, and existing immutable evaluation records.
- Preserve separation of acceptance fixtures and operational evidence. Never relabel fixture statistics, weaken compatibility checks, or fabricate observations to clear warnings.
- Read the repository's established source policy. Prior planning used the community API adapter and provenance-stamped manual imports for gaps; do not silently introduce direct Gaijin/Wiki scraping or a new collection boundary.
- Do not change scoring weights, readiness thresholds, BR rules, or neutral fallback behavior merely to hide missing data. Report any separately justified scoring change explicitly before including it in this task.
- Keep capabilities supported by a vehicle distinct from modifications unlocked by the user. Do not invent per-vehicle modification ownership.
- Treat source discovery and operational data availability as work items. No suitable live statistics provider has been verified by this handoff.
- The exposed 20-tool MCP supports reading/evaluation and user-context mutations, but exposes no evidence import/refresh operation. Use or extend the local ingestion workflow; a new MCP mutation tool is not required by default.

## 1. Reproduce and trace the gaps

Identify the actual database, profile, configured providers, installed package, and entry points used by the running service. Do not assume CLI and MCP use the same database or installed build. Capture a read-only baseline and use the existing backup procedure before schema or operational-data writes.

Trace raw provider payload -> normalized observations -> persisted snapshots -> active bundle -> vehicle resolution -> rule inputs -> CLI/MCP/dashboard output.

Produce a compact gap inventory for the target vehicle set. For each capability used by scoring, distinguish:

- Source supplies explicit evidence but the adapter drops or misinterprets it.
- Source omits the field or does not support that capability.
- Evidence is stored but not selected/resolved/exposed correctly.
- Conflicting or incompatible evidence prevents resolution.

Check why base `vehicle.capabilities` is empty when resolved capabilities are present. Document which representation clients should consume and fix a misleading contract if needed, preserving compatibility where practical.

## 2. Complete capability ingestion and resolution

Enumerate the capabilities actually consumed by current rules, including scouting, stabilizer/vertical stabilizer, smoke, artillery, and high-caliber HE where applicable. Audit real provider payloads and their documented semantics before changing mappings.

Use the existing resolution model for `present`, verified `absent`, `unknown`, and conflicts. Preserve explicit boolean false values. Omitted fields, null values, incomplete lists, and unsupported provider features must remain unknown unless a documented completeness contract justifies absence.

Do not equate vertical stabilization with full stabilization. Do not invent a high-caliber HE threshold or infer scouting solely from vehicle class/rank without an explicit, versioned, sourced rule.

For remaining source gaps, implement or finish the existing manual-evidence import route. Supply a documented JSON/CSV example matching the real schema, with stable vehicle identity, capability, observation value, source reference, retrieval/verification date, and applicable revision/scope. Use existing provenance and confidence conventions. Examples containing synthetic data must remain test/example data.

Import actual source-backed records for the prioritized vehicles where available. Do not deliver only a schema and fixtures while declaring capability completion. Where evidence cannot be obtained, preserve the unknown and report the exact remaining gap.

Ensure later routine refreshes do not silently discard curated evidence; define deterministic precedence/conflict handling using the existing architecture. Validate capability observations against the active vehicle identities and compatibility rules.

## 3. Establish usable operational statistics

First inspect all configured statistics providers, import paths, and stored snapshots. Determine whether a provider exists but is unconfigured, ingestion failed, valid records were rejected, or no operational source has been implemented.

If a source is needed, verify an actual accessible API/export or source-backed manual dataset before building an adapter around it. Record its documented access method, supported mode, coverage, period semantics, metric definitions, sample denominator, update behavior, and limitations. Do not assume a named community statistics website offers a suitable API.

Map source identities explicitly to canonical vehicle IDs. Quarantine ambiguous/unmapped records; do not silently fuzzy-match variants. Do not mix Arcade, Realistic, Simulator, player-specific, or aggregate statistics under the same scope.

Persist provenance, operational purpose, source revision/checksum, retrieval time, sample period when available, sample size/denominator when available, and supported metrics. Missing metadata stays unknown; do not invent a sample size or period. If the existing scoring contract requires information the source cannot provide, report the source as insufficient for that scoring use.

Validate ranges, units, denominators, duplicate records, zero denominators, and finite numeric values. Keep win rate, kill/death ratio, and kills per battle distinct. Preserve existing sample-size adjustment and peer normalization, and verify that sufficient compatible peer evidence exists for any score calculated.

Integrate accepted data through the existing snapshot/bundle selection policy. An operational label alone must not establish compatibility. Partial vehicle coverage should be reported accurately and handled according to the existing contract.

If a suitable source is available, complete a real operational import and prove that vehicle statistics and lineup analysis use it. If no suitable source is available, finish all independent capability/diagnostic work and the usable import route, document the investigated sources and exact blocker, and mark operational statistics acceptance pending. Do not claim this task fully restores statistics without live evidence.

## 4. Correct coverage and user-facing diagnostics

Distinguish record presence from resolved field completeness. Keep existing response fields compatible where possible, adding explicit metrics instead of silently changing their meaning.

Expose per-capability present/verified-absent/unknown/conflict counts and the denominator for the requested scope. A vehicle with only artillery evidence must not imply that its scouting is known. Count both present and verified absent as resolved; count unknown and unresolved conflicts separately.

Show catalog-level coverage and coverage relevant to the active recommendation or profile. Identify missing vehicle/capability pairs and their reasons, with bounded/paginated details if necessary. Statistics diagnostics should distinguish absent source, incompatible snapshot, missing vehicle row, missing metric, insufficient sample metadata, and stale evidence according to actual rules.

Align `get_data_status`, resolved vehicle output, analysis warnings, CLI, and the dashboard. Label the existing score fallback as a neutral composite contribution; never present 50 as an observed performance score or verified capability. Continue to identify uptier resilience as a proxy and expose which inputs were unknown.

Keep immutable stored evaluations unchanged. New evidence must produce the appropriate new bundle/revision and existing staleness signals; recomputation remains explicit.

## 5. Verification and acceptance

Use focused regression tests for these behavior changes and run the repository's required quality/acceptance gates. Do not indiscriminately update frozen expectations or evidence IDs to make tests pass; explain legitimate changes.

| Check | Required evidence |
| --- | --- |
| Boolean handling | True, false, omitted, null, and incomplete lists resolve according to source semantics |
| Capability precedence | Source conflicts and curated evidence survive refresh with deterministic outcomes |
| Coverage | Complete record count with missing scouting produces incomplete field coverage and named gaps |
| Provenance | Every known capability has traceable evidence; unsupported claims remain unknown |
| Statistics isolation | Acceptance fixtures remain ineligible for operational scoring |
| Statistics validation | Invalid units/ranges, wrong mode, ambiguous IDs, and unusable sample metadata are rejected or quarantined with reasons |
| Partial statistics | Missing vehicles/metrics remain null and coverage is accurate |
| Selection | Compatible operational evidence is selected; incompatible evidence gives a precise reason |
| Scoring | Existing fallback and normalization behavior is preserved; known inputs alone influence evidence-based scores |
| Persistence | Imports are safely repeatable; a failed import does not leave a partially active bundle |
| User state | Progress, presets, and context are unchanged; old evaluations remain immutable |
| Interfaces | CLI, dashboard, and the actual running MCP agree on the new evidence and diagnostics |

Perform a final live check against one consistent evidence bundle:

1. Read `get_data_status` and the current user progress; record the bundle and profile revisions.
2. Read representative vehicles, including `us_m3_stuart`, a source-verified scouting-capable vehicle, and a source-verified scouting-absent vehicle. Discover examples from evidence rather than assuming facts.
3. Retrieve their statistics, including an uncovered example if coverage is partial.
4. Re-run the diagnostic lineup above and one current user lineup. Compare capability resolutions, coverage, warnings, and statistical-strength evidence with the baseline.
5. Confirm CLI/MCP parity using the same installed build, database, profile, and evidence bundle. If evidence changes mid-check, recapture a consistent baseline.
6. Verify the operational statistics path with actual data if available. Distinguish automated fixture tests from this live acceptance.

Do not treat a remaining genuine unknown as a test failure or suppress its warning. Report implementation completion and operational evidence completeness separately.

## Deliverables

- Implemented repository changes and focused regression tests.
- Updated architecture/source-mapping documentation and exact verified Windows commands for refresh/import, diagnostics, and service restart if required.
- A practical manual-import example using the actual schema, with example/test data clearly marked.
- Before/after coverage for the catalog and the user's prioritized vehicle set.
- An evidence report with source provenance, selected snapshot IDs, interface parity, relevant test results, and unresolved gaps.
- A concise final status: capability ingestion, coverage diagnostics, statistics importer, and live operational statistics acceptance each marked complete/partial/blocked with reasons.

Proceed with routine implementation choices using the repository's conventions. Ask only when a material source-policy decision or genuinely missing access blocks progress; complete all independent work first.
