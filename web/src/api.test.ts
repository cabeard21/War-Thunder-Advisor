import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("dashboard loading", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps vehicles visible when snapshot request fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/advisor")) {
        return { ok: false, status: 503, json: async () => ({ error: { code: "unavailable", message: "Evidence unavailable" } }) };
      }
      const data = url.endsWith("/vehicles") ? [{ vehicle_id: "m3", name: "M3 Lee" }]
        : url.endsWith("/progress") ? { profile: { crew_slots: 2 }, vehicle_statuses: { m3: "owned" } }
        : url.endsWith("/presets") ? [] : {};
      return { ok: true, json: async () => ({ data }) };
    }));

    const state = await api.dashboard("acceptance");
    await expect(api.advisor("acceptance")).rejects.toThrow("Evidence unavailable");
    expect(state.vehicles).toEqual([{ vehicle_id: "m3", name: "M3 Lee", status: "owned" }]);
  });

  it("returns the vehicle catalog while slow insight requests remain pending", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/advisor")) return new Promise(() => {});
      const data = url.endsWith("/vehicles") ? [{ vehicle_id: "m3", name: "M3 Lee" }]
        : url.endsWith("/progress") ? { profile: { crew_slots: 2 }, vehicle_statuses: {} }
        : url.endsWith("/presets") ? [] : {};
      return { ok: true, json: async () => ({ data }) };
    }));
    expect((await api.dashboard("acceptance")).vehicles).toHaveLength(1);
  });

  it("reads and refreshes a snapshot without legacy insight calls", async () => {
    const fetcher = vi.fn(async (_url: string, _options?: RequestInit) => ({ ok: true, json: async () => ({ data: { status: "current", snapshot: null, stale_reasons: [], error: null } }) }));
    vi.stubGlobal("fetch", fetcher);
    expect((await api.advisor("a b")).status).toBe("current");
    await api.refreshAdvisor("a b");
    expect(fetcher.mock.calls.map((call) => call[0])).toEqual(["/api/profiles/a%20b/advisor", "/api/profiles/a%20b/advisor/refresh"]);
    expect(fetcher.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "POST" }));
  });

  it("loads data health independently of the advisor snapshot", async () => {
    const fetcher = vi.fn(async (url: string) => ({ ok: true, json: async () => ({ data: url.endsWith("/data-status") ? { components: { capabilities: { total_count: 1, field_complete_count: 0, missing_pair_count: 2 } } } : url.endsWith("/vehicles") ? [] : url.endsWith("/presets") ? [] : {} }) }));
    vi.stubGlobal("fetch", fetcher);
    expect((await api.dashboard("acceptance")).evidence_health).toEqual({ components: { capabilities: { total_count: 1, field_complete_count: 0, missing_pair_count: 2 } } });
    expect(fetcher.mock.calls.some(([url]) => url.endsWith("/data-status"))).toBe(true);
  });
});
