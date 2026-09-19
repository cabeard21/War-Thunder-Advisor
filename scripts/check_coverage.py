"""Fail when line or branch coverage falls below the independent project gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered / total * 100


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--minimum", type=float, default=80.0)
    args = parser.parse_args()
    payload: dict[str, Any] = json.loads(args.report.read_text(encoding="utf-8"))
    totals = payload["totals"]
    line = _percent(int(totals["covered_lines"]), int(totals["num_statements"]))
    branch = _percent(int(totals["covered_branches"]), int(totals["num_branches"]))
    print(f"line coverage: {line:.2f}%")
    print(f"branch coverage: {branch:.2f}%")
    return 0 if line >= args.minimum and branch >= args.minimum else 1


if __name__ == "__main__":
    raise SystemExit(main())
