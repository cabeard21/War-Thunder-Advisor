import { FormEvent, useEffect, useMemo, useState } from "react";
import { AdvisorApiError, api, displayError } from "./api";
import type { DashboardState, Json, Preset, RecordValue, Vehicle } from "./types";

const statuses = ["owned", "unlocked_not_purchased", "researching", "available_to_research", "locked", "unknown"];
const profileFromUrl = new URLSearchParams(window.location.search).get("profile") ?? "acceptance";

function asRecord(value: unknown): RecordValue {
  return value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
}
function title(vehicle: Vehicle) { return vehicle.name ?? vehicle.vehicle_id; }
function br(value: unknown) { return typeof value === "number" ? (value / 10).toFixed(1) : "?"; }
function list(value: unknown): string[] { return Array.isArray(value) ? value.filter((x): x is string => typeof x === "string") : []; }

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
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [comparison, setComparison] = useState<RecordValue>();
  const [presetName, setPresetName] = useState("");
  const [editingPreset, setEditingPreset] = useState<Preset>();
  const [pendingStatuses, setPendingStatuses] = useState<Record<string, string>>({});
  const [lastEvaluatedLineup, setLastEvaluatedLineup] = useState<string[]>([]);

  const refresh = async () => {
    setBusy(true); setError("");
    try {
      const next = await api.dashboard(profileId);
      setState(next);
      const context = asRecord(next.context);
      setRequired(list(context.required_vehicle_ids));
      setExcluded(list(context.excluded_vehicle_ids));
      setTargetBr(typeof context.target_br === "number" ? String(context.target_br / 10) : "");
    } catch (caught) { setError(displayError(caught)); }
    finally { setBusy(false); }
  };
  useEffect(() => { void refresh(); }, [profileId]);

  const vehicles = useMemo(() => (state?.vehicles ?? []).filter((vehicle) =>
    !filter || `${title(vehicle)} ${vehicle.vehicle_id} ${vehicle.status ?? ""}`.toLowerCase().includes(filter.toLowerCase()),
  ), [state, filter]);
  const slots = Number(asRecord(state?.profile).crew_slots ?? 5);
  const recommendedAnalysis = asRecord(asRecord(asRecord(state?.play_now).recommended).analysis);
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
  }), "Constraints applied. Saved presets were not changed.");
  const selectContext = (presetId: string) => mutate(() => api.updateContext(profileId, {
    expected_revision: asRecord(state?.context).revision as Json,
    required_vehicle_ids: required,
    excluded_vehicle_ids: excluded,
    target_br: targetBr ? Math.round(Number(targetBr) * 10) : null,
    selected_preset_id: presetId,
  }), "Selected preset saved as the active context.");
  const saveStatus = async (vehicleId: string, attemptedStatus: string) => {
    setPendingStatuses((old) => ({ ...old, [vehicleId]: attemptedStatus }));
    setBusy(true); setError("");
    try {
      const revision = asRecord(state?.profile).revision;
      await api.updateStatus(profileId, vehicleId, attemptedStatus, typeof revision === "string" || typeof revision === "number" ? revision : undefined);
      await refresh();
      setPendingStatuses((old) => {
        const { [vehicleId]: _saved, ...remaining } = old;
        return remaining;
      });
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
    await mutate(
      () => editingPreset
        ? api.updatePreset(profileId, editingPreset.preset_id, { ...body, expected_revision: editingPreset.revision })
        : api.createPreset(profileId, body),
      editingPreset ? "Preset updated." : "Preset saved.",
    );
    setPresetName(""); setEditingPreset(undefined);
  };

  return <main className="shell">
    <header className="masthead"><div><p className="eyebrow">LOCAL FIELD CONSOLE</p><h1>War Thunder Advisor</h1><p>Evidence-first Ground RB lineup workbench.</p></div>
      <label>Profile <input value={profileId} onChange={(e) => setProfileId(e.target.value)} aria-label="Profile ID" /></label>
      <button onClick={() => void refresh()} disabled={busy}>{busy ? "Refreshing…" : "Refresh"}</button></header>
    {error && <section className="alert error" role="alert"><strong>Request needs attention.</strong> {error}<button onClick={() => setError("")}>Dismiss</button></section>}
    {notice && <section className="alert success" role="status">{notice}<button onClick={() => setNotice("")}>Dismiss</button></section>}
    <section className="command-strip"><label>Target BR <input inputMode="decimal" value={targetBr} onChange={(e) => setTargetBr(e.target.value)} placeholder="e.g. 3.7" /></label>
      <span>{required.length} pins · {excluded.length} exclusions · {draft.length}/{slots} draft slots</span>
      <button onClick={() => void saveContext()} disabled={busy}>Apply constraints</button><button className="accent" onClick={() => void evaluate()} disabled={busy || !draft.length}>Evaluate draft</button></section>
    <div className="layout">
      <section className="panel garage"><div className="panel-title"><div><p className="eyebrow">GARAGE</p><h2>Progression status</h2></div><input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter vehicles" aria-label="Filter vehicles" /></div>
        <div className="vehicle-list">{vehicles.map((vehicle) => <article className="vehicle" key={vehicle.vehicle_id}>
          <div><strong>{title(vehicle)}</strong><small>{vehicle.vehicle_id} · BR {br(vehicle.battle_rating)} · {vehicle.vehicle_class ?? "unknown class"}</small></div>
          <select aria-label={`Status for ${title(vehicle)}`} value={pendingStatuses[vehicle.vehicle_id] ?? vehicle.status ?? "unknown"} onChange={(e) => void saveStatus(vehicle.vehicle_id, e.target.value)}>{statuses.map((status) => <option key={status}>{status}</option>)}</select>
          {pendingStatuses[vehicle.vehicle_id] && pendingStatuses[vehicle.vehicle_id] !== vehicle.status && <button aria-label={`Retry status for ${title(vehicle)}`} onClick={() => void saveStatus(vehicle.vehicle_id, pendingStatuses[vehicle.vehicle_id])}>Retry</button>}
          <button aria-pressed={draft.includes(vehicle.vehicle_id)} onClick={() => toggle(vehicle.vehicle_id)}>{draft.includes(vehicle.vehicle_id) ? "Remove" : "Draft"}</button>
          <button className={required.includes(vehicle.vehicle_id) ? "marked" : ""} onClick={() => toggleConstraint(vehicle.vehicle_id, "required")}>Pin</button>
          <button className={excluded.includes(vehicle.vehicle_id) ? "marked" : ""} onClick={() => toggleConstraint(vehicle.vehicle_id, "excluded")}>Exclude</button>
        </article>)}</div></section>
      <aside className="side-stack">
        <section className="panel"><p className="eyebrow">PLAY NOW</p><h2>Ready frontier</h2><Result value={state?.play_now} empty="Refresh to load the active recommendation." /></section>
        <section className="panel"><p className="eyebrow">BUILDER</p><h2>Visible draft</h2><ol className="slots">{Array.from({ length: slots }, (_, index) => <li key={index}>{draft[index] ?? <em>Empty crew slot</em>}</li>)}</ol><p className="muted">Pins and exclusions are hard constraints. Suggestions never overwrite this draft.</p><Detail value={{ draft, required, excluded, target_br: targetBr }} /></section>
        <section className="panel"><p className="eyebrow">COMPARE</p><h2>Alternative check</h2><button onClick={() => void mutate(async () => setComparison(await api.compare(profileId, { lineup_a: draft, lineup_b: playNowLineup.slice(0, slots) })), "Comparison refreshed.")} disabled={draft.length === 0}>Compare draft / play now</button>{comparison && <Result value={comparison} empty="" />}</section>
      </aside>
    </div>
    <section className="lower-grid">
      <section className="panel"><p className="eyebrow">PRESETS</p><h2>Saved lineup intent</h2><form onSubmit={(event) => void savePreset(event)} className="preset-form"><input required name="preset-name" maxLength={80} value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder="New preset name" aria-label="Preset name" /><button className="accent">{editingPreset ? "Save changes" : "Save draft"}</button>{editingPreset && <button type="button" onClick={() => { setEditingPreset(undefined); setPresetName(""); }}>Cancel edit</button>}</form><PresetList presets={state?.presets ?? []} profileId={profileId} selectedPresetId={typeof asRecord(state?.context).selected_preset_id === "string" ? asRecord(state?.context).selected_preset_id as string : undefined} onSelect={(preset) => void selectContext(preset.preset_id)} onLoad={(preset) => { setDraft(preset.slots); setRequired(preset.required_vehicle_ids ?? []); setExcluded(preset.excluded_vehicle_ids ?? []); setNotice(`Loaded ${preset.name}; apply or save explicitly.`); }} onEdit={(preset) => { setEditingPreset(preset); setPresetName(preset.name); setDraft(preset.slots); setRequired(preset.required_vehicle_ids ?? []); setExcluded(preset.excluded_vehicle_ids ?? []); setNotice(`Editing ${preset.name}; save explicitly to update it.`); }} onDelete={(preset) => void mutate(() => api.deletePreset(profileId, preset.preset_id, preset.revision), "Preset deleted.")} /></section>
      <section className="panel"><p className="eyebrow">RESEARCH NEXT</p><h2>One-step unlock evidence</h2><Result value={state?.research_next} empty="No unlock evaluations are currently available." /></section>
      <section className="panel"><p className="eyebrow">DATA HEALTH</p><h2>Evidence boundaries</h2><Result value={state?.evidence_health} empty="No evidence health data returned." /></section>
    </section>
  </main>;
}

