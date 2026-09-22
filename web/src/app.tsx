import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { AdvisorApiError, api, displayError } from "./api";
import type { AdvisorExplanation, AdvisorPreferenceFactors, AdvisorPreferenceTransparency, AdvisorSnapshotState, CommunityRefreshResult, DashboardState, Json, Preset, RecordValue, Vehicle } from "./types";
import { vehicleName } from "./vehicle-name";

const statuses = ["owned", "unlocked_not_purchased", "researching", "available_to_research", "locked", "unknown"];
const profileFromUrl = new URLSearchParams(window.location.search).get("profile") ?? "acceptance";

function asRecord(value: unknown): RecordValue {
  return value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
}
function title(vehicle: Vehicle) { return vehicleName(vehicle); }
function br(value: unknown) { return typeof value === "number" ? (value / 10).toFixed(1) : "?"; }
function list(value: unknown): string[] { return Array.isArray(value) ? value.filter((x): x is string => typeof x === "string") : []; }

function metricLabel(raw: unknown, definition: string, label: string): string | null {
  const metric = asRecord(raw);
  if (metric.metric_definition !== definition) return null;
  const source = typeof metric.source_field === "string" ? ` (${metric.source_field})` : "";
  return `${label}${source}`;
}

function StatisticsEvidence({ analysis, vehicleName }: { analysis: RecordValue; vehicleName: (id: string) => string }) {
  const rules = Array.isArray(analysis.rules) ? analysis.rules : [];
  const statistical = rules.map(asRecord).find((rule) => rule.rule === "statistical_strength");
  const vehicles = asRecord(asRecord(statistical?.evidence).vehicles);
  const entries = Object.entries(vehicles);
  if (!entries.length) return null;
  return <div className="statistics-evidence"><strong>Statistical evidence</strong><ul>{entries.map(([id, raw]) => {
    const evidence = asRecord(raw);
    const label = typeof evidence.evidence_label === "string" ? evidence.evidence_label : "Statistics";
    const eligible = evidence.eligible === true;
    const proxy = evidence.proxy_used === true;
    const scope = typeof evidence.source_scope === "string" ? evidence.source_scope : null;
    const provider = typeof evidence.source_provider === "string" ? evidence.source_provider : null;
    const reason = typeof evidence.exclusion_reason === "string" ? evidence.exclusion_reason : null;
    const metrics = [metricLabel(evidence.kd, "ground_kills_per_death", "ground kills/death"), metricLabel(evidence.kills_per_battle, "ground_kills_per_battle", "ground kills/battle")].filter((value): value is string => value !== null);
    return <li key={id}>{vehicleName(id)}: {eligible ? `${label}${proxy ? " (proxy used)" : ""}` : "statistics excluded"}{scope && <> · scope: {scope}</>}{provider && <> · source: {provider}</>}{metrics.length > 0 && <> · metrics: {metrics.join(", ")}</>}{!eligible && reason && <> · reason: {reason}</>}</li>;
  })}</ul></div>;
}

function Detail({ value }: { value: unknown }) {
  return <details><summary>Why / raw evidence</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}

