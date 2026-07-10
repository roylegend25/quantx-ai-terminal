import { memo } from "react";
import { CheckCircle2, Clock3, XCircle } from "lucide-react";
import { fmtNum } from "../../lib/format";

type Props = {
  /** prediction.decision_engine for the selected symbol/interval */
  decision: any;
  regime?: string | null;
};

/** Green: trade allowed. Red: risk blocked a directional signal.
 *  Yellow: waiting - no decision yet or no qualifying signal. */
function overallState(decision: any): "allowed" | "blocked" | "waiting" {
  if (!decision) return "waiting";
  if (decision.trade_allowed) return "allowed";
  if (decision.final_direction === "LONG" || decision.final_direction === "SHORT") return "blocked";
  return "waiting";
}

function DecisionReasoningCard({ decision, regime }: Props) {
  const state = overallState(decision);
  const stateTone = state === "allowed" ? "green" : state === "blocked" ? "red" : "yellow";
  const StateIcon = state === "allowed" ? CheckCircle2 : state === "blocked" ? XCircle : Clock3;

  const direction = decision?.final_direction ?? "—";
  const dirTone = direction === "LONG" ? "green" : direction === "SHORT" ? "red" : "yellow";
  const reasons: string[] = decision?.top_reasons ?? [];
  const blockers: string[] = decision?.trade_blockers ?? [];
  const activeLabel = decision
    ? decision.mode === "champion_ml" && decision.active_model
      ? `${decision.active_model.model_type ?? decision.active_model.name} ${decision.active_model.version ?? ""} (Champion)`
      : (decision.strategy_used ?? "ensemble").replace(/_/g, " ")
    : "—";

  return (
    <div className="dec-reasoning">
      <div className={`dec-verdict ${stateTone}`}>
        <StateIcon size={18} />
        <div>
          <b>
            {direction === "NO_TRADE" ? "NO TRADE" : direction}
            {state === "allowed" ? " · trade allowed" : state === "blocked" ? " · blocked by risk" : " · waiting"}
          </b>
          <p className="regime-desc">{decision?.risk_reason ?? "Waiting for the decision engine."}</p>
        </div>
      </div>

      <div className="dec-kv-row">
        <div>
          <span className="tile-label">Confidence</span>
          <b className={`tile-value ${dirTone}`}>{decision ? `${fmtNum(decision.final_confidence, 1)}%` : "—"}</b>
        </div>
        <div>
          <span className="tile-label">Required</span>
          <b className="tile-value">
            {typeof decision?.required_confidence === "number" ? `${fmtNum(decision.required_confidence, 1)}%` : "—"}
          </b>
        </div>
        <div>
          <span className="tile-label">Active Engine</span>
          <b className="tile-value dec-small">{activeLabel}</b>
        </div>
        <div>
          <span className="tile-label">Market Regime</span>
          <b className="tile-value dec-small">{(decision?.market_regime ?? regime ?? "—").toString().replace(/_/g, " ")}</b>
        </div>
      </div>

      {reasons.length > 0 && (
        <div className="dec-reasons">
          <span className="tile-label">Top Reasons</span>
          <ul>
            {reasons.slice(0, 5).map((r, i) => (
              <li key={i} className={r.startsWith("Blocked") ? "red" : ""}>
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {blockers.length > 0 && (
        <div className="dec-blockers">
          <span className="tile-label">Trade Blockers</span>
          <div className="chip-row">
            {blockers.map((b, i) => (
              <span className="chip red" key={i}>
                {b}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(DecisionReasoningCard);
