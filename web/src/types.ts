export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type RecordValue = Record<string, Json>;

export interface ApiError {
  code: string;
  message: string;
  details?: Json;
  status?: number;
}

export interface Vehicle {
  vehicle_id: string;
  name?: string;
  status?: string;
  battle_rating?: number;
  vehicle_class?: string;
  roles?: string[];
}

export interface Context {
  revision?: string;
  profile_revision?: string;
  selected_preset_id?: string | null;
  target_br?: number | null;
  required_vehicle_ids?: string[];
  excluded_vehicle_ids?: string[];
}

export interface Preset {
  preset_id: string;
  name: string;
  revision: string;
  slots: string[];
  required_vehicle_ids?: string[];
  excluded_vehicle_ids?: string[];
}

export interface DashboardState {
  load_errors?: string[];
  profile?: RecordValue;
  context?: Context;
  vehicles?: Vehicle[];
  play_now?: RecordValue;
  research_next?: RecordValue | Json[];
  evidence_health?: RecordValue;
  presets?: Preset[];
}

export interface CommunityRefreshResult {
  outcome: "updated" | "unchanged" | "failed" | "restart_required";
  previous_bundle_id?: string | null;
  bundle_id?: string | null;
  accepted_statistics_rows?: number;
  quarantined_statistics_rows?: number;
  statistics_status?: string;
  eligible_statistics_rows?: number;
  statistics_source_url?: string | null;
  statistics_source_revision?: string | null;
  source_observation_date?: string | null;
  statistics_age_days?: number | null;
  statistics_limitations?: string | null;
  message?: string;
  before_coverage?: Json;
  after_coverage?: Json;
}