export function AdvisorApp() {
  const [profileId, setProfileId] = useState(profileFromUrl);
  const [state, setState] = useState<DashboardState>();
  const [draft, setDraft] = useState<string[]>([]);
  const [required, setRequired] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [targetBr, setTargetBr] = useState<string>("");
  const [preferredRoles, setPreferredRoles] = useState("");
  const [duplicateRolePenalty, setDuplicateRolePenalty] = useState("0");
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [comparison, setComparison] = useState<RecordValue>();
  const [presetName, setPresetName] = useState("");
  const [editingPreset, setEditingPreset] = useState<Preset>();
  const [pendingStatuses, setPendingStatuses] = useState<Record<string, string>>({});
  const [lastEvaluatedLineup, setLastEvaluatedLineup] = useState<string[]>([]);
  const [communityRefresh, setCommunityRefresh] = useState<CommunityRefreshResult>();
  const [advisor, setAdvisor] = useState<AdvisorSnapshotState>();
  const refreshSequence = useRef(0);
  const advisorSequence = useRef(0);

  const loadAdvisor = async (requestedProfile: string, autoRefresh = true) => {
    const sequence = ++advisorSequence.current;
    try {
      const result = await api.advisor(requestedProfile);
      if (sequence !== advisorSequence.current) return;
      setAdvisor(result);
      if (autoRefresh && (result.status === "missing" || result.status === "stale")) {
        const started = await api.refreshAdvisor(requestedProfile);
        if (sequence === advisorSequence.current) setAdvisor({ ...started, snapshot: started.snapshot ?? result.snapshot, stale_reasons: started.stale_reasons.length ? started.stale_reasons : result.stale_reasons });
      }
    } catch (caught) {
      if (sequence === advisorSequence.current) {
        const message = displayError(caught);
        setError(`Unable to load advisor snapshot: ${message}`);
        setAdvisor((current) => ({ status: "failed", snapshot: current?.snapshot ?? null, stale_reasons: current?.stale_reasons ?? [], error: message }));
      }
    }
  };

  const retryAdvisor = async () => {
    try { const started = await api.refreshAdvisor(profileId); setAdvisor((current) => ({ ...started, snapshot: started.snapshot ?? current?.snapshot ?? null })); }
    catch (caught) { setError(`Unable to refresh advisor snapshot: ${displayError(caught)}`); }
  };

  const refresh = async () => {
    const sequence = ++refreshSequence.current;
    setBusy(true); setError("");
    try {
      const next = await api.dashboard(profileId);
      if (sequence !== refreshSequence.current) return;
      setState(next);
      if (next.load_errors?.length) setError(next.load_errors.join(" "));
      const context = asRecord(next.context);
      setRequired(list(context.required_vehicle_ids));
      setExcluded(list(context.excluded_vehicle_ids));
      setTargetBr(typeof context.target_br === "number" ? String(context.target_br / 10) : "");
      setPreferredRoles(list(context.preferred_roles).join(", "));
      setDuplicateRolePenalty(typeof context.duplicate_role_penalty === "number" ? String(context.duplicate_role_penalty) : "0");
      void loadAdvisor(profileId);
    } catch (caught) {
      if (sequence === refreshSequence.current) setError(displayError(caught));
    } finally {
      if (sequence === refreshSequence.current) setBusy(false);
    }
  };
  useEffect(() => { ++advisorSequence.current; setAdvisor(undefined); void refresh(); }, [profileId]);
  useEffect(() => {
    if (advisor?.status !== "computing") return;
    const timer = window.setTimeout(() => { void loadAdvisor(profileId, false); }, 1500);
    return () => window.clearTimeout(timer);
  }, [advisor?.status, advisor?.snapshot, profileId]);

  const refreshCommunityEvidence = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.refreshCommunityEvidence();
      setCommunityRefresh(result);
      if (result.outcome === "failed") {
        setError(result.message ?? "Community evidence refresh failed; the previous bundle remains active.");
        return;
      }
      await refresh();
      setNotice(result.message ?? (result.outcome === "updated" ? "Community evidence updated." : "Community evidence is already current."));
    } catch (caught) { setError(displayError(caught)); }
    finally { setBusy(false); }
  };

  const vehicles = useMemo(() => (state?.vehicles ?? []).filter((vehicle) =>
    !filter || `${title(vehicle)} ${vehicle.vehicle_id} ${vehicle.status ?? ""}`.toLowerCase().includes(filter.toLowerCase()),
  ), [state, filter]);
  const names = useMemo(() => new Map((state?.vehicles ?? []).map((vehicle) => [vehicle.vehicle_id, title(vehicle)])), [state]);
  const vehicleName = (id: string) => names.get(id) ?? id;
  const slots = Number(asRecord(state?.profile).crew_slots ?? 5);
  const recommendedAnalysis = asRecord(advisor?.snapshot?.primary_lineup?.analysis);
  const generatedLineup = list(asRecord(recommendedAnalysis.lineup).slots);
  const evaluatedLineup = list(asRecord(asRecord(state?.play_now).lineup).slots);
  const playNowLineup = generatedLineup.length
    ? generatedLineup
    : evaluatedLineup.length
      ? evaluatedLineup
      : lastEvaluatedLineup;
  const toggle = (id: string) => setDraft((old) => old.includes(id) ? old.filter((x) => x !== id) : old.length < slots ? [...old, id] : old);
  const toggleConstraint = (id: string, kind: "required" | "excluded") => {
    const own = kind === "required" ? required : excluded;
    const other = kind === "required" ? excluded : required;
    const update = own.includes(id) ? own.filter((x) => x !== id) : [...own, id];
    if (kind === "required") { setRequired(update); setExcluded(other.filter((x) => x !== id)); }
    else { setExcluded(update); setRequired(other.filter((x) => x !== id)); }
  };
  const mutate = async (work: () => Promise<unknown>, success: string) => {
    setBusy(true); setError("");
    try { await work(); setNotice(success); await refresh(); }
    catch (caught) { setError(displayError(caught)); }
    finally { setBusy(false); }
  };
  const evaluate = () => mutate(async () => {
    const evaluated = await api.evaluate(profileId, {
      vehicle_ids: draft, required_vehicle_ids: required, excluded_vehicle_ids: excluded,
      target_br: targetBr ? Math.round(Number(targetBr) * 10) : null,
    });
    setLastEvaluatedLineup(draft);
    setState((old) => old ? { ...old, play_now: evaluated } : old);
  }, "Draft evaluated under the current evidence bundle.");
  const saveContext = () => mutate(() => api.updateContext(profileId, {
    expected_revision: asRecord(state?.context).revision as string,
    required_vehicle_ids: required, excluded_vehicle_ids: excluded,
    target_br: targetBr ? Math.round(Number(targetBr) * 10) : null,
    selected_preset_id: asRecord(state?.context).selected_preset_id ?? null,
    preferred_roles: [...new Set(preferredRoles.split(",").map((role) => role.trim()).filter(Boolean))],
    duplicate_role_penalty: Number(duplicateRolePenalty),
  }), "Constraints applied. Saved presets were not changed.");
  const selectContext = (presetId: string) => mutate(async () => {
    const context = await api.updateContext(profileId, {
      expected_revision: asRecord(state?.context).revision as Json,
      required_vehicle_ids: required,
      excluded_vehicle_ids: excluded,
      target_br: targetBr ? Math.round(Number(targetBr) * 10) : null,
      selected_preset_id: presetId,
      preferred_roles: [...new Set(preferredRoles.split(",").map((role) => role.trim()).filter(Boolean))],
      duplicate_role_penalty: Number(duplicateRolePenalty),
    });
    setState((old) => old ? { ...old, context: context as DashboardState["context"] } : old);
  }, "Selected preset saved as the active context.");
  const saveStatus = async (vehicleId: string, attemptedStatus: string) => {
    setPendingStatuses((old) => ({ ...old, [vehicleId]: attemptedStatus }));
    setBusy(true); setError("");
    try {
      const revision = asRecord(state?.profile).revision;
      await api.updateStatus(profileId, vehicleId, attemptedStatus, typeof revision === "string" || typeof revision === "number" ? revision : undefined);
      setPendingStatuses((old) => {
        const { [vehicleId]: _saved, ...remaining } = old;
        return remaining;
      });
      await refresh();
      setNotice("Garage status saved; recommendations refreshed.");
    } catch (caught) {
      if (caught instanceof AdvisorApiError && (caught.code === "conflict" || caught.status === 409)) {
        await refresh();
      }
      setError(displayError(caught));
    } finally { setBusy(false); }
  };
  const savePreset = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const body = { name: presetName, slots: draft, required_vehicle_ids: required, excluded_vehicle_ids: excluded };
    setBusy(true); setError("");
    try {
      const saved = editingPreset
        ? await api.updatePreset(profileId, editingPreset.preset_id, { ...body, expected_revision: editingPreset.revision })
        : await api.createPreset(profileId, body);
      setState((old) => old ? {
        ...old,
        presets: editingPreset
          ? (old.presets ?? []).map((item) => item.preset_id === saved.preset_id ? saved : item)
          : [...(old.presets ?? []), saved],
      } : old);
      setNotice(editingPreset ? "Preset updated." : "Preset saved.");
      setPresetName(""); setEditingPreset(undefined);
      await refresh();
    } catch (caught) { setError(displayError(caught)); }
    finally { setBusy(false); }
  };

  return <main className="shell">
    <header className="masthead"><div><p className="eyebrow">LOCAL FIELD CONSOLE</p><h1>War Thunder Advisor</h1><p>Evidence-first Ground RB lineup workbench.</p></div>
      <label>Profile <input value={profileId} onChange={(e) => setProfileId(e.target.value)} aria-label="Profile ID" /></label>
      <button onClick={() => void refresh()} disabled={busy}>{busy ? "Refreshing…" : "Refresh"}</button>
      <button onClick={() => void refreshCommunityEvidence()} disabled={busy}>Refresh community evidence</button></header>
    {error && <section className="alert error" role="alert"><strong>Request needs attention.</strong> {error}<button onClick={() => setError("")}>Dismiss</button></section>}
    {notice && <section className="alert success" role="status">{notice}<button onClick={() => setNotice("")}>Dismiss</button></section>}
    <section className="command-strip"><label>Target BR <input inputMode="decimal" value={targetBr} onChange={(e) => setTargetBr(e.target.value)} placeholder="e.g. 3.7" /></label>
      <label>Preferred roles, in order <input aria-label="Preferred roles, in order" value={preferredRoles} onChange={(e) => setPreferredRoles(e.target.value)} placeholder="tank_destroyer, spaa" /></label>
      <label>Duplicate-role penalty <input aria-label="Duplicate-role penalty" type="number" min="0" max="10" step="1" value={duplicateRolePenalty} onChange={(e) => setDuplicateRolePenalty(e.target.value)} /></label>
      <span>{required.length} pins · {excluded.length} exclusions · {draft.length}/{slots} draft slots</span>
      <button onClick={() => void saveContext()} disabled={busy}>Apply constraints</button><button className="accent" onClick={() => void evaluate()} disabled={busy || !draft.length}>Evaluate draft</button></section>
    <div className="layout">
      <section className="panel garage"><div className="panel-title"><div><p className="eyebrow">GARAGE</p><h2>Progression status</h2></div><input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter vehicles" aria-label="Filter vehicles" /></div>
        <div className="vehicle-list">{vehicles.map((vehicle) => <article className="vehicle" key={vehicle.vehicle_id}>
          <div><strong>{title(vehicle)}</strong><small>BR {br(vehicle.battle_rating)} · {vehicle.vehicle_class ?? "unknown class"}</small></div>
          <select aria-label={`Status for ${title(vehicle)}`} value={pendingStatuses[vehicle.vehicle_id] ?? vehicle.status ?? "unknown"} onChange={(e) => void saveStatus(vehicle.vehicle_id, e.target.value)}>{statuses.map((status) => <option key={status}>{status}</option>)}</select>
          {pendingStatuses[vehicle.vehicle_id] && pendingStatuses[vehicle.vehicle_id] !== vehicle.status && <button aria-label={`Retry status for ${title(vehicle)}`} onClick={() => void saveStatus(vehicle.vehicle_id, pendingStatuses[vehicle.vehicle_id])}>Retry</button>}
          <button aria-pressed={draft.includes(vehicle.vehicle_id)} onClick={() => toggle(vehicle.vehicle_id)}>{draft.includes(vehicle.vehicle_id) ? "Remove" : "Draft"}</button>
          <button className={required.includes(vehicle.vehicle_id) ? "marked" : ""} onClick={() => toggleConstraint(vehicle.vehicle_id, "required")}>Pin</button>
          <button className={excluded.includes(vehicle.vehicle_id) ? "marked" : ""} onClick={() => toggleConstraint(vehicle.vehicle_id, "excluded")}>Exclude</button>
        </article>)}</div></section>
      <aside className="side-stack">
        <section className="panel"><p className="eyebrow">PLAY NOW</p><h2>Ready frontier</h2><AdvisorPanel advisor={advisor} vehicleName={vehicleName} onRetry={() => void retryAdvisor()} /></section>
        <section className="panel"><p className="eyebrow">BUILDER</p><h2>Visible draft</h2><ol className="slots">{Array.from({ length: slots }, (_, index) => <li key={index}>{draft[index] ? vehicleName(draft[index]) : <em>Empty crew slot</em>}</li>)}</ol><p className="muted">Pins and exclusions are hard constraints. Suggestions never overwrite this draft.</p><Detail value={{ draft, required, excluded, target_br: targetBr }} /></section>
        <section className="panel"><p className="eyebrow">COMPARE</p><h2>Alternative check</h2><button onClick={() => void mutate(async () => setComparison(await api.compare(profileId, { lineup_a: draft, lineup_b: playNowLineup.slice(0, slots) })), "Comparison refreshed.")} disabled={draft.length === 0}>Compare draft / play now</button>{comparison && <Result value={comparison} empty="" vehicleName={vehicleName} />}</section>
      </aside>
    </div>
    <section className="lower-grid">
      <section className="panel"><p className="eyebrow">PRESETS</p><h2>Saved lineup intent</h2><form onSubmit={(event) => void savePreset(event)} className="preset-form"><input required name="preset-name" maxLength={80} value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder="New preset name" aria-label="Preset name" /><button className="accent">{editingPreset ? "Save changes" : "Save draft"}</button>{editingPreset && <button type="button" onClick={() => { setEditingPreset(undefined); setPresetName(""); }}>Cancel edit</button>}</form><PresetList presets={state?.presets ?? []} profileId={profileId} selectedPresetId={typeof asRecord(state?.context).selected_preset_id === "string" ? asRecord(state?.context).selected_preset_id as string : undefined} vehicleName={vehicleName} onSelect={(preset) => void selectContext(preset.preset_id)} onLoad={(preset) => { setDraft(preset.slots); setRequired(preset.required_vehicle_ids ?? []); setExcluded(preset.excluded_vehicle_ids ?? []); setNotice(`Loaded ${preset.name}; apply or save explicitly.`); }} onEdit={(preset) => { setEditingPreset(preset); setPresetName(preset.name); setDraft(preset.slots); setRequired(preset.required_vehicle_ids ?? []); setExcluded(preset.excluded_vehicle_ids ?? []); setNotice(`Editing ${preset.name}; save explicitly to update it.`); }} onDelete={(preset) => void mutate(async () => { await api.deletePreset(profileId, preset.preset_id, preset.revision); setState((old) => old ? { ...old, presets: (old.presets ?? []).filter((item) => item.preset_id !== preset.preset_id) } : old); }, "Preset deleted.")} /></section>
      <section className="panel"><p className="eyebrow">RESEARCH NEXT</p><h2>Ranked unlocks</h2><ResearchPanel advisor={advisor} vehicleName={vehicleName} /></section>
      <section className="panel"><p className="eyebrow">DATA HEALTH</p><h2>Evidence boundaries</h2><EvidenceHealth value={state?.evidence_health} />{communityRefresh && <><p>Community refresh: {communityRefresh.outcome}. Statistics: {communityRefresh.statistics_status ?? "unknown"}; {communityRefresh.accepted_statistics_rows ?? 0} accepted, {communityRefresh.quarantined_statistics_rows ?? 0} quarantined, {communityRefresh.eligible_statistics_rows ?? 0} scoring-eligible. Bundle: {communityRefresh.bundle_id ?? "unchanged"}.</p>{communityRefresh.source_observation_date && <p className="muted">Community statistics observed {communityRefresh.source_observation_date} ({communityRefresh.statistics_age_days ?? "?"} days old). {communityRefresh.statistics_limitations}</p>}</>}</section>
    </section>
  </main>;
}

