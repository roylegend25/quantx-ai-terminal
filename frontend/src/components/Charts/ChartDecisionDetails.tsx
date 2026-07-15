import { X } from "lucide-react";
import { formatMarketRegime } from "../../lib/activeDrive";

type Props={prediction:any};
const pct=(v:any)=>typeof v==="number"&&Number.isFinite(v)?`${(v<=1?v*100:v).toFixed(0)}%`:"Not established";

export function ChartDecisionChip({prediction,onOpen}:{prediction:any;onOpen:()=>void}){
  const signal=prediction?.direction??"NO_TRADE";
  const margin=prediction?.decision_engine?.point_margin;
  return <button className={`chart-decision-chip ${signal.toLowerCase()}`} onClick={onOpen} aria-label="View Active Drive V2 decision details">
    <b>{signal}</b><span>V2 · {signal==="NO_TRADE"?"Evidence insufficient":typeof margin==="number"?`Margin ${margin.toFixed(2)}`:"Decision ready"}</span>
  </button>;
}

export function DecisionDetailsPanel({prediction}:Props){
  const de=prediction?.decision_engine??{}; const forecast=prediction?.forecast??{};
  const blockers=de.blocking_reasons??de.trade_blockers??[];
  const sources=forecast.forecast_sources??[];
  return <section className="chart-decision-panel" aria-label="Active Drive V2 decision details">
    <header><div><small>Decision Engine</small><h4>Active Drive V2</h4></div><span className="engine-health-dot">Authoritative</span></header>
    <div className="chart-detail-grid">
      <div><small>Signal</small><b>{prediction?.direction??"NO_TRADE"}</b></div>
      <div><small>Forecast</small><b>{forecast.available?(forecast.forecast_type==="informational"?"Informational":"Actionable"):"Unavailable"}</b></div>
      <div><small>Directional confidence</small><b>{prediction?.direction==="NO_TRADE"?"Not established":pct(de.directional_confidence)}</b></div>
      <div><small>Abstention confidence</small><b>{pct(de.abstention_confidence)}</b></div>
      <div><small>Total evidence</small><b>{de.total_evidence??"—"}</b></div>
      <div><small>Point margin</small><b>{de.point_margin??"—"} / {de.required_point_margin??"—"}</b></div>
    </div>
    <div className="chart-regime"><small>Market regime</small><b>{formatMarketRegime(de.market_regime??prediction?.regime)}</b></div>
    {forecast.available&&<p className="chart-info-notice">{forecast.forecast_type==="informational"?"Informational forecast — not a trade signal":forecast.reason}</p>}
    {!forecast.available&&<ForecastUnavailableState reason={forecast.reason}/>} 
    {!!blockers.length&&<div className="chart-detail-list"><small>Blocking reasons</small>{blockers.slice(0,3).map((x:string)=><span key={x}>{x}</span>)}</div>}
    {!!sources.length&&<div className="chart-detail-list"><small>Forecast composition</small>{sources.slice(0,5).map((s:any)=><span key={`${s.type}:${s.name}`}>{s.name} · {s.direction} · {pct(s.weight)}</span>)}</div>}
    <div className="chart-execution-state">Execution: <b>{forecast.trade_actionable&&de.eligible_for_execution?"Eligible":"Blocked / informational only"}</b></div>
  </section>;
}

export function DecisionDetailsBottomSheet({prediction,open,onClose}:{prediction:any;open:boolean;onClose:()=>void}){
  if(!open)return null;
  return <div className="chart-sheet-backdrop" role="presentation" onClick={onClose}><div className="chart-sheet" role="dialog" aria-modal="true" aria-label="Decision details" onClick={e=>e.stopPropagation()}><button className="chart-sheet-close" onClick={onClose} aria-label="Close decision details"><X size={20}/></button><DecisionDetailsPanel prediction={prediction}/></div></div>;
}

export function ForecastLegend({forecast,bands}:{forecast:any;bands:boolean}){
  if(!forecast?.available)return <span className="pc-legend-item pc-legend-off"><i className="pc-swatch solid cyan"/> Forecast unavailable</span>;
  return <><span className="pc-legend-item"><i className="pc-swatch solid cyan"/> {forecast.forecast_type==="informational"?"AI Forecast — Informational":"AI Forecast"}</span>{bands&&<><span className="pc-legend-item"><i className="pc-swatch dash green"/> Upper Band</span><span className="pc-legend-item"><i className="pc-swatch dash red"/> Lower Band</span></>}</>;
}

export function ForecastUnavailableState({reason}:{reason?:string|null}){return <p className="forecast-unavailable">Forecast unavailable{reason?`: ${reason}`:""}</p>}
