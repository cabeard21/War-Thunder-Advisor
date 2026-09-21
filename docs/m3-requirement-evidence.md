# Milestone 3 Requirement-to-Evidence Matrix

This matrix records evidence from the current checkout on 2026-09-20. `Verified`
means an automated test or packaged-runtime command exercised the stated
contract; HTTP 200 startup checks alone are not workflow evidence.

| Requirement | Evidence | Status |
| --- | --- | --- |
| Populated M2-to-M3 source upgrade | `test_upgrade_from_populated_0004_preserves_profile_and_adds_m3_storage` upgrades one populated profile, preserves it, reaches `0005`, and creates `stored_evaluations` | Verified for this tested source-upgrade case; it is not a general data-migration proof |
| Installed-wheel 0004 upgrade | Clean external venv installed the built wheel, opened a populated `0004` database, preserved `wheel-profile`, reached `0005`, and created `stored_evaluations` | Verified separately from source Alembic migration |
| Snapshot-isolated calculation | `test_m3_evaluation_calculates_only_from_captured_transaction_snapshot` injects a later garage write after repository capture and proves the stored result used the captured inputs | Verified |
| Garage/evidence/preset staleness | Stored-result tests cover garage fingerprint, evidence/ruleset comparison, and the explicitly saved preset revision; changing selected context alone is not stale | Verified |
| Hypothetical ownership isolation | `test_m3_hypothetical_evaluation_and_saved_preset_do_not_mutate_garage` compares the complete progress DTO before/after | Verified |
| Atomic public/write revision guards | Two independent `AdvisorService` clients start from one revision; exactly one guarded mutation commits, the other conflicts, and no partial write occurs | Verified |
| Preset/context/result shared operations | CLI, MCP, and HTTP expose lifecycle, stored read, explicit reevaluation, constrained generation, and comparison; `tests/test_interfaces.py` and HTTP tests exercise the contracts | Verified |
| Constrained identity and domain outcomes | Singular/plural coexistence is rejected, legacy generation identity remains stable, and HTTP distinguishes validation, conflict, unsatisfiable constraints, and not found | Verified |
| Dashboard workflow | Vitest covers selected context, refresh-retained draft, and attempted-edit conflict retry; Playwright drives garage, constraints, evaluation, comparison, preset rename/select/load/delete, retry, and an actual process kill/restart | Verified |
| M3 acceptance isolation | `build_m3_acceptance_report` performs persistence, stored-result, and conflict mutations only in a disposable database | Verified; supplied/user database remains non-mutating |
| Supported frontend runtime | Node `22.22.2` ran TypeScript, 8 Vitest tests, Vite production build, and the real-backend Playwright workflow | Verified |
| Packaged dashboard workflow | The existing Playwright workflow launched the clean installed-wheel Python executable from an external working directory with `PYTHONPATH` removed; it exercised bundled assets, the full workflow, persistence, and an actual installed-process restart | Verified: 1/1 passed under Node 22.22.2 |
| Windows dashboard acceptance | Headed Playwright opened the clean installed-wheel dashboard on Windows and completed the 13-step local application checklist; the exported report records 12/12 PASS results and the installed process was actually restarted | Verified; report: `docs/windows-dashboard-acceptance-report-2026-09-20.txt` |

## Completed automated gates

- `python -m pytest -q --cov=wt_advisor --cov-report=term --disable-warnings`:
  202 passed, 88.57% coverage.
- `python -m ruff check src tests`: passed.
- `python -m mypy src/wt_advisor`: passed.
- `python -m wt_advisor.cli.main acceptance --milestone 1 --json`: passed.
- `python -m wt_advisor.cli.main acceptance --milestone 2`: 6/6 scenarios passed.
- `python -m wt_advisor.cli.main acceptance --milestone 3`: passed all seven checks.
- Node 22.22.2: TypeScript passed; Vitest 8/8 passed; Vite build passed.
- Node 22.22.2 Playwright: 1/1 real-backend workflow passed with process restart.
- Installed-wheel Playwright: 1/1 passed against an external clean virtual
  environment, including an installed-process restart; checkout imports were disabled.
- Headed Windows dashboard acceptance: 13/13 steps complete, 12/12 result rows
  passed; HTML report export completed.
- Current tested wheel SHA-256: `26d7b7a3989223cba1767f7b10f442f1279e0d7087fdb3818405741db45c85b7`.
- Clean installed-wheel upgrade: `version=0005`, `profile=wheel-profile`,
  `stored_evaluations=1`.