function EvidenceHealth({ value }: { value: unknown }) {
  if (!value) return <p className="muted">No evidence health data returned.</p>;
  const health = asRecord(value);
  const components = asRecord(health.components);
  const capabilities = asRecord(components.capabilities);
  const statistics = asRecord(components.global_statistics);
  const total = capabilities.total_count;
  const complete = capabilities.field_complete_count;
  return <div className="result">
    {typeof total === "number" && typeof complete === "number" &&
      <p>{complete} of {total} vehicles have complete scored capability evidence; {String(capabilities.missing_pair_count ?? "?")} vehicle/capability pairs remain unresolved.</p>}
    <p>Statistics: {typeof statistics.status === "string" ? statistics.status : "unknown"}. {statistics.missing_reason === "no_compatible_statistics_snapshot" ? "No compatible operational statistics snapshot is selected." : "Coverage and provenance are in the details below."}</p>
    <p>When evidence is missing, 50 is a neutral composite contribution, not an observed vehicle score or verified capability. Uptier resilience is a proxy; inspect its unknown inputs.</p>
    <Detail value={value} />
  </div>;
}

function reasonLabel(reason: string): string {
  return reason.replaceAll("_", " ");
}

/** Player-facing wording. The backend generates it deterministically from the reason codes. */
function Explanation({ explanation, vehicleName }: { explanation?: AdvisorExplanation; vehicleName: (id: string) => string }) {
  if (!explanation) return null;
  const pointer = explanation.research_pointer;
  // The backend has no display names in this dataset, so re-render its pointer sentence here.
  const tradeoffs = pointer
    ? explanation.tradeoffs.map((line) => line.includes(pointer) ? line.replaceAll(pointer, vehicleName(pointer)) : line)
    : explanation.tradeoffs;
  return <div className="advisor-explanation">
    {explanation.strengths.length > 0 && <><h4>Why this lineup</h4><ul className="advisor-strengths">{explanation.strengths.map((line) => <li key={line}>{line}</li>)}</ul></>}
    {tradeoffs.length > 0 && <><h4>Tradeoffs</h4><ul className="advisor-tradeoffs">{tradeoffs.map((line) => <li key={line}>{line}</li>)}</ul></>}
    {explanation.warnings.length > 0 && <><h4>Warnings</h4><ul className="advisor-warnings">{explanation.warnings.map((line) => <li key={line}>{line}</li>)}</ul></>}
    <p className="advisor-evidence-summary">{explanation.evidence_summary.summary}</p>
  </div>;
}

