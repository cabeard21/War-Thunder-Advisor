import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdvisorApp } from "./app";
import { api, AdvisorApiError } from "./api";

vi.mock("./api", () => ({
  AdvisorApiError: class extends Error {
    code: string;
    status?: number;
    constructor(error: { code: string; message: string; status?: number }) {
      super(error.message); this.code = error.code; this.status = error.status;
    }
  },
  displayError: () => "failed",
  api: {
    dashboard: vi.fn().mockResolvedValue({ profile: { crew_slots: 2, revision: 1 }, context: { revision: "1" }, vehicles: [{ vehicle_id: "m3", name: "M3 Lee", status: "owned", battle_rating: 27 }], presets: [{ preset_id: "p1", name: "Old name", revision: "1", slots: ["m3"] }], play_now: {} }),
    advisor: vi.fn().mockResolvedValue({ status: "current", snapshot: null, stale_reasons: [], error: null }),
    refreshAdvisor: vi.fn().mockResolvedValue({ status: "computing", snapshot: null, stale_reasons: [], error: null }),
    updateContext: vi.fn(), updateStatus: vi.fn(), evaluate: vi.fn(), createPreset: vi.fn(), updatePreset: vi.fn(), deletePreset: vi.fn(), compare: vi.fn(), refreshCommunityEvidence: vi.fn(),
  },
}));

