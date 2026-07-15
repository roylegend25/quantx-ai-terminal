import { useEffect, useMemo, useState } from "react";
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../../services/api";
import { formatMarketRegime } from "../../lib/activeDrive";
import "./LiveDecisionPanel.css";

type Props={symbol:string;timeframe:string;prediction:any};
const fmt=(value:any,unit?:string)=>{
  if(value==null)return "Not available";
  if(typeof value!=="number")return String(value);
  if(unit==="ratio"||unit==="return")return `${(value*100).toFixed(1)}%`;
  if(unit==="x")return `${value.toFixed(2)}x`;
  return Number.isInteger(value)?String(value):value.toFixed(2);
};

function RequirementTrack({item}:{item:any}){
  const numeric=typeof item.value==="number"&&typeof item.required==="number"&&item.required>0;
  const pct=numeric?Math.min(100,Math.max(0,item.value/item.required*100)):item.status==="passed"?100:0;
  return <article className={`decision-requirement ${item.status}`}>
    <div><b>{item.label}</b><span>{fmt(item.value,item.unit)}{item.required!=null?` / ${fmt(item.required,item.unit)}`:""}</span></div>
    <div className="requirement-track"><i style={{width:`${pct}%`}}/></div>
    <small>{item.status.toUpperCase()} · {item.explanation||item.reason_code||"Awaiting the next validated evaluation"}</small>
  </article>;
}

export default function LiveDecisionPanel({symbol,timeframe,prediction}:Props){
  const [snapshot,setSnapshot]=useState<any>(null);
  const [now,setNow]=useState(Date.now());
  const decisionId=prediction?.decision_id??prediction?.decision_engine?.decision_id;
  useEffect(()=>{
    let cancelled=false;
    const load=async()=>{
      try{
        const next=await api.liveDecision(symbol,timeframe);
        if(cancelled||next.symbol!==symbol||next.timeframe!==timeframe)return;
        // Never combine a DB history snapshot with a different chart decision.
        if(decisionId&&next.decision_id!==decisionId)return;
        setSnapshot(next);
      }catch{/* current prediction remains visible while diagnostics catch up */}
    };
    load(); const id=window.setInterval(load,10_000);
    return()=>{cancelled=true;window.clearInterval(id)};
  },[symbol,timeframe,decisionId]);
  useEffect(()=>{const id=window.setInterval(()=>setNow(Date.now()),1000);return()=>window.clearInterval(id)},[]);
  useEffect(()=>setSnapshot(null),[symbol,timeframe]);

  const de=prediction?.decision_engine??{};
  const requirements=snapshot?.requirements??[];
  const nextAt=snapshot?.next_evaluation_at?Date.parse(snapshot.next_evaluation_at):null;
  const seconds=nextAt==null?null:Math.max(0,Math.ceil((nextAt-now)/1000));
  const history=(snapshot?.decision_history??[]).map((x:any)=>({...x,label:new Date(x.timestamp).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit"})}));
  const blockers=snapshot?.blocking_reasons??de.blocking_reasons??[];
  const regime=formatMarketRegime(snapshot?.market_regime??de.market_regime);
  const counts=snapshot?.candidate_counts??{total:de.candidates?.length??0,eligible:de.candidates?.filter((x:any)=>x.eligible).length??0};
  const signal=snapshot?.signal??prediction?.direction??"NO_TRADE";
  const totals=useMemo(()=>({
    long:snapshot?.long_points??de.long_points??0,
    short:snapshot?.short_points??de.short_points??0,
  }),[snapshot,de.long_points,de.short_points]);
  return <section className="live-decision-section" aria-label="Active Drive V2 live decision flow">
    <div className="live-decision-head">
      <div><small>Authoritative decision</small><h3>{signal} <span>Active Drive V2</span></h3></div>
      <div className="next-evaluation"><small>Next V2 evaluation</small><b>{seconds==null?"Awaiting schedule":seconds===0?"Evaluating…":`00:${String(seconds).padStart(2,"0")}`}</b></div>
    </div>
    <div className="decision-snapshot-strip">
      <span><small>LONG</small><b className="green">{totals.long.toFixed(3)}</b></span>
      <span><small>SHORT</small><b className="red">{totals.short.toFixed(3)}</b></span>
      <span><small>Regime</small><b>{regime}</b></span>
      <span><small>Candidates</small><b>{counts.eligible??0} eligible / {counts.total??0}</b></span>
      <span><small>Execution</small><b className={snapshot?.execution_eligible?"green":"yellow"}>{snapshot?.execution_eligible?"Eligible":"Blocked"}</b></span>
    </div>
    {snapshot?.source_family_totals&&<div className="source-family-strip">{Object.entries(snapshot.source_family_totals).map(([name,value]:any)=><span key={name}><b>{name}</b><i className="green">LONG +{value.long.toFixed(2)}</i><i className="red">SHORT -{value.short.toFixed(2)}</i><small>{value.eligible} eligible</small></span>)}</div>}
    <div className="live-decision-grid">
      <div className="requirements-panel">
        <header><h4>Decision requirements</h4><small>Decision {snapshot?.decision_id??decisionId??"loading"}</small></header>
        <div className="requirement-list">{requirements.length?requirements.map((x:any)=><RequirementTrack item={x} key={x.id}/>):<p>Loading the matching decision requirements…</p>}</div>
        {!!blockers.length&&<div className="decision-blockers"><b>Why blocked</b>{blockers.map((x:string)=><span key={x}>{x}</span>)}</div>}
      </div>
      <div className="decision-flow-panel">
        <header><div><h4>Decision Flow</h4><small>One point per completed V2 evaluation · last {history.length}/120</small></div><span>{symbol} · {timeframe}</span></header>
        <div className="decision-flow-chart">
          {history.length?<ResponsiveContainer width="100%" height="100%"><LineChart data={history} margin={{top:8,right:10,bottom:2,left:-15}}>
            <XAxis dataKey="label" minTickGap={45} tick={{fontSize:10}}/>
            <YAxis tick={{fontSize:10}}/>
            <Tooltip contentStyle={{background:"var(--panel)",border:"1px solid var(--border)"}} labelFormatter={(_,p)=>p?.[0]?.payload?.decision_id??""}/>
            <ReferenceLine y={history.at(-1)?.required_point_margin??4} stroke="var(--yellow)" strokeDasharray="4 4"/>
            <Line type="monotone" dataKey="long_points" stroke="var(--green)" dot={false} strokeWidth={2} isAnimationActive={false}/>
            <Line type="monotone" dataKey="short_points" stroke="var(--red)" dot={false} strokeWidth={2} isAnimationActive={false}/>
            <Line type="monotone" dataKey="evidence" stroke="var(--cyan)" dot={false} strokeWidth={2} isAnimationActive={false}/>
            <Line type="monotone" dataKey="point_margin" stroke="var(--yellow)" dot={false} strokeWidth={2} isAnimationActive={false}/>
          </LineChart></ResponsiveContainer>:<p className="analytics-empty">Decision history will appear after completed V2 evaluations.</p>}
        </div>
        <div className="decision-flow-legend"><span className="green">LONG points</span><span className="red">SHORT points</span><span className="cyan">Evidence</span><span className="yellow">Point margin</span></div>
      </div>
    </div>
  </section>;
}
