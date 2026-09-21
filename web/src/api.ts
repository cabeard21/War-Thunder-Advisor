import type { ApiError, DashboardState, Json, Preset, RecordValue } from "./types";

const base = "/api";

export class AdvisorApiError extends Error implements ApiError {
  code: string;
  details?: Json;
  status?: number;

  constructor(error: ApiError) {
    super(error.message);
    this.name = "AdvisorApiError";
    this.code = error.code;
    this.details = error.details;
    this.status = error.status;
  }
}

function isRecord(value: unknown): value is RecordValue {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function unwrap(value: unknown): RecordValue | Json[] {
  if (isRecord(value) && "data" in value) return value.data as RecordValue | Json[];
  return value as RecordValue | Json[];
}

export function displayError(error: unknown): string {
  if (error instanceof AdvisorApiError) {
    const detail = error.details ? ` ${JSON.stringify(error.details)}` : "";
    return `${error.code}: ${error.message}${detail}`;
  }
  return "Unable to reach the local advisor. Check that the dashboard is running.";
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
    ...options,
  });
  const body: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const record = isRecord(body) ? body : {};
    const nested = isRecord(record.error) ? record.error : record;
    throw new AdvisorApiError({
      code: typeof nested.code === "string" ? nested.code : "request_failed",
      message: typeof nested.message === "string" ? nested.message : "The advisor rejected this request.",
      details: nested.details as Json | undefined,
      status: response.status,
    });
  }
  return unwrap(body) as T;
}

export const api = {
  dashboard: async (profileId: string): Promise<DashboardState> => {
    const [progress, context, presets, vehicles, playNow, researchNext, evidenceHealth] = await Promise.all([
      request<RecordValue>(`/profiles/${encodeURIComponent(profileId)}/progress`),
      request<RecordValue>(`/profiles/${encodeURIComponent(profileId)}/context`),
      request<Preset[]>(`/profiles/${encodeURIComponent(profileId)}/presets`),
      request<RecordValue | Json[]>("/vehicles"),
      request<RecordValue>("/evaluate", { method: "POST", body: JSON.stringify({ profile_id: profileId }) }),
      request<RecordValue | Json[]>(`/profiles/${encodeURIComponent(profileId)}/unlock-evaluations`),
      request<RecordValue>("/data-status"),
    ]);
    const statusByVehicle = (progress.vehicle_statuses ?? {}) as Record<string, string>;
    const catalog = Array.isArray(vehicles) ? vehicles : (vehicles.vehicles ?? []);
    return {
      profile: (progress.profile ?? progress) as RecordValue,
      context: context as unknown as DashboardState["context"],
      presets,
      vehicles: (catalog as unknown as import("./types").Vehicle[]).map((vehicle) => ({ ...vehicle, status: statusByVehicle[vehicle.vehicle_id] ?? vehicle.status })),
      play_now: playNow,
      research_next: researchNext,
      evidence_health: evidenceHealth,
    };
  },
  garage: (profileId: string) => request<RecordValue>(`/profiles/${encodeURIComponent(profileId)}/progress`),
  updateStatus: (profileId: string, vehicleId: string, status: string, expectedRevision?: string | number) =>
    request<RecordValue>(`/profiles/${encodeURIComponent(profileId)}/vehicles/${encodeURIComponent(vehicleId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status, ...(expectedRevision ? { expected_revision: expectedRevision } : {}) }),
    }),
  context: (profileId: string) => request<RecordValue>(`/profiles/${encodeURIComponent(profileId)}/context`),
  updateContext: (profileId: string, body: RecordValue) =>
    request<RecordValue>(`/profiles/${encodeURIComponent(profileId)}/context`, { method: "PUT", body: JSON.stringify(body) }),
  presets: (profileId: string) => request<Preset[]>(`/profiles/${encodeURIComponent(profileId)}/presets`),
  createPreset: (profileId: string, body: RecordValue) =>
    request<Preset>(`/profiles/${encodeURIComponent(profileId)}/presets`, { method: "POST", body: JSON.stringify(body) }),
  updatePreset: (profileId: string, presetId: string, body: RecordValue) =>
    request<Preset>(`/profiles/${encodeURIComponent(profileId)}/presets/${encodeURIComponent(presetId)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deletePreset: (profileId: string, presetId: string, expectedRevision: string) =>
    request<RecordValue>(`/profiles/${encodeURIComponent(profileId)}/presets/${encodeURIComponent(presetId)}`, { method: "DELETE", body: JSON.stringify({ expected_revision: expectedRevision }) }),
  evaluate: (profileId: string, body: RecordValue) =>
    request<RecordValue>("/evaluate", { method: "POST", body: JSON.stringify({ profile_id: profileId, ...body }) }),
  compare: (profileId: string, body: RecordValue) =>
    request<RecordValue>("/compare", { method: "POST", body: JSON.stringify({ profile_id: profileId, ...body }) }),
  research: (profileId: string) => request<RecordValue | Json[]>(`/profiles/${encodeURIComponent(profileId)}/unlock-evaluations`),
  health: () => request<RecordValue>("/data-status"),
};
