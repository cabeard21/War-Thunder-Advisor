import { describe, expect, it } from "vitest";
import { vehicleName } from "./vehicle-name";

describe("vehicle display names", () => {
  it("preserves an already readable name", () => {
    expect(vehicleName({ vehicle_id: "us_m3_lee", name: "M3 Lee" })).toBe("M3 Lee");
  });

  it("replaces live provider identifiers with readable labels", () => {
    expect(vehicleName({ vehicle_id: "us_m3_lee", name: "us_m3_lee" })).toBe("M3 Lee");
    expect(vehicleName({ vehicle_id: "us_m13_mgmc", name: "us_halftrack_m13" })).toBe("M13 MGMC");
    expect(vehicleName({ vehicle_id: "us_adats_bradley", name: "us_adats_bradley" })).toBe("ADATS Bradley");
    expect(vehicleName({ vehicle_id: "sdi_minotaur", name: "sdi_minotaur" })).toBe("SDI Minotaur");
  });
});
