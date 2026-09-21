import { describe, expect, it, vi } from "vitest";
import { AdvisorApiError, displayError, request } from "./api";

describe("dashboard API boundary", () => {
  it("unwraps the common success envelope", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { revision: 4 } }), { status: 200 })));
    await expect(request<{ revision: number }>("/example")).resolves.toEqual({ revision: 4 });
  });

  it("keeps actionable structured domain errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "conflict", message: "Refresh before saving.", details: { revision: 2 } } }), { status: 409 })));
    await expect(request("/example")).rejects.toMatchObject({ code: "conflict", status: 409 });
  });

  it("does not expose an arbitrary thrown implementation error", () => {
    expect(displayError(new Error("secret trace"))).toBe("Unable to reach the local advisor. Check that the dashboard is running.");
    expect(displayError(new AdvisorApiError({ code: "validation", message: "Pin overlap", details: { vehicle_id: "m3" } }))).toContain("validation: Pin overlap");
  });
});