/** Answers "did my preferences affect this?" without reading source code. */
function PreferenceMaths({ factors, transparency, vehicleName }: { factors?: AdvisorPreferenceFactors; transparency?: AdvisorPreferenceTransparency; vehicleName: (id: string) => string }) {
  if (!factors) return null;
  const first = asRecord(transparency?.objective_first_choice);
  return <div className="preference-maths">
    <strong>Preference calculation</strong>
    <ul>
      <li>Objective evaluation: {factors.objective_score.toFixed(3)}</li>
      <li>Preferred roles: {factors.role_component >= 0 ? "+" : ""}{factors.role_component.toFixed(3)} (cap {factors.role_cap})</li>
      <li>Duplicate roles: {factors.duplicate_component.toFixed(3)} (cap −{factors.duplicate_cap}, avoidance strength {factors.duplicate_role_penalty}/10)</li>
      <li>Preference adjustment: {factors.preference_adjustment >= 0 ? "+" : ""}{factors.preference_adjustment.toFixed(3)}</li>
      <li>Recommendation score: {factors.recommendation_score.toFixed(3)}</li>
      <li>Most preferences can ever overcome: {factors.max_preference_swing} points</li>
      {typeof transparency?.candidate_pool_size === "number" && <li>Ready lineups compared at this BR: {transparency.candidate_pool_size}</li>}
      <li>Did preferences change the recommendation? {transparency?.preference_changed_selection ? "Yes" : "No"}</li>
      {transparency?.preference_changed_selection && Array.isArray(first.slots) && <li>Without preferences: {list(first.slots).map(vehicleName).join(" · ")}</li>}
    </ul>
  </div>;
}

