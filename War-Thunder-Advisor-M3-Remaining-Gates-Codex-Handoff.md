# Milestone 3 — Completion and Acceptance Handoff

## Assignment

Continue the existing War Thunder Lineup Advisor M3 implementation. Inspect what is already implemented, finish the remaining requirements, and produce reviewable acceptance evidence. This is a continuation of **Milestone 3 — Everyday Advisor Workflow**, not a restart or a new milestone.

The foundation report does not establish full M3 completion. Treat requirements below as **unverified until inspected**, not automatically absent. Retain working code, add focused coverage for concrete gaps, and carry the authorized implementation through completion without stopping at an audit or another plan.

Read the repository’s `AGENTS.md`, original M3 handoff, approved plan, architecture, and acceptance documentation. Use this handoff to close gaps without expanding the original scope. Resolve routine implementation choices autonomously. Preserve unrelated/untracked handoffs, M2 notes, parity JSON, tunnel scripts, and live databases.

## Reported starting state

These are the implementer’s reports, not independent verification by the author of this handoff:

| Area | Reported progress |
| --- | --- |
| Persistence | Alembic 0005; durable presets/context; optimistic preset/context revisions. |
| Ownership | Optional expected-revision guards on CLI/MCP vehicle-status writes. |
| Generation | Plural hard constraints; singular/plural ambiguity rejection; legacy unconstrained and singular-required behavior preserved. |
| Dashboard | FastAPI loopback API, packaged static React/Vite dashboard, `wt-advisor dashboard`. |
| Tests | Backend preset/API/packaged-asset tests; frontend tests/typecheck/build. |
| Verification | Python reported as “187 tests reached 100% passing output”; focused M3/API tests: 5 passed; Ruff/mypy passed; 4 Vitest tests, typecheck, and production build passed. |
| Environment issue | Node 22.18 emitted engine-version warnings. |

The earlier planning baseline was 182 passing tests. Neither test count nor progress-bar completion establishes acceptance or coverage. Establish the current checkout state and record completed commands, exit codes, summaries, and coverage.

## Fixed scope and compatibility rules

- Acceptance remains USA Ground RB through approximately BR 4.0. Honor each profile’s configured crew-slot count; use five slots for the principal acceptance scenario.
- Keep M1/M2 scoring, readiness, research eligibility, unknown-evidence semantics, and diagnostic-only specialist redundancy unchanged.
- Preserve exact legacy behavior/IDs for unchanged unconstrained and singular-required requests. New constraints must have unambiguous effective-request identity.
- Cross-BR overall score deltas remain `null`. A deterministic tie-break is not evidence of vehicle superiority.
- The lower-BR fallback must pass readiness and be strictly below the applicable target/recommendation/highest evaluated frontier under the corrected service contract. Otherwise return `null`.
- Hypothetical ownership stays request-scoped. Saving a preset does not change ownership, research status, purchases, or in-game crew assignments.
- New preset/context updates and deletes require revision guards. Existing vehicle-status callers retain compatibility through an optional guard; dashboard writes supply it. Define create semantics without requiring a revision for a nonexistent row.
- Profile configuration remains displayed but read-only because the approved plan deferred mutation in the absence of a supported update service.
- Local single-user loopback application only. No public/LAN deployment or dashboard exposure through the MCP tunnel.
- No multi-step research optimizer, aircraft/CAS, crew economics, new scoring weights, new automated data sources, game capture, or built-in LLM/chat service.

## Gate 1 — Requirement-to-evidence inventory

Inspect services, repositories/migrations, generated MCP schemas, CLI help, HTTP routes, frontend flows, and current tests. Produce a compact matrix with:

`Requirement | Implementation location | Verification evidence | Status | Remaining action`

Use explicit statuses: implemented and verified, implemented but unverified, missing, or blocked. An endpoint existing is not proof that its UI workflow or persistence contract works. A mocked frontend test is not proof of database restart behavior.

Cover each subsequent gate, complete missing implementation, then update this matrix with final evidence. Do not stop after writing the matrix or replace working components merely to match an assumed architecture.

## Gate 2 — Complete the everyday dashboard workflow

Exercise and finish this vertical slice using real application services and an isolated database:

