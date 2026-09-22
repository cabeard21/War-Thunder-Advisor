# Operational evidence implementation and verification — 2026-09-21

## Result and evidence boundary

The retained `wt-advisor-live-acceptance.sqlite` database is now at schema
`0006`. A SQLite-consistent, integrity-checked pre-migration backup was saved as
`wt-advisor-live-acceptance-pre-0006-20260921.sqlite` and a separate validation
copy was migrated to `0006`. A read-only post-migration comparison found the
profile, 22 vehicle states, presets, context, stored evaluations, and seven
snapshot records identical to the backup. Both files are ignored by Git and
contain user state; do not commit them.

The selected operational vehicle snapshot is
`wt-vehicles-api-20260919T030556Z`; the capability snapshot is
`wt-vehicles-api-capabilities-20260919T055326249370Z`. The profile is
`acceptance-usa-ground-rb`, five slots, with 22 active vehicle states. The
only stored statistics snapshot is the synthetic acceptance fixture, so no
operational statistics snapshot is selected. No new game observations were
fabricated or imported.

| Scope | Prior record coverage | Resolved scored fields | Still unknown/conflicted |
| --- | ---: | ---: | ---: |
| USA Ground RB catalog, 161 vehicles | 161/161 | 88/1,127 | 1,039 |
| Owned and immediate-research scope, 22 vehicles | previously not shown | 40/154 | 114 |

The denominator is seven capabilities consumed by the current M2 rules:
scouting, artillery, stabilizer, vertical stabilizer, smoke, high-caliber HE,
and ATGM. None of the scoped vehicles has all seven resolved. Scouting alone
is present for 8/161 catalog vehicles and 2/22 priority vehicles; there are
zero verified-absent scouting observations. `us_m3_stuart` has sourced
artillery and vertical-stabilizer presence; scouting is unknown. The retained
source explicitly supports scouting presence for `us_m24` and `us_m5a1`, but
supplies no defensible scouting-absent example. Its 43 retained detail payloads
have no explicit `has_vertical=false`. Omission is not absence.

The diagnostic lineup remains not ready independently of evidence quality.
Its scouting and statistical-strength raw scores remain null, each with a
neutral composite contribution of 50. The current-profile recommended lineup
on the validation copy was `us_m13_mgmc`, `us_m22`, `us_m2_medium`,
`us_m3_stuart`, `us_m3a1_stuart`. New field diagnostics do not change scoring.

## Source and acceptance status

| Area | Status | Reason |
| --- | --- | --- |
| Capability ingestion route | Complete; evidence partial | Validated, provenance-stamped imports and refresh-safe resolution work; no new defensible scouting-absent claim was found. |
| Coverage diagnostics | Complete locally | Catalog/profile field counts and bounded gaps verified; fresh CLI/MCP parity passed on the retained database and dashboard parity on the validation copy. Remote tunnel acceptance is pending. |
| Statistics importer | Complete | Ground RB scope, nonfinite values, and unusable explicit denominators are rejected; fixture isolation remains. |
| Live operational statistics | Blocked | No permitted, accessible source/export with sufficient scoring metadata was verified. |

The community Vehicles API is the automated vehicle source under the existing
policy. Its retained source references are in each resolved observation.
StatShark's global-statistics page returned HTTP 403 on recheck; no documented
permitted Ground RB performance export/API was verified. The community API
documents vehicle data rather than the needed performance metrics. The local
statistics JSON/CSV importer is stricter, but **live operational statistics
acceptance is pending a real source**. A source-verified scouting-absent vehicle
also remains unavailable. Do not substitute the synthetic example or fixture.

## Verified commands and safe operational sequence

Run from the repository root in PowerShell. Use the project virtual
environment, not system Python. Before opening the retained database with new
code, make a SQLite-consistent backup and verify `integrity_check`. The backup
path below already exists from this run; choose a new filename rather than
overwriting it if repeating the procedure:

```powershell
python -c 'import sqlite3; source_db=sqlite3.connect("file:wt-advisor-live-acceptance.sqlite?mode=ro",uri=True); backup_db=sqlite3.connect("wt-advisor-live-acceptance-pre-0006-20260921.sqlite"); source_db.backup(backup_db); print(backup_db.execute("pragma integrity_check").fetchone()[0]); source_db.close(); backup_db.close()'
```

`data status` reports the active vehicle snapshot ID needed to guard an import:

```powershell
$env:WT_ADVISOR_DB = 'D:\Repos\war_thunder_advisor\wt-advisor-live-acceptance.sqlite'
.\.venv\Scripts\wt-advisor.exe data status --json
```

The [JSON example](examples/capability-import-synthetic.json) is deliberately
synthetic. Replace it with verified source-backed observations and use the
current vehicle snapshot ID. Imports are local CLI operations, not MCP tools:

```powershell
.\.venv\Scripts\wt-advisor.exe capabilities import .\verified-capabilities.json --provider verified-source --source-revision source-revision --vehicle-snapshot-id wt-vehicles-api-20260919T030556Z --json
.\.venv\Scripts\wt-advisor.exe statistics inspect .\verified-ground-rb.csv --provider verified-source --purpose operational --json
.\.venv\Scripts\wt-advisor.exe statistics import .\verified-ground-rb.csv --provider verified-source --purpose operational --json
```

The two statistics commands require a real permitted export; no such file was
available for this verification. Importing or refreshing creates new immutable
evidence and requires starting a fresh service to select it. The stopped
tunnel was **not** restarted: the safety reviewer rejected exposing the
retained-database MCP surface to `api.openai.com` without explicit
authorization for that destination and payload. Do not launch it indirectly.
Once authorized, the configured launcher command is
`.\scripts\start-mcp-tunnel.ps1 -DatabasePath
'D:\Repos\war_thunder_advisor\wt-advisor-live-acceptance.sqlite'`. Remote MCP
acceptance remains pending.

The isolated copy passed installed CLI versus fresh stdio MCP parity for data
status, profile, M3 Stuart, its empty statistics, and the diagnostic lineup;
all 20 tools were discovered. A briefly started loopback dashboard reported
the same schema `0006` and bundle ID, with 153/161 scouting fields unknown.
The reusable parity command is:

```powershell
.\.venv\Scripts\python.exe .\scripts\check_operational_evidence.py .\wt-advisor-live-acceptance.sqlite --cli .\.venv\Scripts\wt-advisor.exe --mcp .\.venv\Scripts\wt-advisor-mcp.exe
```

Verification: 233 Python tests passed at 88.66% coverage after the final
schema, diagnostic, and provenance corrections. Ruff, mypy, TypeScript,
12 Vitest tests, the Vite production build, M1/M2/M3 acceptance, and 2
real-backend Playwright tests passed. A final fresh-process CLI/MCP parity
check on the retained database also passed, with all 20 tools discovered and
the same `0006` bundle and profile revision as the validation copy.
