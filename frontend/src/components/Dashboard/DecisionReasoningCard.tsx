import { memo } from "react";
import { CheckCircle2, Clock3, XCircle } from "lucide-react";
import { engineName, formatMarketRegime, pct01 } from "../../lib/activeDrive";

type ExecutionOutcome = {
  attempted: boolean;
  ok: boolean | null;
  reason: string | null;
  /** True when this outcome is from an older signal cycle than the one on
   *  screen (see ExecutionPipelineCard / trading_control._is_historical_attempt) -
   *  a stale failure (e.g. an expired rate-limit cooldown) must never be
   *  shown as today's active blocker. */
  historical?: boolean;
} | null;

type Props = {
  /** prediction.decision_engine for the selected symbol/interval */
  decision: any;
  regime?: any;
  /** Ground truth from the most recent real Binance order attempt for this
   *  symbol (see useExecutionPipeline) - when the signal was approved but
   *  execution actually failed, this overrides the "trade allowed" verdict
   *  below so the two are never conflated. Omit/null on the Paper tab,
   *  where "trade allowed" already accurately describes what happens. */
  executionOutcome?: ExecutionOutcome;
};

/** Green: trade allowed. Red: risk blocked a directional signal.
 *  Yellow: waiting - no decision yet or no qualifying signal. */
function overallState(decision: any): "allowed" | "blocked" | "waiting" {
  if (!decision) return "waiting";
  if (decision.trade_allowed) return "allowed";
  if (decision.final_direction === "LONG" || decision.final_direction === "SHORT") return "blocked";
  return "waiting";
}

function DecisionReasoningCard({ decision, regime, executionOutcome }: Props) {
  const state = overallState(decision);
  // A signal the decision engine approved, but whose live execution
  // actually failed, must never be shown as simply "allowed" - see
  // ExecutionPipelineCard for the full stage-by-stage reason.
  const executionFailed =
    state === "allowed" &&
    !!executionOutcome?.attempted &&
    executionOutcome?.ok === false &&
    !executionOutcome?.historical;
  const stateTone = executionFailed ? "red" : state === "allowed" ? "green" : state === "blocked" ? "red" : "yellow";
  const StateIcon = executionFailed ? XCircle : state === "allowed" ? CheckCircle2 : state === "blocked" ? XCircle : Clock3;

  const direction = decision?.final_signal ?? decision?.final_direction ?? "—";
  const dirTone = direction === "LONG" ? "green" : direction === "SHORT" ? "red" : "yellow";
  const reasons: string[] = decision?.top_reasons ?? decision?.blocking_reasons ?? [];
  const blockers: string[] = decision?.blocking_reasons ?? decision?.trade_blockers ?? [];
  const activeLabel = engineName(decision);

  return (
    <div className="dec-reasoning">
      <div className={`dec-verdict ${stateTone}`}>
        <StateIcon size={18} />
        <div>
          {executionFailed ? (
            <>
              <b>
                <CheckCircle2 size={13} className="green" style={{ verticalAlign: "-2px" }} /> Signal Approved
                {"  ·  "}
                <XCircle size={13} className="red" style={{ verticalAlign: "-2px" }} /> Execution Failed
              </b>
              <p className="regime-desc">Reason: {executionOutcome?.reason || "Execution failed"}</p>
            </>
          ) : (
            <>
              <b>
                {direction === "NO_TRADE" ? "NO TRADE · Evidence insufficient" : direction}
                {state === "allowed" ? " · trade allowed" : state === "blocked" ? " · blocked by risk" : " · waiting"}
              </b>
              <p className="regime-desc">{blockers[0] ?? "Waiting for the decision engine."}</p>
            </>
          )}
        </div>
      </div>

      <div className="dec-kv-row">
        <div>
          <span className="tile-label">{direction === "NO_TRADE" ? "Directional confidence" : "Directional confidence"}</span>
          <b className={`tile-value ${dirTone}`}>{direction === "NO_TRADE" ? "Not established" : pct01(decision?.directional_confidence, 1)}</b>
        </div>
        <div>
          <span className="tile-label">Required</span>
          <b className="tile-value">
            {typeof decision?.required_confidence === "number" ? pct01(decision.required_confidence, 1) : "Not available"}
          </b>
        </div>
        <div>
          <span className="tile-label">Active Engine</span>
          <b className="tile-value dec-small">{activeLabel}</b>
        </div>
        <div>
          <span className="tile-label">Market Regime</span>
          <b className="tile-value dec-small">{formatMarketRegime(decision?.market_regime ?? regime)}</b>
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
