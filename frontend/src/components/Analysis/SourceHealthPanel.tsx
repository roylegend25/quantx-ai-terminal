import { useEffect, useState } from "react";
import { api } from "../../services/api";
type Props={symbol:string;timeframe:string};
const ratio=(a?:number,b?:number)=>`${a??0} / ${b??0}`;
const pct=(v?:number|null)=>v==null?"Not established":`${(v*100).toFixed(1)}%`;
export default function SourceHealthPanel({symbol,timeframe}:Props){
  const [health,setHealth]=useState<any>(null); const [error,setError]=useState("");
  useEffect(()=>{let live=true; setError(""); api.sourceHealth(symbol,timeframe).then(v=>{if(live)setHealth(v)}).catch(e=>{if(live)setError(e.message)}); return()=>{live=false}},[symbol,timeframe]);
  if(error)return <p className="analytics-empty">Source health unavailable: {error}</p>;
  if(!health)return <p className="analytics-empty">Loading source health…</p>;
  const s=health.summary,r=health.resolver,l=health.ledger,d=health.decision_requirements; const sources=health.sources??[];
  const count=(type:string,status:string)=>sources.filter((x:any)=>x.source_type===type&&x.runtime_status===status).length;
  return <div className="source-health-panel" data-testid="source-health"><div className="engine-metric-grid">
    <span><b>ML Models</b><br/>{ratio(s.ml_working,s.ml_total)} working · {count("ml","shadow_not_inferred")} shadow</span>
    <span><b>Strategies</b><br/>{ratio(s.strategy_working,s.strategy_total)} working · {sources.filter((x:any)=>x.source_type==="strategy"&&x.production_eligible).length} eligible now</span>
    <span><b>Quant</b><br/>{ratio(s.quant_working,s.quant_total)} working · {count("quant","unavailable_data")} unavailable</span>
    <span><b>Prediction Resolver</b><br/><span className={`chip ${r.healthy?"green":"red"}`}>{r.healthy?"Healthy":"Degraded"}</span> · {l.resolved} resolved · {l.expired_unresolved} expired</span>
  </div><div className="engine-metric-grid">
    <span>Evidence <b>{d.total_evidence??"—"} / {d.minimum_total_evidence}</b></span><span>Point margin <b>{d.point_margin??"—"} / {d.required_point_margin}</b></span>
    <span>Confidence <b>{pct(d.directional_confidence)} / {pct(d.required_confidence)}</b></span><span>History <b>{l.resolved} / {d.minimum_resolved_samples}</b></span>
  </div>{!r.healthy&&<p className="regime-desc">Resolver degraded: {r.degraded_reason??r.last_error??"unknown reason"}</p>}</div>;
}