1. Display current garage state and read-only profile settings.
2. Explicitly change a test vehicle’s status with an expected revision.
3. Refresh dependent recommendations against the saved profile. Preserve unsaved builder work and explain choices invalidated by the update.
4. Build a lineup: add/remove vehicles, pin at least two, exclude another eligible vehicle, and request remaining slots. Suggestions are applied explicitly; generation must not silently overwrite the draft.
5. Compare a same-BR alternative and a different-BR alternative using the shared domain services.
6. Save, rename, update, load, and delete named presets. Verify trimmed, case-normalized profile-scoped uniqueness, empty-name errors, slot ordering, incomplete-draft handling, and capacity/duplicate/reference validation.
7. Select a preset/context explicitly, restart the application process, and recover the same durable state.

Required UI behavior:

- Clearly distinguish unsaved draft, saved intent, hypothetical inputs, stored evaluation, current evaluation, and stale evaluation.
- Show actionable validation/conflict/constraint errors, empty states, loading states, and readiness failures. An incomplete lineup must not appear complete/ready solely because its partial score is high.
- Recover from stale edits without silent force-save or loss of the attempted edit.
- Deleting the selected preset clears its selection transactionally and preserves unrelated settings/state.
- Keyboard users can reach and operate the core flow with visible focus and meaningful labels; status meaning cannot rely solely on color.

## Gate 3 — Complete shared CLI, MCP, and HTTP contracts

Verify all promised operations exist and call the same application services:

- Preset list/read/create/update/rename/delete.
- Advisor-context read/update, including selected preset, target BR, pins, and exclusions.
- Stored evaluation retrieval without recalculation.
- Existing generation, analysis/comparison, suggestions, unlock evaluation, garage state, and evidence health used by the dashboard.

Choose names consistent with the repository; avoid redundant scoring/progression tools. Inspect actual MCP schemas rather than assuming service methods are exposed. Preserve legacy response compatibility and integer-tenths BR machine inputs; display normal decimals in the UI.

Verify constraints for overlap, duplicate IDs, too many required vehicles, unknown IDs, incompatible nation/mode, unowned requirements, target incompatibility, and singular/plural ambiguity. Report structured reasons for unsatisfiable requests; never drop pins or reintroduce exclusions silently.

Parity tests must compare matching effective inputs, profile revision, and evidence bundle across CLI, MCP, and HTTP. Compare domain payloads and result identities while allowing explicitly documented transport-only metadata. Mutation parity should use equivalent isolated fixtures, not sequential writes to one revision that change the test’s starting state.

## Gate 4 — Stored evaluations, snapshot consistency, and isolation

Capture one consistent profile revision and evidence bundle for each evaluation/comparison. A profile/evidence change during a request must not produce a result assembled from mixed snapshots: complete against the captured context or return a clear retry/conflict according to the documented service contract.

Retain compact evaluation evidence using existing persistence where appropriate: effective inputs, profile revision, evidence/ruleset identity, resolved BRs, readiness, and result identity. Separate stored-result reads from explicit reevaluation; ordinary reads must not update garage, presets, or saved context.

Detect stale stored/displayed results when relevant ownership, effective constraints, evidence, or ruleset changes. Names, UI order, and unrelated presentation metadata must not alter identical analysis IDs. Mark an older result as stale without rewriting its historical inputs or presenting it as current.

On controlled evidence/ownership changes:

- Preserve preset vehicle choices and show affected slots, changed BRs, eligibility problems, or unresolved identities.
- Explain material differences on reevaluation. Do not invent historical values when a prior evaluation does not exist.
- Do not automatically replace vehicles or mutate profile reconciliation state.

Prove hypothetical isolation by comparing complete garage state and its revision before and after evaluation and saving/loading a hypothetical preset. The preset/context may change through an explicit save action; the garage must not. Loading in real-ownership mode must visibly flag unowned slots.

## Gate 5 — Play Now, Research Next, and Why/Data Health

Verify these are usable views backed by real service results, not placeholders:

| View | Required behavior |
| --- | --- |
| Play Now | Show active constraints, highest ready frontier under existing semantics, valid lower-BR fallback when available, and explicit no-recommendation blockers otherwise. |
| Research Next | Present existing one-step evaluation labels: immediate adoption, new ready frontier, existing-frontier improvement, missing role, stronger backup. Show prerequisites/current status and current/expanded/forced-lineup distinctions where returned. |
| Comparisons | Same-BR deltas match services; cross-BR overall deltas are null; show BR/readiness/role/backup differences and ties honestly. |
| Why/Data Health | Explain failing gates, provenance, selected evidence components, freshness, coverage, unknown/conflicted capabilities, missing statistics, and unavailable/incomplete graph evidence. |

Keep research ordering deterministic without inventing a universal research score or multi-step plan. Missing operational statistics must not block M3. Group labels may overlap. A newly researchable vehicle need not be immediately useful or ready for play.

