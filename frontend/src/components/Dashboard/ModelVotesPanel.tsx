import { memo } from "react";
import type { ContributorStatus, SignalCandidate } from "../../lib/activeDrive";
import { CONTRIBUTOR_STATUS_LABEL, classifyContributor, pct01 } from "../../lib/activeDrive";

type Props={candidates?:SignalCandidate[]|null;finalDirection?:string|null;
  /** Active Drive V2's own tally for this decision (decision.long_points /
   *  short_points) - shown alongside the sum of this panel's "active" group
   *  so a mismatch between what's displayed and what actually decided the
   *  trade is visible immediately rather than silently trusted. */
  enginePoints?:{long:number|null|undefined;short:number|null|undefined}|null};
const sourceLabels:Record<string,string>={ml:"ML",ml_model:"ML",strategy:"Strategy",quant:"Quant",quant_model:"Quant"};
const statusOrder:ContributorStatus[]=["active","veto","abstaining","inactive"];
const statusDesc:Record<ContributorStatus,string>={
  active:"Currently contributing points to this decision.",
  veto:"A structural gate (stale data, a missing required indicator, or a regime/confirmation mismatch) blocked this source this cycle.",
  abstaining:"Evaluated cleanly and found no qualifying directional signal this cycle.",
  inactive:"Shadow-mode - not yet promoted to live voting.",
};
function tone(d?:string){return d==="LONG"?"green":d==="SHORT"?"red":"yellow";}
function sourceLabel(c:SignalCandidate){return sourceLabels[c.source_type]??c.source_type;}

function ModelVotesPanel({candidates,finalDirection,enginePoints}:Props){
  if(!candidates) return <p className="analytics-empty">Calculating Active Drive V2 decision…</p>;
  if(!candidates.length) return <p className="analytics-empty">No candidates generated for this symbol/timeframe.</p>;
  const groups=statusOrder.map(status=>({status,items:candidates.filter(c=>classifyContributor(c)===status)}));
  const active=groups.find(g=>g.status==="active")?.items??[];
  const activeLong=active.filter(c=>c.direction==="LONG").reduce((sum,c)=>sum+c.final_points,0);
  const activeShort=active.filter(c=>c.direction==="SHORT").reduce((sum,c)=>sum+(-c.final_points),0);
  const reconciles=enginePoints
    ? Math.abs(activeLong-(enginePoints.long??0))<0.01 && Math.abs(activeShort-(enginePoints.short??0))<0.01
    : null;
  return <div className="votes-panel candidate-groups">{groups.map(g=><section key={g.status} className="candidate-group"><h4>{CONTRIBUTOR_STATUS_LABEL[g.status]} <small>({g.items.length})</small></h4><p className="regime-desc">{statusDesc[g.status]}</p>{g.items.length?g.items.map(c=><div className={`vote-row ${g.status!=="active"?"vote-unavailable":""}`} key={`${c.source_type}-${c.name}-${c.version}`}>
    <div className="vote-head"><span className="vote-name">{c.name.replaceAll("_"," ")} <small>{sourceLabel(c)} · {c.version}</small></span><span className={`chip ${tone(c.direction)}`}>{c.direction}</span></div>
    <div className="vote-bar-row"><span className="vote-meta">{c.final_points>=0?"+":""}{c.final_points.toFixed(2)} pts · {pct01(c.calibrated_confidence)} · {c.resolved_samples} resolved · {c.evidence_tier.replaceAll("_"," ")}</span></div>
    <p className="vote-reason">{c.rejection_reason??c.reason}</p>
  </div>):<p className="analytics-empty">No {CONTRIBUTOR_STATUS_LABEL[g.status].toLowerCase()} contributors.</p>}</section>)}
  {enginePoints&&<p className={`regime-desc votes-final ${reconciles===false?"red":""}`}>
    Active contributors sum: <b className="green">+{activeLong.toFixed(2)}</b> LONG · <b className="red">-{activeShort.toFixed(2)}</b> SHORT
    {" "}(engine total: +{(enginePoints.long??0).toFixed(2)} / -{(enginePoints.short??0).toFixed(2)}){reconciles===false&&" · does not reconcile"}
  </p>}
  {finalDirection&&<p className="regime-desc votes-final">Final decision: <b className={tone(finalDirection)}>{finalDirection}</b></p>}</div>;
}
export default memo(ModelVotesPanel);
