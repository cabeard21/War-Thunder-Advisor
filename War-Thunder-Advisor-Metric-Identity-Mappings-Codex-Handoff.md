# Codex handoff: Fix community metric and vehicle identity mappings

## Goal

Implement practical fixes that recover usable statistics already present in the community dataset but lost through parsing, metric mapping, or vehicle identity joins. Use the existing repository, historically `D:\Repos\war_thunder_advisor`, and follow its `AGENTS.md` and conventions.

This is a personal hobby project. Work autonomously, use simple documented defaults, and require no manual vehicle audits, screenshot collection, or preference questionnaires. Complete the implementation and reprocess real data; do not stop at another plan or diagnostic framework. Do not promise coverage increases where the source genuinely lacks values.

## Current behavior to preserve

The advisor already imports WT Data Project statistics and scores eligible community RB ground-vehicle records as a lower-confidence proxy. Preserve:

- Actual `realistic_all_contexts` scope and source provenance.
- Verified Ground RB precedence, with no double counting.
- Existing 90-day age limit, positive battle count, five compatible peers per metric, and sample-size adjustment.
- Proxy attenuation: halve the adjusted metric's deviation from neutral 50.
- Existing metric/component weights, BR behavior, readiness rules, and ownership rules.
- Missing values as unknown and acceptance fixtures excluded from operational scoring.
- Immutable stored evaluations and existing staleness behavior.

Do not expand this task into new providers, capability enrichment, community-guide collection, cross-nation peer expansion, or scoring-weight tuning. A targeted schema/metric-definition change is in scope if needed to represent the source accurately.

## Observed baseline — September 21, 2026

Reproduce against the current database; these observations may have changed.

- Active statistics snapshot: `community-statistics-20260920-1a02525ff097`.
- Provider: `wt_data_project_thunderskill_wiki_join`.
- Reported date: `2026-09-20`; stored sample start was null. Do not infer an observation window from this handoff.
- 141 imported rows; 120 eligible catalog vehicles. Priority scope had 22 vehicles and 10 eligible vehicles.
- All 10 eligible priority vehicles lacked `kd`.
- `us_m4a1`: 79 battles, reported win rate `0.7143`, reported kills per battle `3.71`, reported K/D null; eligible proxy.
- `us_m3_stuart`: 69 battles but all three reported scoring metrics null; reason `missing_metrics`.
- `us_m3_gmc`: no statistics row.
- `us_m3a1_stuart`: excluded for insufficient compatible peers.
- Other priority vehicles with missing rows included `us_m2a2`, `us_m2a4_first_tank_div`, and `us_m7`. Treat these as diagnostic leads, not proof that a matching source row exists or that the profile is free of identity issues.

The upstream README documents `rb_battles`, `rb_win_rate`, `rb_ground_frags_per_battle`, and `rb_ground_frags_per_death`, plus source `name` and `alt_name`. Its joined dataset adds Wiki metadata and explicitly warns that joins are imperfect. A documented column does not establish that any particular downloaded row has a non-null value.

Source: https://github.com/ControlNet/wt-data-project.data

## 1. Inspect the actual pipeline and retained source

Confirm the configured database/profile and installed package without discarding unrelated worktree changes. Trace source CSV -> parsing -> identity resolution -> stored observations -> scoring selection -> CLI/MCP/dashboard output.

Start with retained raw CSV/cache corresponding to the selected snapshot. If unavailable, obtain the matching public repository revision where practical; otherwise use the latest accessible dataset and clearly separate source changes from parser changes. Cache the input. Do not call the vehicle API merely to reprocess statistics.

For the specific baseline vehicles, automatically compare raw source values, parsed values, selected identities, and final metric eligibility. Classify each gap as source missing, parser loss, semantic exclusion, failed join, ambiguous identity, or scoring gate. This is a developer diagnostic, not a user audit task.

## 2. Correct metric parsing and definitions

Inspect the actual columns and current mapping code. Recover values only when present or legitimately derivable from source counts.

| Source field | Intended meaning |
| --- | --- |
| `rb_battles` | Reported RB battle count |
| `rb_win_rate` | Reported RB win rate; inspect source units and normalize once |
| `rb_ground_frags_per_battle` | Ground-target kills per battle |
| `rb_ground_frags_per_death` | Ground-target kills per death |

Requirements:

