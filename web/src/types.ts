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
  preferred_roles?: string[];
  duplicate_role_penalty?: number;
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

export interface AdvisorEvidenceSummary {
  summary: string;
  vehicle_count: number;
  eligible_count: number;
  proxy_count: number;
}

/** Deterministic player-facing wording built from the reason codes, never instead of them. */
export interface AdvisorExplanation {
  strengths: string[];
  tradeoffs: string[];
  warnings: string[];
  evidence_summary: AdvisorEvidenceSummary;
  research_pointer: string | null;
}

export interface AdvisorPreferenceFactors {
  preferred_roles_configured: string[];
  preferred_roles_covered: string[];
  preferred_roles_missing: string[];
  role_satisfaction: number;
  role_component: number;
  duplicate_role_pairs: number;
  duplicate_role_penalty: number;
  duplicate_strength: number;
  duplicate_load: number;
  duplicate_component: number;
  preference_adjustment: number;
  objective_score: number;
  recommendation_score: number;
  role_cap: number;
  duplicate_cap: number;
  preference_cap: number;
  max_preference_swing: number;
}

export interface AdvisorDifferentiation {
  differences: string[];
  /** Null across battle ratings: raw scores are only comparable within one BR. */
  objective_delta: number | null;
  same_battle_rating: boolean;
}

export interface AdvisorLineup {
  analysis: RecordValue;
  reasons: string[];
  preference_factors?: AdvisorPreferenceFactors;
  explanation?: AdvisorExplanation;
  differentiation?: AdvisorDifferentiation;
}

export interface AdvisorResearchPriority {
  vehicle_id: string;
  rank: number;
  reasons: string[];
  recovery_target: boolean;
  research_cost?: number;
  resulting_br?: number;
  readiness_after?: boolean;
  resolved_blockers?: string[];
  same_br_score_delta?: number | null;
  evidence_quality?: number;
  ranking_factors?: RecordValue;
  expanded_lineup?: RecordValue;
  forced_lineup?: RecordValue;
}

export interface AdvisorPreferenceTransparency {
  preference_changed_selection: boolean;
  objective_first_choice: RecordValue | null;
  candidate_pool_size: number;
  /** No ready lineup at this BR can cover these, whatever the preference weighting. */
  unsatisfiable_preferred_roles: string[];
  /** The primary lacks these, but another ready lineup at this BR covers them. */
  unmet_preferred_roles: string[];
  minimum_duplicate_role_pairs: number;
  duplicate_roles_unavoidable: boolean;
}

export interface AdvisorSnapshot {
  primary_lineup: AdvisorLineup | null;
  alternative_lineups: AdvisorLineup[];
  research_priorities: AdvisorResearchPriority[];
  preference_transparency?: AdvisorPreferenceTransparency;
  blockers: string[];
  data_gaps: string[];
  recommended_br: number | null;
  profile_id?: string;
  evidence_context?: RecordValue;
  generated_at?: string;
  source_fingerprint?: string;
  source_revisions?: RecordValue;
}

export interface AdvisorSnapshotState {
  status: "missing" | "current" | "stale" | "computing" | "failed";
  snapshot: AdvisorSnapshot | null;
  stale_reasons: string[];
  error: string | null;
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
