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
    updateContext: vi.fn(), updateStatus: vi.fn(), evaluate: vi.fn(), createPreset: vi.fn(), updatePreset: vi.fn(), deletePreset: vi.fn(), compare: vi.fn(),
  },
}));

describe("field console", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("keeps builder edits visibly local until applied", async () => {
    render(<AdvisorApp />);
    expect(await screen.findByText("M3 Lee")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Draft" }));
    expect(screen.getByText(/1\s*\/\s*2\s*draft slots/)).toBeInTheDocument();
    expect(screen.getByText("m3", { selector: "li" })).toBeInTheDocument();
  });

  it("loads a preset into an explicit edit form before saving changes", async () => {
    render(<AdvisorApp />);
    await screen.findByText("Old name");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const input = screen.getByRole("textbox", { name: "Preset name" });
    expect(input).toHaveValue("Old name");
    fireEvent.change(input, { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Preset updated.")).toBeInTheDocument();
  });

  it("retains a local draft across a dashboard refresh", async () => {
    render(<AdvisorApp />);
    await screen.findByText("M3 Lee");
    fireEvent.click(screen.getByRole("button", { name: "Draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(api.dashboard).toHaveBeenCalledTimes(2));
    expect(screen.getByText("m3", { selector: "li" })).toBeInTheDocument();
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
