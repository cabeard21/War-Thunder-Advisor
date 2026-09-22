"""Compare installed CLI and fresh stdio MCP reads on one selected database.

The caller must back up any retained database before running this script because
service startup can apply a pending schema migration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

DIAGNOSTIC_LINEUP = [
    "us_m3_stuart", "us_m3a1_stuart", "us_m2_medium", "us_m8_scott", "us_m13_mgmc",
]


def cli_json(executable: Path, database: Path, *args: str) -> Any:
    environment = {**os.environ, "WT_ADVISOR_DB": str(database)}
    result = subprocess.run(
        [str(executable), *args, "--json"],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(result.stdout)


async def compare(database: Path, cli: Path, mcp: Path) -> dict[str, Any]:
    expected_status = cli_json(cli, database, "data", "status")
    expected_progress = cli_json(cli, database, "profile", "show")
    expected_vehicle = cli_json(cli, database, "vehicle", "show", "us_m3_stuart")
    expected_statistics = cli_json(cli, database, "vehicle", "stats", "us_m3_stuart")
    expected_analysis = cli_json(cli, database, "lineup", "analyze", *DIAGNOSTIC_LINEUP)
    parameters = StdioServerParameters(
        command=str(mcp), env={"WT_ADVISOR_DB": str(database)}, cwd=str(database.parent),
    )
    async with stdio_client(parameters) as (read_stream, write_stream), ClientSession(
        read_stream, write_stream
    ) as session:
        await session.initialize()
        names: set[str] = set()
        cursor: str | None = None
        while True:
            page = await session.list_tools(params={"cursor": cursor} if cursor else None)
            names.update(tool.name for tool in page.tools)
            cursor = page.next_cursor
            if cursor is None:
                break

        async def read(name: str, arguments: dict[str, Any]) -> Any:
            result = await session.call_tool(name, arguments)
            if result.is_error or result.structured_content is None:
                raise RuntimeError(f"MCP {name} did not return structured data")
            return result.structured_content

        actual_status = await read("get_data_status", {})
        actual_progress = await read("get_user_progress", {})
        actual_vehicle = await read("get_vehicle", {"vehicle_id": "us_m3_stuart"})
        actual_statistics = await read(
            "get_vehicle_statistics", {"vehicle_id": "us_m3_stuart"}
        )
        if isinstance(actual_statistics, dict) and set(actual_statistics) == {"result"}:
            actual_statistics = actual_statistics["result"]
        actual_analysis = await read(
            "analyze_lineup", {"vehicle_ids": DIAGNOSTIC_LINEUP}
        )
    comparisons = {
        "status": expected_status == actual_status,
        "progress": expected_progress == actual_progress,
        "vehicle": expected_vehicle == actual_vehicle,
        "statistics": expected_statistics == actual_statistics,
        "analysis": expected_analysis == actual_analysis,
    }
    return {
        "schema_revision": actual_status["schema_revision"],
        "bundle_id": actual_status["active"]["bundle_id"],
        "profile_revision": actual_progress["revision"],
        "tool_count": len(names),
        "comparisons": comparisons,
        "passed": all(comparisons.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--mcp", type=Path, required=True)
    arguments = parser.parse_args()
    if not arguments.database.is_file():
        parser.error("database file does not exist")
    report = asyncio.run(
        compare(arguments.database.resolve(), arguments.cli.resolve(), arguments.mcp.resolve())
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
