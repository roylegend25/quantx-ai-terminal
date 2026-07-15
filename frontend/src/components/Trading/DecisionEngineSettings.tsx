import { useEffect, useState } from "react";
import { BrainCircuit, CheckCircle2, History, ShieldCheck, X } from "lucide-react";
import { api } from "../../services/api";
import { formatMarketRegime, pct01 } from "../../lib/activeDrive";

type EngineId = "active_drive_v1" | "active_drive_v2";
type Props = { showToast: (message: string, type?: any) => void };

export default function DecisionEngineSettings({ showToast }: Props) {
  const [state, setState] = useState<any>(null);
  const [pending, setPending] = useState<EngineId | null>(null);
  const [busy, setBusy] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const load = () => api.decisionEngine().then(setState).catch((e) => showToast(e.message, "error"));
  useEffect(() => { load(); }, []);
  const confirm = async () => {
    if (!pending) return;
    setBusy(true);
    try { setState(await api.switchDecisionEngine(pending)); showToast("Decision engine updated", "success"); setPending(null); }
    catch (e: any) { showToast(e.message || "Engine switch failed", "error"); }
    finally { setBusy(false); }
  };
  if (!state) return <div className="analytics-empty">Loading decision-engine settings…</div>;
  const last = state.last_decision;
  return <div className="decision-settings-layout">
    <section className="engine-selector-panel">
      <div className="card-title">Active Engine</div>
      <p className="regime-desc">Exactly one server-selected engine controls future decisions. Switching never changes positions or orders.</p>
      <div className="engine-card-grid" role="radiogroup" aria-label="Decision engine">
        {state.available_engines.map((engine: any) => <button key={engine.id} role="radio" aria-checked={engine.selected}
          className={`engine-choice ${engine.selected ? "selected" : ""}`} onClick={() => { if (!engine.selected) { setAcknowledged(false); setPending(engine.id); } }}>
          <span className="engine-choice-head"><BrainCircuit size={19}/><b>{engine.name}</b>{engine.selected && <CheckCircle2 size={18}/>}</span>
          <span className={`chip ${engine.id === "active_drive_v2" ? "green" : "yellow"}`}>{engine.id === "active_drive_v2" ? "Recommended" : "Legacy rollback"}</span>
          <small>v{engine.version} · {engine.health}</small>
          <span>{engine.id === "active_drive_v2" ? "Multi-model, multi-strategy, quant scoring with conservative evidence weighting." : "Original decision engine retained for manual rollback."}</span>
        </button>)}
      </div>
      <label className="engine-shadow-toggle"><input type="checkbox" checked={state.compare_engines_shadow}
        onChange={async e => { const next = e.target.checked; try { setState(await api.setEngineComparison(next)); } catch (err: any) { showToast(err.message, "error"); } }}/>
        Compare inactive engine in shadow mode (never executes)</label>
    </section>
    <section className="engine-detail-panel">
      <div className="card-title">Authoritative Decision Status</div>
      <div className="engine-metric-grid">
        <Metric label="Current engine" value={state.active_engine === "active_drive_v2" ? "Active Drive V2" : "Legacy V1"}/>
        <Metric label="Last signal" value={last?.final_signal || "No decision yet"}/>
        <Metric label="Engine health" value={last?.health || "healthy"}/>
        <Metric label="Data status" value={last?.data_status?.stale ? "Cached / stale" : last?.data_status?.source || last?.data_status || "—"}/>
        <Metric label="Candidate sources" value={last?.candidate_count ?? 0}/>
        <Metric label="Resolved history" value={state.resolved_history_count ?? 0}/>
        <Metric label="Evidence tier" value={(last?.evidence_tier || "insufficient_evidence").replaceAll("_", " ")}/>
        <Metric label="Last switch" value={state.last_switch?.created_at ? new Date(state.last_switch.created_at).toLocaleString() : "Default migration"}/>
        <Metric label="LONG points" value={last?.long_points ?? "—"}/>
        <Metric label="SHORT points" value={last?.short_points ?? "—"}/>
        <Metric label="Point margin" value={last?.point_margin ?? "—"}/>
        <Metric label="Required margin" value={last?.required_point_margin ?? "—"}/>
        <Metric label="Required confidence" value={pct01(last?.required_confidence)}/>
        <Metric label="Market regime" value={formatMarketRegime(last?.market_regime)}/>
      </div>
      {last?.blocking_reasons?.length > 0 && <div className="engine-blockers"><ShieldCheck size={17}/><div><b>Current NO_TRADE safeguards</b>{last.blocking_reasons.map((r: string) => <p key={r}>{r}</p>)}</div></div>}
      <div className="engine-history-note"><History size={16}/> Historical accuracy and expected edge remain unavailable until fixed-horizon predictions resolve.</div>
    </section>
    {pending && <div className="engine-confirm-backdrop" role="dialog" aria-modal="true" aria-label="Confirm engine switch">
      <div className="engine-confirm-sheet"><button className="engine-confirm-close" onClick={() => setPending(null)}><X size={18}/></button>
        <h3>Switch to {pending === "active_drive_v2" ? "Active Drive V2" : "Active Drive V1"}?</h3>
        <p>{pending === "active_drive_v2" ? "Active Drive V2 will become the authoritative decision engine." : "You are switching to the legacy Active Drive V1 engine."} Existing positions and protective TP/SL orders will not be modified.</p>
        <label className="engine-ack"><input type="checkbox" checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)}/> I acknowledge this applies only to future decisions.</label>
        <button disabled={busy || !acknowledged} onClick={confirm}>{busy ? "Switching…" : "Confirm engine switch"}</button>
      </div>
    </div>}
  </div>;
}
function Metric({label, value}:{label:string,value:any}) { return <div className="analytics-tile"><span className="tile-label">{label}</span><b className="tile-value engine-metric-value">{String(value)}</b></div>; }