- Preserve legitimate numeric zero. Distinguish empty/NA/null from zero; reject nonfinite or nonsensical values. Do not use truthiness to select numeric fields.
- Confirm units at the provider/column level. Do not guess percentages versus fractions separately for every row.
- Do not calculate K/D from battle count and kills per battle. Use the reported ratio or actual kill/death totals when available. Never manufacture underlying counts from rounded ratios.
- Check whether omission of ground-kills-per-death was intentional because `kd` previously meant all-target K/D. If so, represent the ground-target definition explicitly rather than silently changing the meaning of old records.
- Prefer the smallest compatible implementation: reuse existing metric-definition/provenance support when sufficient; add a targeted field or schema migration only if necessary. Ground-target metrics must compare with peers using the same definition. Do not combine air and ground ratios with unmatched or unknown denominators.
- Show human-readable labels such as “ground kills/death” and “ground kills/battle” wherever these values are exposed. Preserve original source field names in provenance.
- Reject an invalid metric independently where possible, retaining other valid metrics on the same correctly identified row. Report exclusions at the appropriate metric or row level.
- Keep five-peer eligibility metric-specific: missing K/D or insufficient K/D peers must not disable valid win-rate or kills-per-battle evidence.

## 3. Repair vehicle identity mappings

Use canonical vehicle IDs, existing `source_vehicle_id` mappings, and source-specific aliases. Start with `us_m3_gmc` and the other missing-row leads. Inspect the upstream raw ThunderSkill data (`ts`) if the joined data (`joined`) drops or mislabels a recoverable row.

- Confirm mappings automatically from exact source IDs and compatible vehicle metadata. Add aliases only for actual matches, with a brief reason/source.
- Keep premium, event, national, and equipment variants distinct. Similar names alone must not cause merges; fuzzy matching can suggest candidates but must not silently establish identity.
- If a raw source row can be identified exactly, join it to the advisor's canonical vehicle metadata rather than requiring an unreliable upstream display-name join to succeed.
- Deduplicate when multiple aliases or joined/raw rows resolve to the same observation. Conflicting source rows must not be silently combined or counted as additional battles.
- Keep genuinely ambiguous or absent matches excluded, with concise reasons. Do not mutate user ownership/progress to work around a statistics alias problem.

## 4. Reprocess and activate corrected observations

Use one retained source input for a before/after comparison. Reprocess it through the fixed importer on a validation copy first, then apply the normal import/publication workflow to the retained database after a SQLite-consistent backup.

Ensure snapshot identity includes the normalization/mapping revision as needed. The same source checksum with corrected interpretation must not incorrectly reuse the old normalized snapshot. Repeating the same corrected import must be idempotent.

Publish new immutable evidence, preserve the original source date and scope, and leave old snapshots/evaluations untouched. Advance the relevant evidence or ruleset revision so stored-result staleness works. Do not reset source age to the reimport date.

Use the existing shared service for CLI, dashboard, and MCP. Make sure normal future refreshes retain the fixed mappings. Document any service restart needed to select the new bundle; distinguish installed/live verification from source-checkout tests.

## 5. Focused verification and delivery

Add regressions for legitimate zero, missing values, units, ground-target ratio semantics, valid metrics surviving a bad metric, per-metric peer eligibility, aliases/variant separation, raw-versus-joined recovery, deduplication, and reimport identity/idempotency. Use small permitted source excerpts or synthetic fixtures clearly marked as test data. Do not commit the user's database or bulk source dataset.

Run the repository's required quality gates and relevant interface checks. Preserve existing user state and frozen evidence unless an intentional versioned change requires a documented expectation update.

Report a compact before/after comparison using the same source input and fixed lineup:

- Mapped vehicles and eligible vehicles, catalog and priority scope.
- Usable win-rate, ground-kills/battle, and ground-kills/death counts.
- What happened to M3 GMC, M3 Stuart, and M3A1 Stuart, with specific reasons.
- Statistical component and overall score changes for one current owned lineup.
- Remaining gaps separated into source absence, identity ambiguity, and scoring eligibility.
- Tests run and whether the installed service has actually loaded the change.

Success means source values and identities are handled correctly and recoverable evidence reaches new evaluations. A score/ranking change, complete coverage, or a usable row for every named vehicle is not required. Finish with the concrete outcome and any exact install/restart command still needed. No further planning cycle or manual auditing from the user.
