# Milestone 3 — Everyday Advisor Workflow

## Task

Implement a local dashboard, durable named lineup presets, and a shared “play now / research next” workflow for the existing War Thunder Lineup Advisor repository. Carry the work through implementation, automated verification, documentation, and a concrete Windows live-acceptance checklist.

The intended experience is: update a vehicle’s ownership → see what changed → build and save a five-slot lineup → inspect an alternative → understand which next unlocks help → restart without losing saved state. The dashboard, CLI, and MCP must use the same services and explain the same results.

This handoff defines the proposed M3 implementation scope. Inspect the repository before choosing file paths, dependencies, API names, or migration numbers. Resolve routine implementation choices autonomously; ask only about a material ambiguity that cannot be resolved from the repository and this scope.

## Starting point and constraints

- The project runs locally on Windows; the previously reported repository path is `D:\Repos\war_thunder_advisor`. Verify the actual checkout and read its `AGENTS.md` and project documentation first.
- The established acceptance slice is USA Ground Realistic through approximately BR 4.0, with five crew slots. Do not hard-code five slots into storage or components; honor the profile’s configured count.
- M1 established deterministic lineup generation/analysis, per-BR frontiers, readiness gates, CLI/MCP parity, profile state, and evidence provenance.
- M2 added operational capabilities, availability and identity resolution, profile reconciliation, independent research graphs, richer one-step unlock evaluation, statistics inspection/import, and advisor-quality scenarios.
- Existing operational scoring uses `m2-capability-aware-v1`; frozen M1 acceptance uses `m1-baseline-v1`. Preserve both.
- The most recent reported correction made `lower_br_alternative` a readiness-passing frontier strictly below the applicable target, recommendation, or highest evaluated BR. No qualifying fallback means `null`. Preserve the implemented contract and regression tests.
- The last reported baseline was 180 passing tests, passing frozen M1/M2 acceptance, and passing Ruff/strict mypy. These are historical reports, not checks performed for this handoff. Establish the actual current baseline before editing.
- Prior live ownership examples and exported acceptance profiles are test evidence, not an instruction to overwrite the user’s current garage. Read current state when needed.

## Outcome and scope

| Feature | Required outcome |
| --- | --- |
| Garage | Browse/filter the catalog, view current progression state, and explicitly update vehicle status. |
| Builder | Add/remove vehicles, pin required vehicles, exclude unwanted vehicles, fill remaining slots, and explain readiness. |
| Presets | Save, rename, load, update, and delete named lineups; persist across restart and reevaluate against current evidence. |
| Play now | Show the highest ready lineup under the active constraints and a valid lower-BR alternative when one exists. |
| Research next | Present existing one-step unlock evaluations grouped by practical benefit and explain prerequisites and blockers. |
| Why and data health | Expose evidence, uncertainty, freshness, readiness failures, and meaningful comparison details. |
| Assistant continuity | Expose the same presets, selected context, constraints, profile revision, and evaluation evidence through MCP and CLI. |

Keep these out of M3: multi-step research optimization, aircraft/CAS or loadout planning, crew skill/training costs, economy optimization, player-skill modeling, all-nation acceptance, scoring-weight tuning, new statistics acquisition, game login, overlays, process inspection, automatic game-state capture, and public hosting. A built-in chat client or paid LLM integration is unnecessary; the existing assistant/MCP workflow supplies conversational advice.

## Non-negotiable domain behavior

