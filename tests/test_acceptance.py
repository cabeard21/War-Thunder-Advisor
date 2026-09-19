import json

from typer.testing import CliRunner

from wt_advisor.cli.main import create_app
from wt_advisor.services.acceptance import (
    build_acceptance_report,
    build_m2_acceptance_report,
    render_acceptance_markdown,
    render_m2_acceptance_markdown,
)
from wt_advisor.services.advisor import AdvisorService


def test_acceptance_report_covers_required_m3_lee_scenario() -> None:
    report = build_acceptance_report(AdvisorService.from_acceptance_fixture())

    assert report["profile"]["crew_slots"] == 5
    assert report["current"]["recommended"]
    assert report["after_m3_lee"]["expanded_best"]
    assert "us_m3_lee" in report["after_m3_lee"]["forced_include"]["analysis"]["lineup"]["slots"]
    assert report["after_m3_lee"]["component_deltas"]
    assert report["suggested_next_additions"]
    assert report["next_unlocks"]
    assert report["data_status"]["snapshots"]
    assert "us_m24" in report["statistics"]["missing_or_unknown"]


def test_acceptance_markdown_is_generated_from_structured_report() -> None:
    markdown = render_acceptance_markdown(
        build_acceptance_report(AdvisorService.from_acceptance_fixture())
    )

    assert "# Milestone 1 Acceptance Report" in markdown
    assert "M3 Lee" in markdown
    assert "Snapshot evidence" in markdown
    assert "StatShark" in markdown


def test_m2_acceptance_report_covers_six_quality_scenarios() -> None:
    report = build_m2_acceptance_report()

    assert report["milestone"] == 2
    assert len(report["scenarios"]) == 6
    assert all(item["passed"] for item in report["scenarios"])
    assert report["components"]["capabilities"]["status"] == "available"
    assert report["components"]["research_graph"]["status"] == "available"
    mature = next(item for item in report["scenarios"] if item["id"] == "mature_3_7")
    assert mature["properties"]["m24_scouting_present"] is True
    assert mature["properties"]["m24_vertical_stabilizer_present"] is True


def test_m2_component_freshness_matches_recommendation_evidence_context() -> None:
    report = build_m2_acceptance_report()
    component_names = ("capabilities", "availability", "research_graph")

    for scenario in report["scenarios"]:
        snapshots = {
            snapshot["snapshot_id"]: snapshot["freshness"]
            for snapshot in scenario["recommended"]["analysis"]["evidence_context"]["snapshots"]
        }
        for component_name in component_names:
            component = report["components"][component_name]
            assert snapshots[component["selected_snapshot_id"]] == component["freshness"]


def test_m2_acceptance_markdown_summarizes_property_results() -> None:
    markdown = render_m2_acceptance_markdown(build_m2_acceptance_report())

    assert "# Milestone 2 Advisor-Quality Acceptance" in markdown
    assert "6 / 6 scenarios passed" in markdown
    assert "mature_3_7" in markdown


def test_cli_selects_m2_acceptance_without_mutating_default_service() -> None:
    runner = CliRunner()
    service = AdvisorService.from_acceptance_fixture()

    result = runner.invoke(
        create_app(service),
        ["acceptance", "--milestone", "2", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["milestone"] == 2


def test_cli_rejects_unknown_data_import_source() -> None:
    result = CliRunner().invoke(
        create_app(AdvisorService.from_acceptance_fixture()),
        ["data", "import", "--source", "unknown"],
    )

    assert result.exit_code != 0
    assert "source must be acceptance or wt-api" in result.output
