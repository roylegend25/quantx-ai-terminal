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
  /** The dashboard's currently-selected chart interval (e.g. "4h") -
   *  compared against currentPipeline.timeframe so a non-authoritative
   *  interval's own eligible_for_execution verdict is never mistaken for
   *  real execution approval. */
  interval?: string;
  /** GET /api/trading/pipeline/current (see useCurrentPipeline) - the one
   *  authoritative source for whether this decision is actually approved
   *  for paper execution. decision.trade_allowed alone is NEVER sufficient:
   *  it only reflects the freely-chosen chart interval's own gate, which
   *  may not be the timeframe the scheduler will ever actually execute. */
  currentPipeline?: any;
};

/** Green: approved for paper execution - now requires BOTH the selected
 *  interval's own gate passing AND the unified current-pipeline API
 *  reporting execution_status === "approved_for_paper_execution" /
 *  "approved_for_execution" for the SAME authoritative timeframe. Red: a
 *  directional LONG/SHORT candidate exists but a downstream gate blocked
 *  it before execution. Yellow/"waiting": either no decision has loaded
 *  yet, or the verdict is a genuine NO_TRADE abstention - both render the
 *  real reason text below, never a bare "waiting". */
function overallState(decision: any, currentPipeline: any, interval?: string): "allowed" | "blocked" | "waiting" {
  if (!decision) return "waiting";
  const samplingSameTimeframe = !currentPipeline || !interval || currentPipeline.timeframe === interval;
  const pipelineApproved =
    !currentPipeline ||
    (currentPipeline.execution_status === "approved_for_paper_execution" ||
      currentPipeline.execution_status === "approved_for_execution" ||
      currentPipeline.execution_status === "execution_pending" ||
      currentPipeline.execution_status === "paper_position_open" ||
      currentPipeline.execution_status === "position_open");
  if (decision.trade_allowed && samplingSameTimeframe && pipelineApproved) return "allowed";
  if (decision.final_direction === "LONG" || decision.final_direction === "SHORT") return "blocked";
  return "waiting";
}

function DecisionReasoningCard({ decision, regime, executionOutcome, interval, currentPipeline }: Props) {
  const state = overallState(decision, currentPipeline, interval);
  const timeframeMismatch =
    !!currentPipeline && !!interval && currentPipeline.timeframe !== interval &&
    (decision?.final_direction === "LONG" || decision?.final_direction === "SHORT");
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
                {direction === "NO_TRADE"
                  ? `NO TRADE · ${blockers[0] ?? "No actionable decision"}`
                  : state === "allowed"
                  ? `${direction} candidate — approved for paper execution`
                  : state === "blocked"
                  ? `${direction} candidate — blocked: ${blockers[0] ?? "risk gate"}`
                  : direction}
              </b>
              {!decision && <p className="regime-desc">Waiting for the decision engine - no verdict has loaded yet.</p>}
            </>
          )}
        </div>
      </div>

      {timeframeMismatch && (
        <p className="regime-desc" style={{ opacity: 0.8 }}>
          Showing {interval} — the authoritative execution timeframe is {currentPipeline.timeframe}. This candidate
          is informational only; it is not what the scheduler will act on.
        </p>
      )}

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

      {typeof decision?.point_margin === "number" && (
        <div className="dec-kv-row">
          <div>
            <span className="tile-label">Point Margin</span>
            <b className={`tile-value ${decision.point_margin_pass ? "green" : "red"}`}>
              {decision.point_margin.toFixed(2)} / required {decision.required_point_margin?.toFixed?.(2) ?? "—"}
            </b>
          </div>
          <div>
            <span className="tile-label">Configuration Scope</span>
            <b className="tile-value dec-small">
              {decision.configuration_scope ?? "—"} · v{decision.configuration_version ?? "—"}
            </b>
          </div>
        </div>
      )}

      {(decision?.active_indicators?.length > 0 || decision?.shadow_indicators?.length > 0 || decision?.disabled_indicators?.length > 0) && (
        <div className="dec-why-decided">
          <span className="tile-label">Why Bot Decided This</span>
          {decision?.active_indicators?.length > 0 && (
            <div className="chip-row" style={{ marginTop: 6 }}>
              <span className="regime-desc" style={{ marginRight: 6 }}>
                Contributed:
              </span>
              {decision.active_indicators.map((name: string) => (
                <span className="chip green" key={name}>
                  {name.replaceAll("_", " ")}
                </span>
              ))}
            </div>
          )}
          {decision?.shadow_indicators?.length > 0 && (
            <div className="chip-row" style={{ marginTop: 6 }}>
              <span className="regime-desc" style={{ marginRight: 6 }}>
                Shadow (excluded from ensemble):
              </span>
              {decision.shadow_indicators.map((name: string) => {
                const reason = decision?.exclusion_reasons?.[name];
                const starred = reason === "RECOMMENDED_FOR_REACTIVATION";
                return (
                  <span className="chip yellow" key={name} title={reason}>
                    {name.replaceAll("_", " ")}
                    {starred ? " ⭐" : ""}
                  </span>
                );
              })}
            </div>
          )}
          {decision?.disabled_indicators?.length > 0 && (
            <div className="chip-row" style={{ marginTop: 6 }}>
              <span className="regime-desc" style={{ marginRight: 6 }}>
                Manually disabled:
              </span>
              {decision.disabled_indicators.map((name: string) => (
                <span className="chip" key={name}>
                  {name.replaceAll("_", " ")}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(DecisionReasoningCard);