1. Reuse the M2 scoring services. Frontend code formats results; it does not independently score, pick research eligibility, or infer vehicle superiority.
2. Preserve cross-BR non-comparability. Overall score deltas remain `null` across different BRs. Show resulting BR, readiness, roles, and backup changes instead of a universal improvement percentage.
3. Preserve unknown semantics. Missing statistics stay visibly unknown; effective neutral values are not observed average performance. Missing capability evidence is not verified absence. Conflicted facts remain unresolved according to M2.
4. Preserve diagnostic-only specialist redundancy: no new weight, readiness effect, or hidden preference. General-purpose duplicates are not penalized merely for sharing a role.
5. Preserve research eligibility and graph semantics. Do not infer ownership or prerequisites from a selected target. Missing or incomplete graph evidence must produce an explained unavailable/partial result, not a fabricated research path.
6. Do not silently switch to a higher BR, drop a pin, reinsert an excluded vehicle, or mark an unready lineup as recommended. Explain unsatisfiable constraints.
7. Keep ordinary reads free of ownership, preset, or preference mutations. Hypothetical analysis must not modify the garage. Explicit evaluation may use existing analysis persistence, but it must not overwrite saved user intent.
8. Preserve frozen M1/M2 outputs and IDs for unchanged requests. New constrained evaluations must identify their effective inputs unambiguously using the repository’s identity conventions. UI-only names, timestamps, and display ordering must not perturb otherwise identical analysis results.

## Implementation slices

### 1. Durable presets and advisor context

Use the existing database, migration tooling, profile identity, canonical vehicle IDs, and service patterns. Add a forward migration; do not rewrite old migrations or recreate live databases.

A preset needs a stable identity, profile association, name, ordered vehicle slots, saved builder constraints, revision, and creation/update timestamps. A saved draft may be incomplete; present its completeness explicitly. Saving a preset does not assign vehicles to actual in-game crews or purchase/own them.

Support multiple named presets per profile. Scope names to a profile, trim whitespace, and reject empty or duplicate names with a clear conflict. Define and document normalization. Validate capacity, duplicate vehicle selections, mode/nation compatibility, and canonical references using existing domain rules.

Persist a small advisor context per profile: selected preset (optional), target BR (optional), required/pinned vehicles, and excluded vehicles. Keep draft changes distinct from saved preferences and preset edits. Loading or viewing a preset must not quietly persist a new context; an explicit selection/save action may do so.

Keep hypothetical ownership request-scoped in M3. Saving a hypothetical lineup may retain its vehicle slots, but must clearly show which vehicles are unowned when loaded in real-ownership mode. Never convert a hypothetical ownership flag into permanent garage state.

Use optimistic revision checks for edits/deletes and shared context writes. For vehicle-status writes, preserve the existing tool contract; add an optional expected-revision guard if needed. A stale write must fail without partial mutation and return enough information to refresh. Do not allow dashboard and assistant edits to silently overwrite one another. Deleting the selected preset must clear that selection transactionally while retaining unrelated profile state.

When a preset is explicitly evaluated, retain a compact reference or snapshot of the last evaluation: effective request, profile revision, evidence bundle/ruleset identity, resolved BRs, readiness, and result identity. Reuse existing result persistence where suitable. A later evaluation can explain material changes without inventing historical evidence. Reading a stored result is distinct from recalculation; full evaluation-history browsing is out of scope.

### 2. Shared services and interface contracts

Implement preset CRUD, advisor-context read/update, and constrained builder behavior in the application/service layer. Add shared typed DTOs consumed by local HTTP, CLI, and MCP.

Preserve existing MCP tools and top-level response compatibility. Add only the operations needed to list/read/manage presets and read/update advisor context; choose concise names after inspecting the existing surface. Keep the existing progression/evaluation tools instead of duplicating their logic behind dashboard-specific tools. Expose all required preset/context operations in the CLI as well, so acceptance can compare transports.

Retain integer-tenths BR inputs in machine interfaces: `23` means 2.3, `40` means 4.0. The UI displays normal decimals and converts through a validated shared boundary. Reject unsupported values instead of rounding silently.

Multiple required vehicles are hard inclusion constraints; exclusions are hard omission constraints. Apply them before candidate selection, retaining readiness and per-BR frontier behavior. No-constraint requests must retain existing behavior. Validate overlap, excess pins, incompatible vehicles, missing ownership, and target incompatibility. Prefer explicit diagnostics over a generic empty list. A hypothetical mode may admit explicitly hypothetical vehicles while remaining visibly separate from real ownership.

