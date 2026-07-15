import { memo } from "react";
import { Brain, Crown } from "lucide-react";
import { fmtLocalDateTime, fmtNum } from "../../lib/format";
import { engineName, formatMarketRegime } from "../../lib/activeDrive";

type Props={status:any;decision:any;symbol:string;interval:string};
function Tile({label,value,tone=""}:{label:string;value:any;tone?:string}) { return <div className="analytics-tile"><span className="tile-label">{label}</span><b className={`tile-value ${tone}`}>{value}</b></div>; }

function DecisionEngineCard({status,decision,symbol,interval}:Props) {
  const v2=decision?.engine==="active_drive_v2";
  return <div className="dec-engine">
    <div className="dec-mode-row">
      <span className={`dec-mode-badge ${v2?"green":"cyan"}`}><Brain size={14}/>{engineName(decision)}</span>
      <span className="chip">{symbol} · {interval}</span><span className="chip green">{decision?.health||"healthy"}</span>
    </div>
    <div className="analytics-grid dec-metrics">
      <Tile label="LONG Points" value={decision?.long_points??0} tone="green"/><Tile label="SHORT Points" value={decision?.short_points??0} tone="red"/>
      <Tile label="Point Margin" value={`${decision?.point_margin??0} / ${decision?.required_point_margin??"—"}`}/><Tile label="Evidence" value={(decision?.evidence_tier??"insufficient_evidence").replaceAll("_"," ")}/>
      <Tile label="Candidates" value={decision?.candidate_count??0}/><Tile label="Regime" value={formatMarketRegime(decision?.market_regime)}/>
      <Tile label="Data" value={decision?.data_status?.stale?"Cached / stale":decision?.data_status?.source??decision?.data_status??"—"}/><Tile label="Method" value="Weighted Ensemble"/>
    </div>
    {status?.has_champion&&<div className="dec-model-row"><Crown size={15}/><span>Top ML candidate: {status.model_type??"Champion"} {status.version??""}</span><span>{typeof status.accuracy==="number"?`${fmtNum(status.accuracy*100,1)}% validated accuracy`:"Accuracy not available"}</span><span>{status.trained_at?fmtLocalDateTime(status.trained_at):""}</span></div>}
  </div>;
}
export default memo(DecisionEngineCard);
