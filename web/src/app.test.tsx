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
    dashboardInsights: vi.fn().mockResolvedValue({}),
    updateContext: vi.fn(), updateStatus: vi.fn(), evaluate: vi.fn(), createPreset: vi.fn(), updatePreset: vi.fn(), deletePreset: vi.fn(), compare: vi.fn(), refreshCommunityEvidence: vi.fn(),
  },
}));

describe("field console", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("keeps builder edits visibly local until applied", async () => {
    render(<AdvisorApp />);
    expect(await screen.findByText("M3 Lee", { selector: ".vehicle strong" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Draft" }));
    expect(screen.getByText(/1\s*\/\s*2\s*draft slots/)).toBeInTheDocument();
    expect(screen.getByText("M3 Lee", { selector: "li" })).toBeInTheDocument();
  });

  it("shows vehicles alongside a secondary-load warning", async () => {
    vi.mocked(api.dashboardInsights).mockResolvedValueOnce({ load_errors: ["Unable to load recommendation: Evidence unavailable"] });
    vi.mocked(api.dashboard).mockResolvedValueOnce({
      profile: { crew_slots: 2 }, context: {}, presets: [],
      vehicles: [{ vehicle_id: "m3", name: "M3 Lee", status: "owned" }],
    });
    render(<AdvisorApp />);
    expect(await screen.findByText("M3 Lee", { selector: ".vehicle strong" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Unable to load recommendation");
  });

  it("renders vehicles before an insight request settles", async () => {
    vi.mocked(api.dashboardInsights).mockImplementationOnce(() => new Promise(() => {}));
    render(<AdvisorApp />);
    expect(await screen.findByText("M3 Lee", { selector: ".vehicle strong" })).toBeInTheDocument();
  });

  it("shows vehicle names in saved and recommended lineups", async () => {
    vi.mocked(api.dashboard).mockResolvedValueOnce({
      profile: { crew_slots: 2, revision: 1 }, context: { revision: "1" },
      vehicles: [{ vehicle_id: "us_m3_lee", name: "us_m3_lee", status: "owned", battle_rating: 27 }],
      presets: [{ preset_id: "p1", name: "Favorite", revision: "1", slots: ["us_m3_lee"] }],
      play_now: { analysis: { lineup: { slots: ["us_m3_lee"] } } },
    });
    render(<AdvisorApp />);
    expect(await screen.findByText("M3 Lee", { selector: ".result p" })).toBeInTheDocument();
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
    expect(screen.getByRole("status")).toHaveTextContent(/Evidence updated/);
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
