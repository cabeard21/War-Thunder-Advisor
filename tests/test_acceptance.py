from wt_advisor.services.acceptance import build_acceptance_report, render_acceptance_markdown
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