function AdvisorPanel({ advisor, vehicleName, onRetry }: { advisor?: AdvisorSnapshotState; vehicleName: (id: string) => string; onRetry: () => void }) {
  if (!advisor) return <p className="muted">Loading saved advisor result…</p>;
  const snapshot = advisor.snapshot;
  const primary = snapshot?.primary_lineup;
  const analysis = asRecord(primary?.analysis);
  const lineup = list(asRecord(analysis.lineup).slots);
  return <div className="result">
    <p className={`advisor-status ${advisor.status}`} aria-live="polite">{advisor.status === "current" ? "Current recommendation" : advisor.status === "computing" ? "Computing recommendation…" : advisor.status === "stale" ? "Stale recommendation — refreshing" : advisor.status === "failed" ? "Recomputation failed" : "No saved recommendation yet — computing"}</p>
    {advisor.stale_reasons.length > 0 && <p>Changed: {advisor.stale_reasons.map(reasonLabel).join(", ")}</p>}
    {advisor.error && <p className="not-ready">{advisor.error}</p>}
    {advisor.status === "failed" && <button onClick={onRetry}>Retry advisor</button>}
    {primary ? <>
      <p>{analysis.readiness_passed === true ? <strong className="ready">READY</strong> : <strong className="not-ready">NOT READY</strong>} {typeof snapshot?.recommended_br === "number" && <> BR {br(snapshot.recommended_br)}</>}</p>
      <p className="advisor-lineup">{lineup.map(vehicleName).join(" · ") || "No ready lineup"}</p>
      <Explanation explanation={primary.explanation} vehicleName={vehicleName} />
      <details className="advisor-details">
        <summary>Reason codes, evidence and preference maths</summary>
        {primary.reasons.length > 0 && <p className="reason-codes">{primary.reasons.join(" · ")}</p>}
        <PreferenceMaths factors={primary.preference_factors} transparency={snapshot?.preference_transparency} vehicleName={vehicleName} />
        <StatisticsEvidence analysis={analysis} vehicleName={vehicleName} />
      </details>
    </> : <p className="muted">No ready lineup is available.</p>}
    {(snapshot?.alternative_lineups ?? []).length > 0 && <><h3>Alternatives</h3><ol className="advisor-alternatives">{snapshot?.alternative_lineups.map((alternative, index) => {
      const altAnalysis = asRecord(alternative.analysis);
      return <li key={index}>
        <p className="advisor-lineup">{list(asRecord(altAnalysis.lineup).slots).map(vehicleName).join(" · ")}</p>
        <p className="muted">{altAnalysis.readiness_passed === true ? "Ready" : "Not ready"}{typeof altAnalysis.lineup_br === "number" && <> · BR {br(altAnalysis.lineup_br)}</>}</p>
        {(alternative.differentiation?.differences ?? []).length > 0 && <ul className="advisor-tradeoffs">{alternative.differentiation?.differences.map((line) => <li key={line}>{line}</li>)}</ul>}
        <details><summary>Reason codes</summary><p className="reason-codes">{alternative.reasons.join(" · ")}</p></details>
      </li>;
    })}</ol></>}
    {(snapshot?.blockers ?? []).length > 0 && <><h3>Readiness blockers</h3><ul>{snapshot?.blockers.map((item) => <li key={item}>{reasonLabel(item)}</li>)}</ul></>}
    {(snapshot?.data_gaps ?? []).length > 0 && <><h3>Evidence gaps</h3><ul>{snapshot?.data_gaps.map((item) => <li key={item}>{reasonLabel(item)}</li>)}</ul></>}
    {snapshot?.generated_at && <small>Generated {snapshot.generated_at}</small>}
    {snapshot && <Detail value={snapshot} />}
  </div>;
}

