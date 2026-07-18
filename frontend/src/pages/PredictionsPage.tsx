import { useMemo, useState } from "react";
import Card from "../components/Layout/Card";
import AIDecisionCenter from "../components/Dashboard/AIDecisionCenter";
import MarketRegimePanel from "../components/Dashboard/MarketRegimePanel";
import ModelVotesPanel from "../components/Dashboard/ModelVotesPanel";
import SourceHealthPanel from "../components/Analysis/SourceHealthPanel";
import { formatMarketRegime, pct01, type SignalCandidate } from "../lib/activeDrive";
import type { AppData } from "../hooks/useAppData";

const TABS=["Overview","ML Models","Strategies","Quant","Market Regime","Performance","Prediction History"] as const;
type Tab=typeof TABS[number];
function CandidateCards({items}:{items:SignalCandidate[]}) { return items.length?<div className="analysis-source-grid">{items.map(c=><article className="analysis-source-card" key={`${c.source_type}-${c.name}-${c.version}`}><div className="vote-head"><b>{c.name.replaceAll("_"," ")}</b><span className={`chip ${c.direction==="LONG"?"green":c.direction==="SHORT"?"red":"yellow"}`}>{c.direction}</span></div><div className="engine-metric-grid"><span>{c.final_points>=0?"+":""}{c.final_points.toFixed(2)} points</span><span>{pct01(c.calibrated_confidence)} confidence</span><span>{c.resolved_samples} resolved</span><span>{c.evidence_tier.replaceAll("_"," ")}</span></div><p className="regime-desc">{c.reason}</p></article>)}</div>:<p className="analytics-empty">No candidates generated for this symbol/timeframe.</p>; }

export default function PredictionsPage(props:AppData){
  const [tab,setTab]=useState<Tab>("Overview"); const d=props.prediction?.decision_engine; const candidates:SignalCandidate[]=d?.candidates??[];
  const grouped=useMemo(()=>({ml:candidates.filter(c=>c.source_type==="ml"||c.source_type==="ml_model"),strategy:candidates.filter(c=>c.source_type==="strategy"),quant:candidates.filter(c=>c.source_type==="quant"||c.source_type==="quant_model")}),[candidates]);
  return <div className="page-grid analysis-page"><Card title="Analysis" full><div className="analysis-tabs" role="tablist">{TABS.map(t=><button role="tab" aria-selected={tab===t} className={tab===t?"active":""} onClick={()=>setTab(t)} key={t}>{t}</button>)}</div></Card>
    {tab==="Overview"&&<><Card title="Source Health" full><SourceHealthPanel symbol={props.symbol} timeframe={props.interval} decision={d}/></Card><Card title="Active Drive V2 Decision" full><AIDecisionCenter symbol={props.symbol} prediction={props.prediction} lastUpdated={props.lastUpdated}/></Card><Card title="Current Decision Contributors" full><ModelVotesPanel candidates={candidates} finalDirection={d?.final_signal} enginePoints={{long:d?.long_points,short:d?.short_points}}/></Card></>}
    {tab==="ML Models"&&<Card title="ML Models" full><CandidateCards items={grouped.ml}/></Card>}
    {tab==="Strategies"&&<Card title="Strategies" full><CandidateCards items={grouped.strategy}/></Card>}
    {tab==="Quant"&&<Card title="Quant Studies" full><p className="regime-desc">Deterministic statistical studies are shown separately from machine-learning models and trading strategies.</p><CandidateCards items={grouped.quant}/></Card>}
    {tab==="Market Regime"&&<><Card title={formatMarketRegime(d?.market_regime)} full><MarketRegimePanel prediction={props.prediction}/><div className="chip-row regime-chips"><span className="chip">Trend: {d?.market_regime?.trend??"unknown"}</span><span className="chip">Volatility: {d?.market_regime?.volatility??"unknown"}</span><span className="chip">Liquidity: {d?.market_regime?.liquidity??"unknown"}</span><span className="chip">Derivatives: {d?.market_regime?.derivatives??"unknown"}</span></div></Card></>}
    {tab==="Performance"&&<Card title="Resolved Performance" full><p className="regime-desc">Only fixed-horizon resolved ledger records count. Unresolved predictions and shadow candidates are excluded.</p><CandidateCards items={candidates.filter(c=>c.resolved_samples>0)}/></Card>}
    {tab==="Prediction History"&&<Card title="Prediction History" full><p className="regime-desc">Current decision {d?.decision_id??"pending persistence"} · {d?.history?.resolved_sample_count??0} resolved source records. Use the chart history overlay for symbol/timeframe outcomes.</p></Card>}
  </div>;
}
