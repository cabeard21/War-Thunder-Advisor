# Milestone 1 Acceptance Report

## Scenario

USA Ground Realistic, five crew slots, applicable Rank I vehicles owned, M3 Lee researching, and premium/event/pack vehicles excluded.

## Current recommendation

- Lineup: `us_m15_cgmc`, `us_m22`, `us_m3_gmc`, `us_m3_stuart`, `us_m3a1_stuart`
- Matchmaking BR: 2.0
- Construction score: 73.92
- Readiness gates: pass

## Hypothetical M3 Lee unlock

- Forced-include lineup: `us_m15_cgmc`, `us_m22`, `us_m3_gmc`, `us_m3_lee`, `us_m3a1_stuart`
- Matchmaking BR: 2.7
- Construction score: 61.92
- Cross-BR scores directly comparable: false
- Component deltas: anti-air 0.00, backup depth -41.67, BR cohesion -42.00, role coverage +35.00, scouting 0.00, statistical strength +5.34, and uptier resilience -7.00.

The 2.7 lineup is reachable but does not pass the same depth/cohesion standard as the recommended 2.0 lineup. The scores are reported separately because lineup construction scores at different matchmaking BRs are not progression-power scores.

## Snapshot evidence

- Vehicle metadata: `fixture-usa-ground-rb-vehicles-m1`, aging, checksum `7bf900390804129590bac287458a2af1f9400626038c364160cfed256def0b03`.
- Global statistics: `fixture-usa-ground-rb-statistics-m1`, fresh, checksum `4a4e802b1836efda06f7d4489d27cb26b58cb17c611d010a4f510f6114d82f65`.
- Override revision: `m1-no-curated-corrections`.
- Database schema revision: `0001`.

## Statistics and additions

- Statistics are confidence-adjusted, peer-relative supporting evidence, not proof of intrinsic vehicle quality.
- Statistics are missing or unknown for `us_m24` and premium `us_m2a4_first_tank_div`; missing values remain unknown and contribute neutral evidence only inside the composite.
- Suggested additions and directly researchable unlock evaluations are emitted by the generated JSON report, including before/after warnings, component deltas, BR changes, and forced-include results.
- StatShark acquisition is not automated because no documented, permitted stable endpoint was confirmed. The validated JSON/CSV import path remains active and HTML scraping is prohibited.

## Verification payload

CLI and MCP use the same service DTOs and deterministic analysis IDs. Regenerate the complete machine-readable evidence with:

```powershell
wt-advisor acceptance --json
```