The advisor-context response must make saved intent and current evaluation distinguishable. Include profile/context/preset revisions, active evidence identity, current constraints, whether hypothetical inputs are involved, evaluation identity when available, and stale/unavailable reasons. Do not label an old stored result “current.”

### 3. Local dashboard and garage

Reuse an existing frontend/backend stack if present. Otherwise choose the smallest maintainable local stack consistent with the repository. Serve the built frontend and API from the local application with a documented Windows start command and clean shutdown. Bind to loopback by default, keep database access server-side, and apply appropriate same-origin/Host validation and mutation protections. Do not expose the new dashboard over the existing MCP tunnel by default.

Provide a desktop-friendly layout with accessible labels, keyboard operation, visible focus, readable status text, and loading/error/empty states. Avoid color-only meanings. Catalog images are optional and must not introduce a new scraping or asset dependency.

Garage functionality:

- Filter by nation/mode where supported, BR range, vehicle class, progression status, and name.
- Show owned, unlocked-not-purchased, researching, available-to-research, locked, and unknown using the existing domain statuses.
- Explicit status edits return the saved state/revision, then refresh dependent results. Preserve an unsaved builder draft and explain any newly invalid choices.
- Show profile settings used by the advisor and reuse supported profile-edit services for configuration changes. Avoid a separate dashboard-only profile model.
- On a write conflict, keep the attempted edit visible and offer refresh/retry against the new state; never force-save automatically.

### 4. Builder, comparisons, and presets

Show the configured number of slots, with each selected vehicle’s name, resolved Ground RB BR, class/roles, ownership, and relevant capability evidence. Add/remove, pin/unpin, exclude, and suggest/fill actions must operate on a visible draft. A suggestion must not overwrite the user’s draft until applied.

Show resulting lineup BR, readiness and specific failing gates, strengths, coverage gaps, and warnings. Incomplete drafts may be analyzed where supported but must never be presented as a complete ready preset solely because a partial score is high.

Compare alternatives with component explanations. A tied score is a tie; deterministic ordering is not evidence of a better tank. Across BRs, suppress overall delta while showing differences allowed by the domain contract.

Saving/updating a preset requires an explicit action. Reopening uses its saved choices with current catalog/profile validation. If evidence changes a BR, ownership changes eligibility, or a vehicle becomes unresolved, retain the preset and identify the affected slots; do not silently replace or remove vehicles. Use the existing confirmed identity-resolution mechanism where supported, and surface unresolved mappings without introducing an automatic profile-reconciliation write.

### 5. Play now, research next, and evidence

The “Play now” view must display the active constraints alongside the recommendation. With no target, follow the existing highest-ready-frontier policy. With a target, use existing target semantics and show a readiness-passing lower-BR fallback only when the corrected service contract permits it. If neither exists, explain the blockers; do not substitute the least-bad unready result as a recommendation.

The “Research next” view consumes existing one-step unlock evaluations. Group or label results by immediate adoption, opening a new ready frontier, improving an existing frontier, filling a missing role, or providing a stronger backup. An unlock may have multiple labels. Show current status, prerequisite evidence, resulting BR/readiness, and current/expanded/forced-lineup distinctions when returned. Explain when a vehicle enables future progress but is not adopted immediately. Use deterministic ordering without creating a new universal research score.

Provide expandable “Why?” details and a compact data-health view: selected evidence components, provider/source references, freshness, coverage gaps, conflicts, and scoring rules. Summarize critical limitations near the recommendation and retain full detail on demand. Operational statistics remaining unavailable must not block M3.

If an ownership update or evidence refresh changes results, explain the material changes using comparable stored/current inputs. Capture one consistent profile/evidence context per evaluation and never combine results from different snapshots into an apparently single comparison. Mark previously displayed results stale when their inputs change; require or perform an explicit, visible refresh without mutating presets.

## Verification and acceptance

Use a dedicated test profile/database for mutations. Do not overwrite ignored live databases, captured exports, or the user’s current garage. Preserve all existing M1/M2 regression tests and documented acceptance commands. Add focused tests at the service boundaries and a small number of end-to-end workflow tests; avoid duplicating the scoring suite in the frontend.

