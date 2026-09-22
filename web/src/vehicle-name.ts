import type { Vehicle } from "./types";

const knownNames: Record<string, string> = {
  us_m2a4_1st: "M2A4 (reserve section)",
  us_m2a4_first_tank_div: "M2A4 (1st Arm.Div.)",
  us_m3_gmc: "M3 GMC",
  us_m13_mgmc: "M13 MGMC",
  us_m15_cgmc: "M15 CGMC",
  us_m16_mgmc: "M16 MGMC",
  us_m22: "M22 Locust",
  us_m24: "M24 Chaffee",
  us_m4a3_105: "M4A3 (105)",
};

const acronyms = new Set(["adats", "ags", "ccvl", "efv", "gmc", "hstv", "itv", "lav", "losat", "mgmc", "mgs", "nasams", "rdf", "sdi", "spaa", "usmc"]);

function isInternalName(value: string): boolean {
  return /^[a-z0-9]+(?:_[a-z0-9]+)+$/.test(value);
}

function readableToken(token: string): string {
  if (acronyms.has(token)) return token.toUpperCase();
  if (/^[a-z]{1,4}\d/.test(token)) return token.toUpperCase();
  return token.charAt(0).toUpperCase() + token.slice(1);
}

export function vehicleName(vehicle: Vehicle): string {
  const sourceName = vehicle.name?.trim();
  if (sourceName && !isInternalName(sourceName)) return sourceName;
  const known = knownNames[vehicle.vehicle_id];
  if (known) return known;
  const source = sourceName || vehicle.vehicle_id;
  const parts = source.replace(/^us_/, "").split("_").filter(Boolean);
  return parts.length ? parts.map(readableToken).join(" ") : vehicle.vehicle_id;
}