function Result({ value, empty }: { value: unknown; empty: string }) {
  if (!value || (Array.isArray(value) && value.length === 0)) return <p className="muted">{empty}</p>;
  const record = asRecord(value as Json); const analysis = asRecord(record.analysis as Json); const readiness = analysis.readiness_passed ?? record.readiness_passed;
  return <div className="result"><p>{typeof readiness === "boolean" && <strong className={readiness ? "ready" : "not-ready"}>{readiness ? "READY" : "NOT READY"}</strong>} {typeof analysis.lineup_br === "number" && <> BR {br(analysis.lineup_br)}</>}</p><p>{list(asRecord(analysis.lineup as Json).slots).join(" · ") || (typeof record.message === "string" ? record.message : "See details for returned evidence.")}</p><Detail value={value} /></div>;
}

function PresetList({ presets, profileId, selectedPresetId, onSelect, onLoad, onEdit, onDelete }: { presets: Preset[]; profileId: string; selectedPresetId?: string; onSelect: (preset: Preset) => void; onLoad: (preset: Preset) => void; onEdit: (preset: Preset) => void; onDelete: (preset: Preset) => void }) {
  if (!presets.length) return <p className="muted">No saved presets for {profileId}.</p>;
  return <ul className="presets">{presets.map((preset) => <li key={preset.preset_id}><div><strong>{preset.name}</strong>{selectedPresetId === preset.preset_id && <small>Selected context</small>}<small>{preset.slots.join(" · ") || "Incomplete draft"}</small></div><button onClick={() => onSelect(preset)} disabled={selectedPresetId === preset.preset_id}>Select context</button><button onClick={() => onLoad(preset)}>Load</button><button onClick={() => onEdit(preset)}>Edit</button><button onClick={() => onDelete(preset)}>Delete</button></li>)}</ul>;
}
