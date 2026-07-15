import { memo } from "react";
import type { SignalCandidate } from "../../lib/activeDrive";
import { pct01 } from "../../lib/activeDrive";

type Props={candidates?:SignalCandidate[]|null;finalDirection?:string|null};
const labels:Record<string,string>={ml:"ML Models",ml_model:"ML Models",strategy:"Strategies",quant:"Quant Signals",quant_model:"Quant Signals"};
function tone(d?:string){return d==="LONG"?"green":d==="SHORT"?"red":"yellow";}
function ModelVotesPanel({candidates,finalDirection}:Props){
  if(!candidates) return <p className="analytics-empty">Calculating Active Drive V2 decision…</p>;
  if(!candidates.length) return <p className="analytics-empty">No candidates generated for this symbol/timeframe.</p>;
  const groups=["ml","strategy","quant"].map(key=>({key,items:candidates.filter(c=>(c.source_type==="ml_model"?"ml":c.source_type==="quant_model"?"quant":c.source_type)===key)}));
  return <div className="votes-panel candidate-groups">{groups.map(g=><section key={g.key} className="candidate-group"><h4>{labels[g.key]}</h4>{g.items.length?g.items.map(c=><div className={`vote-row ${c.status==="shadow"?"vote-unavailable":""}`} key={`${c.source_type}-${c.name}-${c.version}`}>
    <div className="vote-head"><span className="vote-name">{c.name.replaceAll("_"," ")} <small>{c.version}</small></span><span className={`chip ${tone(c.direction)}`}>{c.direction}</span></div>
    <div className="vote-bar-row"><span className="vote-meta">{c.final_points>=0?"+":""}{c.final_points.toFixed(2)} pts · {pct01(c.calibrated_confidence)} · {c.resolved_samples} resolved · {c.evidence_tier.replaceAll("_"," ")}</span></div>
    <p className="vote-reason">{c.reason}</p>
  </div>):<p className="analytics-empty">No {labels[g.key].toLowerCase()} generated.</p>}</section>)}
  {finalDirection&&<p className="regime-desc votes-final">Final decision: <b className={tone(finalDirection)}>{finalDirection}</b></p>}</div>;
}
export default memo(ModelVotesPanel);