## Gate 6 — Local API and installed-package behavior

Verify the approved local boundary using focused tests: loopback default binding, Host/Origin handling, no permissive CORS, bounded inputs, and structured schema/conflict/domain errors. Preserve actionable error details while withholding internal exception traces. Review any added request limits to ensure normal refresh and evaluation flows remain usable.

Build the actual distribution artifact, install it in a clean environment, and launch `wt-advisor dashboard` from outside the source checkout. Verify HTML, JavaScript, CSS, routes, and API access with the bundled frontend. A source-tree asset-existence test is insufficient. Runtime must not depend on a Vite dev server or frontend source files; document any build-time Node requirement separately.

Resolve the Node engine warning by checking actual dependency engine requirements, choosing/documenting a compatible Node version, and aligning any existing version pin/CI setup. Rerun frontend checks under that supported version. Do not suppress warnings or downgrade dependencies merely to conceal the mismatch.

## Gate 7 — Automated acceptance and migrations

Complete targeted coverage for the outstanding risks, reusing existing tests where they already prove the contract:

- Fresh database migration and upgrade from the M2 schema with existing data preserved. Audit 0005 before changing it; if it has already been applied/distributed, use an appropriate forward migration rather than assuming all databases can be rebuilt.
- Restart persistence; preset lifecycle; transactional selected-preset deletion; two-client optimistic conflicts with no partial writes.
- Constraint validation, default compatibility, and both legacy generation paths.
- Hypothetical isolation; stored-result read without reevaluation; stale detection; consistent evaluation snapshots.
- Same/cross-BR comparison and fallback cases, including isolated high-BR ownership and no valid lower fallback.
- Missing/conflicted capability evidence, absent statistics, and incomplete/unavailable research graph.
- CLI/MCP/HTTP parity and installed-package startup.
- Focused Playwright workflow with a real backend/database: garage edit → recommendation refresh → constrained draft → comparison → save → restart/reload. Add focused keyboard/error/conflict checks. Identify any mocked tests separately.

Add or finish `wt-advisor acceptance --milestone 3` consistent with existing acceptance conventions. It must exercise meaningful M3 contracts and fail nonzero on violations, not merely report that a frontend built. Document which browser/package checks are separate commands.

Run frozen M1/M2 acceptance, M3 acceptance, full pytest with the approved 80% coverage floor (or a stricter existing project requirement), Ruff, strict mypy, frontend tests/typecheck/build, and focused browser tests. Capture each command’s completed summary and exit code. If a process hangs or fails after showing 100% progress, diagnose it; do not report the progress bar as a pass.

Once the gates pass, stop optional test expansion and finish delivery. Do not change legitimate frozen outputs simply to make regressions pass.

## Gate 8 — Windows dashboard acceptance and final delivery

Update launch/setup, architecture/API/migration, and user documentation. Give exact PowerShell commands appropriate to the actual repository/package, supported Python/Node versions, isolated database/profile setup, and shutdown/restart steps. Do not assume the prior acceptance profile still matches the user’s real garage.

Provide a runnable Windows checklist covering:

1. Installed-package dashboard launch without a frontend dev server.
2. Initial garage/profile/evidence identity agreement across interfaces.
3. Explicit ownership change and recommendation refresh, retaining an unsaved draft.
4. Multiple pins, an exclusion, filling slots, and unsatisfiable-constraint diagnostics.
5. Same-BR and cross-BR comparisons; valid/null fallback behavior.
6. Preset/context persistence after an actual process restart.
7. Hypothetical garage-isolation check.
8. One-step research benefits and missing-data explanations.
9. Stale-write recovery with two clients and controlled stale-evaluation evidence.

Use isolated test data for mutations and evidence-change scenarios. Record actual Windows results if the environment supports execution. Otherwise mark **Windows dashboard acceptance pending** and provide the concrete commands/checklist; do not conflate container or mocked browser success with Windows verification.

Return a completion report containing:

- Final requirement-to-evidence matrix with source/test references and any remaining blockers.
- What changed since the foundation report and exact Windows launch instructions.
- Completed command summaries, exit codes, coverage percentage/scope, runtime versions, and acceptance results.
- Confirmation of legacy compatibility, interface parity, installed-package behavior, and hypothetical isolation with supporting evidence.
- Separate statuses for implementation completion, automated acceptance, and Windows dashboard acceptance.

M3 is complete only when the promised workflow and contracts are implemented and the required acceptance evidence is available. If a genuine environment/access blocker remains, finish all unblocked work and state precisely what is pending and how to verify it.
