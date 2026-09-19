# Milestone 2 Advisor-Quality Acceptance

Milestone 2 adds a network-free, property-based gate over six USA Ground RB progression states:

- Rank I current state
- M3 Lee newly owned
- Coherent M5A1/M3 Lee/M16 2.7 pool
- One isolated higher-BR vehicle
- Mature 3.3 pool
- Mature 3.7 pool with M24 capability evidence

The gate checks useful properties rather than pinning opaque lineup IDs: readiness, inaccessible
vehicle exclusion, lower-BR alternatives, cross-BR labels, premature-escalation resistance,
specialist redundancy diagnostics, role diversity, and explicit M24 scouting/stabilizer evidence.

Run the generated report with:

```powershell
wt-advisor acceptance --milestone 2
wt-advisor acceptance --milestone 2 --json
```

The fixture records expected properties and provenance notes in
`src/wt_advisor/data/fixtures/usa_ground_rb_m2_scenarios.json`. Milestone 1 remains separately
reproducible with `wt-advisor acceptance --milestone 1`.