describe("field console", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("explains the lineup in player terms and hides raw reason codes behind details", async () => {
    vi.mocked(api.advisor).mockResolvedValueOnce({
      status: "current", stale_reasons: [], error: null,
      snapshot: {
        recommended_br: 30, blockers: [], data_gaps: [], research_priorities: [],
        alternative_lineups: [],
        preference_transparency: {
          preference_changed_selection: false, objective_first_choice: null,
          candidate_pool_size: 7, unsatisfiable_preferred_roles: ["tank_destroyer"],
          unmet_preferred_roles: [], minimum_duplicate_role_pairs: 2,
          duplicate_roles_unavoidable: true,
        },
        primary_lineup: {
          analysis: { readiness_passed: true, lineup_br: 30, lineup: { slots: ["m3"] }, rules: [] },
          reasons: ["HIGHEST_READY_BR", "OBJECTIVE_SCORE_RANK", "STRENGTH_BR_COHESION"],
          preference_factors: {
            preferred_roles_configured: ["tank_destroyer"], preferred_roles_covered: [],
            preferred_roles_missing: ["tank_destroyer"], role_satisfaction: 0, role_component: 0,
            duplicate_role_pairs: 2, duplicate_role_penalty: 10, duplicate_strength: 1,
            duplicate_load: 0.667, duplicate_component: -1, preference_adjustment: -1,
            objective_score: 79.74, recommendation_score: 78.74, role_cap: 1.5,
            duplicate_cap: 1.5, preference_cap: 1.5, max_preference_swing: 3,
          },
          explanation: {
            research_pointer: "m3",
            strengths: ["The vehicles sit closely together in battle rating."],
            tradeoffs: [
              "No ready lineup at this battle rating can include your preferred tank destroyer, so no preference setting would change that.",
              "Researching m3 is the ranked path towards changing that.",
            ],
            warnings: [],
            evidence_summary: { summary: "Community performance data is available for all 5 vehicles.", vehicle_count: 5, eligible_count: 5, proxy_count: 5 },
          },
        },
      },
    });

    render(<AdvisorApp />);

    // Player-facing wording leads the card.
    expect(await screen.findByText(/vehicles sit closely together/, { selector: ".advisor-strengths li" })).toBeInTheDocument();
    expect(screen.getByText(/can include your preferred tank destroyer/, { selector: ".advisor-tradeoffs li" })).toBeInTheDocument();
    expect(screen.getByText(/available for all 5 vehicles/, { selector: ".advisor-evidence-summary" })).toBeInTheDocument();
    // The research pointer is re-rendered with the readable vehicle name.
    expect(screen.getByText(/Researching M3 Lee/, { selector: ".advisor-tradeoffs li" })).toBeInTheDocument();
    // Raw codes survive, but only inside the details view.
    const codes = screen.getByText(/HIGHEST_READY_BR/, { selector: ".reason-codes" });
    expect(codes.closest("details")).not.toBeNull();
  });

  it("never presents an unmet preference as a strength or a blocker", async () => {
    vi.mocked(api.advisor).mockResolvedValueOnce({
      status: "current", stale_reasons: [], error: null,
      snapshot: {
        recommended_br: 30, blockers: [], data_gaps: [], research_priorities: [],
        alternative_lineups: [],
        primary_lineup: {
          analysis: { readiness_passed: true, lineup_br: 30, lineup: { slots: ["m3"] }, rules: [] },
          reasons: [],
          explanation: {
            research_pointer: null,
            strengths: ["It includes dedicated anti-air cover."],
            tradeoffs: ["No ready lineup at this battle rating can include your preferred tank destroyer, so no preference setting would change that."],
            warnings: [],
            evidence_summary: { summary: "No community performance data is attached to this lineup.", vehicle_count: 0, eligible_count: 0, proxy_count: 0 },
          },
        },
      },
    });

    render(<AdvisorApp />);

    const tradeoff = await screen.findByText(/preferred tank destroyer/, { selector: ".advisor-tradeoffs li" });
    expect(tradeoff.closest("ul")?.className).toContain("advisor-tradeoffs");
    expect(screen.queryByText("Readiness blockers")).toBeNull();
    expect(screen.queryByText(/preferred tank destroyer/, { selector: ".advisor-strengths li" })).toBeNull();
    expect(screen.queryByText(/preferred tank destroyer/, { selector: ".advisor-warnings li" })).toBeNull();
    const strengths = screen.getByText(/dedicated anti-air cover/, { selector: ".advisor-strengths li" });
    expect(strengths.closest("ul")?.className).toContain("advisor-strengths");
  });

  it("shows the preference calculation so the player can see whether preferences mattered", async () => {
    vi.mocked(api.advisor).mockResolvedValueOnce({
      status: "current", stale_reasons: [], error: null,
      snapshot: {
        recommended_br: 30, blockers: [], data_gaps: [], research_priorities: [],
        alternative_lineups: [],
        preference_transparency: {
          preference_changed_selection: true,
          objective_first_choice: { slots: ["m3"], objective_score: 80.5 },
          candidate_pool_size: 21, unsatisfiable_preferred_roles: [],
          unmet_preferred_roles: [], minimum_duplicate_role_pairs: 0,
          duplicate_roles_unavoidable: false,
        },
        primary_lineup: {
          analysis: { readiness_passed: true, lineup_br: 30, lineup: { slots: ["m3"] }, rules: [] },
          reasons: [],
          preference_factors: {
            preferred_roles_configured: ["tank_destroyer"], preferred_roles_covered: ["tank_destroyer"],
            preferred_roles_missing: [], role_satisfaction: 1, role_component: 1.5,
            duplicate_role_pairs: 0, duplicate_role_penalty: 10, duplicate_strength: 1,
            duplicate_load: 0, duplicate_component: 0, preference_adjustment: 1.5,
            objective_score: 79.7, recommendation_score: 81.2, role_cap: 1.5,
            duplicate_cap: 1.5, preference_cap: 1.5, max_preference_swing: 3,
          },
          explanation: { research_pointer: null, strengths: [], tradeoffs: [], warnings: [], evidence_summary: { summary: "x", vehicle_count: 0, eligible_count: 0, proxy_count: 0 } },
        },
      },
    });

    render(<AdvisorApp />);

    expect(await screen.findByText(/Did preferences change the recommendation\? Yes/)).toBeInTheDocument();
    expect(screen.getByText(/Most preferences can ever overcome: 3 points/)).toBeInTheDocument();
    expect(screen.getByText(/Ready lineups compared at this BR: 21/)).toBeInTheDocument();
  });

  it("keeps builder edits visibly local until applied", async () => {
    render(<AdvisorApp />);
    expect(await screen.findByText("M3 Lee", { selector: ".vehicle strong" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Draft" }));
    expect(screen.getByText(/1\s*\/\s*2\s*draft slots/)).toBeInTheDocument();
    expect(screen.getByText("M3 Lee", { selector: "li" })).toBeInTheDocument();
  });

  it("shows vehicles alongside a secondary-load warning", async () => {
    vi.mocked(api.advisor).mockRejectedValueOnce(new Error("Evidence unavailable"));
    vi.mocked(api.dashboard).mockResolvedValueOnce({
      profile: { crew_slots: 2 }, context: {}, presets: [],
      vehicles: [{ vehicle_id: "m3", name: "M3 Lee", status: "owned" }],
    });
    render(<AdvisorApp />);
    expect(await screen.findByText("M3 Lee", { selector: ".vehicle strong" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("failed");
  });

  it("renders vehicles before an insight request settles", async () => {
    vi.mocked(api.advisor).mockImplementationOnce(() => new Promise(() => {}));
    render(<AdvisorApp />);
    expect(await screen.findByText("M3 Lee", { selector: ".vehicle strong" })).toBeInTheDocument();
  });

  it("shows stale snapshot and starts nonblocking refresh", async () => {
    vi.mocked(api.advisor).mockResolvedValueOnce({ status: "stale", stale_reasons: ["garage_changed"], error: null, snapshot: {
      primary_lineup: { analysis: { lineup: { slots: ["m3"] }, readiness_passed: true, lineup_br: 27 }, reasons: ["ready_frontier"] },
      alternative_lineups: [], research_priorities: [], blockers: [], data_gaps: [], recommended_br: 27,
    } });
    render(<AdvisorApp />);
    expect(await screen.findByText(/garage changed/i)).toBeInTheDocument();
    expect(screen.getByText("M3 Lee", { selector: ".advisor-lineup" })).toBeInTheDocument();
    expect(api.refreshAdvisor).toHaveBeenCalledWith("acceptance");
  });

  it("shows failed computation with a retry while retaining old advice", async () => {
    vi.mocked(api.advisor).mockResolvedValueOnce({ status: "failed", stale_reasons: ["evidence_changed"], error: "Evidence unavailable", snapshot: {
      primary_lineup: { analysis: { lineup: { slots: ["m3"] }, readiness_passed: true }, reasons: [] },
      alternative_lineups: [], research_priorities: [], blockers: [], data_gaps: [], recommended_br: 27,
    } });
    render(<AdvisorApp />);
    expect(await screen.findByText(/Evidence unavailable/)).toBeInTheDocument();
    expect(screen.getByText("M3 Lee", { selector: ".advisor-lineup" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry advisor" }));
    await waitFor(() => expect(api.refreshAdvisor).toHaveBeenCalledWith("acceptance"));
  });

  it("shows vehicle names in saved and recommended lineups", async () => {
    vi.mocked(api.advisor).mockResolvedValueOnce({ status: "current", stale_reasons: [], error: null, snapshot: {
      primary_lineup: { analysis: { lineup: { slots: ["us_m3_lee"] }, readiness_passed: true }, reasons: [] },
      alternative_lineups: [], research_priorities: [], blockers: [], data_gaps: [], recommended_br: 27,
    } });
    vi.mocked(api.dashboard).mockResolvedValueOnce({
      profile: { crew_slots: 2, revision: 1 }, context: { revision: "1" },
      vehicles: [{ vehicle_id: "us_m3_lee", name: "us_m3_lee", status: "owned", battle_rating: 27 }],
      presets: [{ preset_id: "p1", name: "Favorite", revision: "1", slots: ["us_m3_lee"] }],
      play_now: { analysis: { lineup: { slots: ["us_m3_lee"] } } },
    });
    render(<AdvisorApp />);
    expect(await screen.findByText("M3 Lee", { selector: ".advisor-lineup" })).toBeInTheDocument();
    expect(screen.getByText("M3 Lee", { selector: ".vehicle strong" })).toBeInTheDocument();
    expect(screen.getByText("M3 Lee", { selector: ".presets small" })).toBeInTheDocument();
    expect(screen.queryByText("us_m3_lee")).not.toBeInTheDocument();
  });

  it("loads a preset into an explicit edit form before saving changes", async () => {
    const state = {
      profile: { crew_slots: 2, revision: 1 }, context: { revision: "1" },
      vehicles: [{ vehicle_id: "m3", name: "M3 Lee", status: "owned", battle_rating: 27 }],
      presets: [{ preset_id: "p1", name: "Old name", revision: "1", slots: ["m3"] }],
      play_now: {},
    };
    vi.mocked(api.dashboard)
      .mockResolvedValueOnce(state)
      .mockResolvedValueOnce({
        ...state,
        presets: [{ preset_id: "p1", name: "Renamed", revision: "2", slots: ["m3"] }],
      });
    vi.mocked(api.updatePreset).mockResolvedValueOnce({
      preset_id: "p1", name: "Renamed", revision: "2", slots: ["m3"],
    });
    render(<AdvisorApp />);
    await screen.findByText("Old name");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const input = screen.getByRole("textbox", { name: "Preset name" });
    expect(input).toHaveValue("Old name");
    fireEvent.change(input, { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Preset updated.")).toBeInTheDocument();
    expect(screen.getByText("Renamed", { selector: ".presets strong" })).toBeInTheDocument();
  });

  it("retains a local draft across a dashboard refresh", async () => {
    render(<AdvisorApp />);
    await screen.findByText("M3 Lee", { selector: ".vehicle strong" });
    fireEvent.click(screen.getByRole("button", { name: "Draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2));
    expect(screen.getByText("M3 Lee", { selector: "li" })).toBeInTheDocument();
  });

  it("refreshes community evidence and reloads recommendations without losing a local draft", async () => {
    vi.mocked(api.refreshCommunityEvidence).mockResolvedValueOnce({ outcome: "updated", bundle_id: "new", accepted_statistics_rows: 12, quarantined_statistics_rows: 2, statistics_status: "usable", message: "Evidence updated." });
    render(<AdvisorApp />);
    await screen.findByText("M3 Lee", { selector: ".vehicle strong" });
    fireEvent.click(screen.getByRole("button", { name: "Draft" }));

    fireEvent.click(screen.getByRole("button", { name: "Refresh community evidence" }));

    await waitFor(() => expect(api.refreshCommunityEvidence).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2));
    expect(screen.getAllByRole("status").some((item) => /Evidence updated/.test(item.textContent ?? ""))).toBe(true);
    expect(screen.getByText("M3 Lee", { selector: "li" })).toBeInTheDocument();
  });

  it("reports a failed refresh while keeping the current dashboard visible", async () => {
    vi.mocked(api.refreshCommunityEvidence).mockResolvedValueOnce({ outcome: "failed", message: "Source unavailable." });
    render(<AdvisorApp />);
    await screen.findByText("M3 Lee", { selector: ".vehicle strong" });

    fireEvent.click(screen.getByRole("button", { name: "Refresh community evidence" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Source unavailable/);
    expect(screen.getByText("M3 Lee", { selector: ".vehicle strong" })).toBeInTheDocument();
  });

  it("labels missing evidence and the neutral scoring fallback", async () => {
    vi.mocked(api.advisor).mockResolvedValueOnce({ status: "current", stale_reasons: [], error: null, snapshot: {
      primary_lineup: null, alternative_lineups: [], research_priorities: [], blockers: [], data_gaps: [], recommended_br: null,
    } });
    vi.mocked(api.dashboard).mockResolvedValueOnce({
      profile: { crew_slots: 2, revision: 1 }, context: { revision: "1" },
      vehicles: [], presets: [], play_now: {},
      evidence_health: {
        components: {
          capabilities: { covered_count: 1, total_count: 1, field_complete_count: 0, missing_pair_count: 5 },
          global_statistics: { status: "unavailable", missing_reason: "no_compatible_statistics_snapshot" },
        },
      },
    });
    render(<AdvisorApp />);
    expect(await screen.findByText(/0 of 1 vehicles have complete scored capability evidence/)).toBeInTheDocument();
    expect(screen.getByText(/50 is a neutral composite contribution/)).toBeInTheDocument();
  });

  it("shows community proxy eligibility, actual scope, and excluded reasons", async () => {
    vi.mocked(api.advisor).mockResolvedValueOnce({ status: "current", stale_reasons: [], error: null, snapshot: {
      primary_lineup: { analysis: { lineup: { slots: ["us_m3_lee"] }, rules: [{ rule: "statistical_strength", evidence: { vehicles: {
        us_m3_lee: { eligible: true, proxy_used: true, evidence_label: "Community RB ground-vehicle proxy", source_scope: "realistic_all_contexts", source_provider: "wt_data_project",
          kd: { status: "usable", metric_definition: "ground_kills_per_death", source_field: "rb_ground_frags_per_death" },
          kills_per_battle: { status: "usable", metric_definition: "ground_kills_per_battle", source_field: "rb_ground_frags_per_battle" } },
        us_m24: { eligible: false, proxy_used: false, exclusion_reason: "no_compatible_peers" },
      } } }] }, reasons: [] }, alternative_lineups: [], research_priorities: [], blockers: [], data_gaps: [], recommended_br: 27,
    } });
    vi.mocked(api.dashboard).mockResolvedValueOnce({
      profile: { crew_slots: 2, revision: 1 }, context: { revision: "1" },
      vehicles: [{ vehicle_id: "us_m3_lee", name: "M3 Lee", battle_rating: 27 }], presets: [],
      play_now: { analysis: { lineup: { slots: ["us_m3_lee"] }, rules: [{
        rule: "statistical_strength", evidence: { vehicles: {
          us_m3_lee: { eligible: true, proxy_used: true, evidence_label: "Community RB ground-vehicle proxy", source_scope: "realistic_all_contexts", source_provider: "wt_data_project",
            kd: { status: "usable", metric_definition: "ground_kills_per_death", source_field: "rb_ground_frags_per_death" },
            kills_per_battle: { status: "usable", metric_definition: "ground_kills_per_battle", source_field: "rb_ground_frags_per_battle" } },
          us_m24: { eligible: false, proxy_used: false, exclusion_reason: "no_compatible_peers" },
        } },
      }] } },
    });
    render(<AdvisorApp />);
    expect(await screen.findByText(/M3 Lee: Community RB ground-vehicle proxy \(proxy used\)/)).toHaveTextContent("scope: realistic_all_contexts");
    expect(screen.getByText(/ground kills\/death \(rb_ground_frags_per_death\)/)).toBeInTheDocument();
    expect(screen.getByText(/ground kills\/battle \(rb_ground_frags_per_battle\)/)).toBeInTheDocument();
    expect(screen.getByText(/us_m24: statistics excluded/)).toHaveTextContent("reason: no_compatible_peers");
  });

  it("selects a preset as the active saved context explicitly", async () => {
    render(<AdvisorApp />);
    await screen.findByText("Old name");
    fireEvent.click(screen.getByRole("button", { name: "Select context" }));
    await waitFor(() => expect(api.updateContext).toHaveBeenCalledWith("acceptance", expect.objectContaining({
      expected_revision: "1",
      selected_preset_id: "p1",
    })));
  });

  it("saves ordered role preferences and duplicate penalty with constraints", async () => {
    vi.mocked(api.dashboard).mockResolvedValueOnce({
      profile: { crew_slots: 2 }, context: { revision: "1", preferred_roles: ["scout"], duplicate_role_penalty: 2 },
      vehicles: [], presets: [],
    });
    vi.mocked(api.updateContext).mockResolvedValueOnce({});
    render(<AdvisorApp />);
    const roles = await screen.findByRole("textbox", { name: "Preferred roles, in order" });
    expect(roles).toHaveValue("scout");
    expect(roles).toHaveAttribute("placeholder", "tank_destroyer, spaa");
    fireEvent.change(roles, { target: { value: "support, scout" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Duplicate-role penalty" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply constraints" }));
    await waitFor(() => expect(api.updateContext).toHaveBeenCalledWith("acceptance", expect.objectContaining({
      preferred_roles: ["support", "scout"], duplicate_role_penalty: 4,
    })));
  });

  it("preserves a conflicted status edit and retries it against refreshed state", async () => {
    vi.mocked(api.dashboard)
      .mockResolvedValueOnce({ profile: { crew_slots: 2, revision: 1 }, context: { revision: "1" }, vehicles: [{ vehicle_id: "m3", name: "M3 Lee", status: "owned", battle_rating: 27 }], presets: [], play_now: {} })
      .mockResolvedValueOnce({ profile: { crew_slots: 2, revision: 2 }, context: { revision: "1" }, vehicles: [{ vehicle_id: "m3", name: "M3 Lee", status: "owned", battle_rating: 27 }], presets: [], play_now: {} })
      .mockResolvedValueOnce({ profile: { crew_slots: 2, revision: 3 }, context: { revision: "1" }, vehicles: [{ vehicle_id: "m3", name: "M3 Lee", status: "researching", battle_rating: 27 }], presets: [], play_now: {} });
    vi.mocked(api.updateStatus)
      .mockRejectedValueOnce(new AdvisorApiError({ code: "conflict", message: "Refresh before saving.", status: 409 }))
      .mockResolvedValueOnce({});

    render(<AdvisorApp />);
    const status = await screen.findByRole("combobox", { name: "Status for M3 Lee" });
    fireEvent.change(status, { target: { value: "researching" } });

    const retry = await screen.findByRole("button", { name: "Retry status for M3 Lee" });
    expect(status).toHaveValue("researching");
    expect(api.updateStatus).toHaveBeenLastCalledWith("acceptance", "m3", "researching", 1);

    fireEvent.click(retry);
    await waitFor(() => expect(api.updateStatus).toHaveBeenLastCalledWith("acceptance", "m3", "researching", 2));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Retry status for M3 Lee" })).not.toBeInTheDocument());
  });
});