function ResearchPanel({ advisor, vehicleName }: { advisor?: AdvisorSnapshotState; vehicleName: (id: string) => string }) {
  const priorities = advisor?.snapshot?.research_priorities ?? [];
  if (!priorities.length) return <p className="muted">No directly researchable targets in this snapshot.</p>;
  return <ol className="research-priorities">{priorities.map((priority) => <li key={priority.vehicle_id}>
    <strong>{vehicleName(priority.vehicle_id)}</strong> {priority.recovery_target && <span className="not-ready">Recovery target</span>}
    {priority.reasons.length > 0 && <p>Why: {priority.reasons.map(reasonLabel).join(" · ")}</p>}
    {(priority.expanded_lineup || priority.ranking_factors) && <Detail value={{ ranking_factors: priority.ranking_factors, research_cost: priority.research_cost, resulting_br: priority.resulting_br, resolved_blockers: priority.resolved_blockers, expanded_lineup: priority.expanded_lineup }} />}
  </li>)}</ol>;
}

function Result({ value, empty, vehicleName }: { value: unknown; empty: string; vehicleName: (id: string) => string }) {
  if (!value || (Array.isArray(value) && value.length === 0)) return <p className="muted">{empty}</p>;
  const record = asRecord(value as Json);
  const directAnalysis = asRecord(record.analysis);
  const recommendedAnalysis = asRecord(asRecord(record.recommended).analysis);
  const analysis = Object.keys(directAnalysis).length ? directAnalysis : Object.keys(recommendedAnalysis).length ? recommendedAnalysis : record;
  const readiness = analysis.readiness_passed ?? record.readiness_passed;
  return <div className="result"><p>{typeof readiness === "boolean" && <strong className={readiness ? "ready" : "not-ready"}>{readiness ? "READY" : "NOT READY"}</strong>} {typeof analysis.lineup_br === "number" && <> BR {br(analysis.lineup_br)}</>}</p><p>{list(asRecord(analysis.lineup as Json).slots).map(vehicleName).join(" · ") || (typeof record.message === "string" ? record.message : "See details for returned evidence.")}</p><StatisticsEvidence analysis={analysis} vehicleName={vehicleName} /><Detail value={value} /></div>;
}

function PresetList({ presets, profileId, selectedPresetId, vehicleName, onSelect, onLoad, onEdit, onDelete }: { presets: Preset[]; profileId: string; selectedPresetId?: string; vehicleName: (id: string) => string; onSelect: (preset: Preset) => void; onLoad: (preset: Preset) => void; onEdit: (preset: Preset) => void; onDelete: (preset: Preset) => void }) {
  if (!presets.length) return <p className="muted">No saved presets for {profileId}.</p>;
  return <ul className="presets">{presets.map((preset) => <li key={preset.preset_id}><div><strong>{preset.name}</strong>{selectedPresetId === preset.preset_id && <small>Selected context</small>}<small>{preset.slots.map(vehicleName).join(" · ") || "Incomplete draft"}</small></div><button onClick={() => onSelect(preset)} disabled={selectedPresetId === preset.preset_id}>Select context</button><button onClick={() => onLoad(preset)}>Load</button><button onClick={() => onEdit(preset)}>Edit</button><button onClick={() => onDelete(preset)}>Delete</button></li>)}</ul>;
}
