import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("dashboard loading", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps vehicles visible when recommendation and research requests fail", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/evaluate") || url.endsWith("/unlock-evaluations")) {
        return { ok: false, status: 503, json: async () => ({ error: { code: "unavailable", message: "Evidence unavailable" } }) };
      }
      const data = url.endsWith("/vehicles") ? [{ vehicle_id: "m3", name: "M3 Lee" }]
        : url.endsWith("/progress") ? { profile: { crew_slots: 2 }, vehicle_statuses: { m3: "owned" } }
        : url.endsWith("/presets") ? [] : {};
      return { ok: true, json: async () => ({ data }) };
    }));

    const state = await api.dashboard("acceptance");
    const insights = await api.dashboardInsights("acceptance");
    expect(state.vehicles).toEqual([{ vehicle_id: "m3", name: "M3 Lee", status: "owned" }]);
    expect(insights.load_errors).toEqual(expect.arrayContaining([expect.stringContaining("recommendation"), expect.stringContaining("research")]));
  });

  it("returns the vehicle catalog while slow insight requests remain pending", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/evaluate") || url.endsWith("/unlock-evaluations")) return new Promise(() => {});
      const data = url.endsWith("/vehicles") ? [{ vehicle_id: "m3", name: "M3 Lee" }]
        : url.endsWith("/progress") ? { profile: { crew_slots: 2 }, vehicle_statuses: {} }
        : url.endsWith("/presets") ? [] : {};
      return { ok: true, json: async () => ({ data }) };
    }));
    expect((await api.dashboard("acceptance")).vehicles).toHaveLength(1);
  });
});