| Gate | Required evidence |
| --- | --- |
| Baseline compatibility | Frozen M1/M2 acceptance remains green; unchanged unconstrained requests preserve their expected results/IDs. |
| Migration/persistence | Fresh installation and upgrade from the current M2 schema succeed; existing profile/evidence data survives; presets/context survive process restart. |
| Preset lifecycle | Create, load, rename, update, and delete work; name/capacity/reference errors are clear; deleting the selected preset clears only that selection. |
| Concurrent edits | Two clients editing the same revision produce one success and one explicit conflict with no partial writes. |
| Builder constraints | Multiple pins and exclusions are honored; contradictions and unavailable ownership fail clearly; defaults remain unchanged. |
| Hypothetical isolation | Before/after complete garage state and revision match following hypothetical evaluation and saving/loading a hypothetical draft. |
| Readiness/fallback | An isolated high-BR vehicle cannot create a false ready recommendation; fallback is strictly lower and passing or is null. Include no-fallback coverage. |
| Comparisons | Same-BR deltas agree with services; cross-BR overall delta stays null; tied alternatives carry no unsupported superiority claim. |
| Changed evidence | A controlled BR/evidence or ownership change marks stored evaluation stale, preserves preset intent, and explains affected slots on reevaluation. |
| Missing evidence | Missing/conflicted capabilities, missing statistics, and incomplete research graph retain M2 semantics and readable UI states. |
| Transport parity | Identical effective inputs/profile revision/evidence yield the same domain result via CLI, MCP, and dashboard API; compare domain payloads, allowing documented transport metadata. |
| UI workflow | Garage edit → refreshed recommendation → draft lineup → comparison → save → reload after restart works in a browser. Verify errors, keyboard access, and stale-write recovery. |

Use established USA scenario fixtures where possible: newly owned M3 Lee; M5A1/M3 Lee/M16; isolated higher-BR vehicle; mature 3.3; mature 3.7/4.0 with M24. Treat their vehicle BRs as fixture/snapshot values, not permanently current game facts. Acceptance asserts domain behavior rather than arbitrary UI ordering or exact new preset IDs.

Run applicable backend tests, frozen acceptance, Ruff, strict mypy, frontend type/build checks, and focused browser tests. Add a discoverable M3 acceptance entry point consistent with the existing CLI convention. Preserve project coverage requirements; report actual results and environment limitations honestly.

## Windows dashboard acceptance checklist to deliver

1. Start the application using the documented command against an isolated acceptance database; record the active profile revision, ruleset, and evidence bundle.
2. Open the dashboard and confirm its garage matches CLI/MCP state.
3. Change one test vehicle’s status explicitly; verify all interfaces reflect the saved state and explain recommendation changes under the same evidence bundle.
4. Build a five-slot lineup, pin two choices, exclude another, and generate remaining slots. Verify the constraints and readiness explanations.
5. Save a named preset, compare a same-BR alternative and a different-BR alternative, and check delta semantics.
6. Run hypothetical ownership and verify the saved garage/revision remains unchanged.
7. Inspect one-step research benefits and prerequisite/data-gap explanations.
8. Restart the application; retrieve the preset and context from dashboard, CLI, and MCP.
9. Exercise a stale edit from two clients and a controlled evidence-change scenario on test data.
10. Export a concise acceptance report with commands, observed identities/revisions, results, and any checks still pending. Do not declare Windows dashboard acceptance complete if only container tests ran.

## Documentation and completion report

Update architecture, user/setup documentation, CLI/MCP usage, migration notes, and test instructions. Explain preset versus garage state, target/pin/exclusion semantics, hypothetical mode, stale results, graph limitations, cross-BR comparison rules, and the dashboard’s local-only startup.

On completion, report what changed, how to launch it on Windows, schema/API additions, verification results, remaining limitations, and the exact manual acceptance steps. Deliver a working vertical slice; defer extras beyond this scope without weakening M1/M2 guarantees.
