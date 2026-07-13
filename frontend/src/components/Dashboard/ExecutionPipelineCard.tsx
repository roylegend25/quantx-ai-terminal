import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Clock, FlaskConical, RefreshCw, ShieldCheck, XCircle } from "lucide-react";
import AutoCardTable from "../Responsive/AutoCardTable";
import { EXECUTION_ATTEMPT_COLUMNS, ExecutionAttemptDetail } from "../../lib/executionAttemptColumns";
import { fmtLocalDateTime, fmtPct, fmtUsd } from "../../lib/format";
import { api } from "../../services/api";

const CHECKLIST_LABELS: [string, string][] = [
  ["model_approved", "Model Approved"],
  ["risk_gate_approved", "Risk Gate Approved"],
  ["margin_approved", "Margin Approved"],
  ["quantity_valid", "Quantity Valid"],
  ["exchange_filters_passed", "Exchange Filters Passed"],
  ["binance_validation_passed", "Binance Validation Passed"],
];

function na(value: unknown, render: (v: any) => string = String): string {
  return value === null || value === undefined ? "Not available" : render(value);
}

const STAGE_LABELS: Record<string, string> = {
  signal_generated: "Signal Generated",
  champion_model: "Champion Model",
  risk_gate: "Risk Gate",
  position_sizing: "Position Sizing",
  quantity_calculation: "Quantity Calculation",
  binance_validation: "Binance Validation",
  entry_order_submitted: "Entry Order Submitted",
  entry_order_accepted: "Entry Order Accepted",
  protective_tp_submitted: "Protective TP Submitted",
  protective_sl_submitted: "Protective SL Submitted",
  protection_confirmed: "Protection Confirmed",
};

// Fixed display order regardless of the backend's recording order (position
// sizing happens before the risk gate evaluates the clamped notional) - see
// app/trading/execution_pipeline.STAGE_ORDER. Phase 27: a filled entry is
// not a complete trade until protection is confirmed against Binance's own
// open orders - the last three stages exist specifically so "entry
// accepted" is never mistaken for "trade done".
const STAGE_DISPLAY_ORDER = [
  "signal_generated", "champion_model", "risk_gate", "position_sizing",
  "quantity_calculation", "binance_validation", "entry_order_submitted", "entry_order_accepted",
  "protective_tp_submitted", "protective_sl_submitted", "protection_confirmed",
];

type Props = {
  symbol: string;
  pipeline: any;
  simulation?: any;
  statusChecklist?: any;
  loading?: boolean;
  errored?: boolean;
  onRefresh: () => void | Promise<void>;
  showToast: (message: string, tone?: "success" | "error") => void;
};

export default function ExecutionPipelineCard({
  symbol, pipeline, simulation, statusChecklist, loading, errored, onRefresh, showToast,
}: Props) {
  const [logsOpen, setLogsOpen] = useState(false);
  const [logs, setLogs] = useState<any>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [testBusy, setTestBusy] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  // Debug 2.pdf section 9: a historical FAILURE starts collapsed - a red
  // fatal-looking reason from an old, resolved attempt must never carry
  // the same visual weight as a genuinely current blocker. Keyed by
  // last_attempt_at so a newer historical attempt doesn't inherit a stale
  // dismiss/expand choice left over from a previous one.
  const [expandedHistoricalAt, setExpandedHistoricalAt] = useState<string | null>(null);

  async function toggleLogs() {
    const next = !logsOpen;
    setLogsOpen(next);
    if (next && !logs) {
      setLogsLoading(true);
      try {
        setLogs(await api.binanceExecutionLogs(100));
      } catch (e: any) {
        showToast(e?.message || "Failed to load execution logs", "error");
      } finally {
        setLogsLoading(false);
      }
    }
  }

  async function handleTestOrder() {
    if (
      !window.confirm(
        `Place Binance's smallest legal order for ${symbol} with real funds, then immediately close it?\n\n` +
          "This spends a small amount on taker fees on both legs. The exact exchange response (success or " +
          "rejection) will be shown."
      )
    ) {
      return;
    }
    setTestBusy(true);
    setTestResult(null);
    try {
      const r = await api.binanceTestOrder({ symbol, confirm: true });
      setTestResult(r);
      showToast(
        r.submit?.ok ? "Test order placed and closed successfully" : `Test order failed: ${r.submit?.reason}`,
        r.submit?.ok ? "success" : "error"
      );
      await onRefresh();
      if (logsOpen) setLogs(await api.binanceExecutionLogs(100).catch(() => logs));
    } catch (e: any) {
      showToast(e?.message || "Test order request failed", "error");
      setTestResult({ error: e?.message || "Request failed" });
    } finally {
      setTestBusy(false);
    }
  }

  if (errored) {
    return (
      <div className="regime-focus blocked">
        <span className="tile-label">Execution Pipeline unavailable</span>
        <p className="regime-desc">Could not read execution status. Try refreshing.</p>
        <div className="controls" style={{ marginTop: 10 }}>
          <button className="mini-btn" onClick={() => onRefresh()}>
            Refresh
          </button>
        </div>
      </div>
    );
  }

  const stagesByName: Record<string, any> = {};
  for (const s of pipeline?.stages || []) stagesByName[s.stage] = s;
  const orderedStages = STAGE_DISPLAY_ORDER.map(
    (name) => stagesByName[name] || { stage: name, status: "waiting", reason: null, at: null }
  );

  const signalApproved = !!pipeline?.signal_approved;
  const executionAttempted = !!pipeline?.execution_attempted;
  const executionOk = pipeline?.execution_ok;
  // The core correction this card exists for: a "signal approved" verdict
  // must never be shown as if it were a successful execution. Equally
  // important (Debug 1.pdf section 3/8): a FAILED attempt only counts as
  // today's active blocker if it happened during the current signal cycle
  // - is_historical_attempt (computed backend-side against last_decision_at)
  // routes anything older into the "Previous Execution Attempt -
  // Historical" branch below instead, so a rate-limit cooldown that
  // expired hours ago can never reappear as a fresh "Execution Failed".
  const showExecutionFailedBanner =
    signalApproved && executionAttempted && executionOk === false && !pipeline?.is_historical_attempt;

  return (
    <div>
      {/* -------- Section 11: Professional status banner -------- */}
      {statusChecklist && (
        <div className={`regime-focus ${statusChecklist.overall === "READY" ? "allowed" : "blocked"}`} style={{ marginBottom: 12 }}>
          <span className="tile-label">
            {statusChecklist.overall === "READY" ? (
              <>
                <CheckCircle2 size={13} className="green" style={{ verticalAlign: "-2px" }} /> Trade Approved
              </>
            ) : (
              <>
                <XCircle size={13} className="red" style={{ verticalAlign: "-2px" }} /> Execution Blocked
              </>
            )}
          </span>
          {statusChecklist.overall === "READY" ? (
            <ul className="reason-list" style={{ paddingLeft: 0, listStyle: "none" }}>
              {CHECKLIST_LABELS.map(([key, label]) => {
                const value = statusChecklist[key];
                return (
                  <li key={key} className={value === true ? "green" : value === false ? "red" : "yellow"}>
                    {value === true ? "✓" : value === false ? "✗" : "…"} {label}
                  </li>
                );
              })}
              <li className="green">✓ Order Ready</li>
            </ul>
          ) : (
            <>
              <p className="regime-desc">Reason: {statusChecklist.blocked_reason || "Not ready"}</p>
              {statusChecklist.missing_amount != null && (
                <p className="regime-desc">
                  Need {fmtUsd(statusChecklist.missing_amount)} more.{" "}
                  Recommendation: reduce position size or deposit {fmtUsd(statusChecklist.missing_amount)}.
                </p>
              )}
            </>
          )}
        </div>
      )}

      {/* -------- Debug 1.pdf section 8-9: Binance API status, kept
          separate from execution status - a cooldown with no fresh
          attempt this cycle is never shown as "Execution Failed". -------- */}
      {!loading && pipeline?.binance_rate_limited && !showExecutionFailedBanner && (
        <div className="regime-focus" style={{ marginBottom: 12 }}>
          <span className="tile-label">
            <RefreshCw size={13} style={{ verticalAlign: "-2px" }} /> Binance API cooling down — showing cached data
            {pipeline.binance_retry_after_seconds != null ? ` (retry in ${Math.ceil(pipeline.binance_retry_after_seconds)}s)` : ""}
          </span>
        </div>
      )}

      {/* -------- Phase 30: Current Protection Check --------
          Computed fresh right now via the same shared resolver the real
          risk gate uses (protection.resolve_protection /
          check_all_positions_protected) - NEVER derived from a historical
          BinanceExecutionAttempt row, so an old protection failure can
          never masquerade as today's active blocker. */}
      {!loading && pipeline?.current_protection_check && (
        <div
          className={`regime-focus ${
            pipeline.current_protection_check.passed === true
              ? "allowed"
              : pipeline.current_protection_check.passed === false
                ? "blocked"
                : ""
          }`}
          style={{ marginBottom: 12 }}
        >
          <span className="tile-label">
            <ShieldCheck size={13} style={{ verticalAlign: "-2px" }} /> Current Protection Check
          </span>
          <b
            className={`tile-value ${
              pipeline.current_protection_check.passed === true
                ? "green"
                : pipeline.current_protection_check.passed === false
                  ? "red"
                  : ""
            }`}
          >
            {pipeline.current_protection_check.passed === true
              ? "PASS"
              : pipeline.current_protection_check.passed === false
                ? "FAIL"
                : "UNKNOWN"}
            {" — "}
            {pipeline.current_protection_check.detail}
          </b>
        </div>
      )}

      {!loading && pipeline && (
        <div className={`regime-focus ${showExecutionFailedBanner ? "blocked" : executionOk ? "allowed" : ""}`}>
          {showExecutionFailedBanner ? (
            <>
              <span className="tile-label">
                <CheckCircle2 size={13} className="green" style={{ verticalAlign: "-2px" }} /> Signal Approved
                {"  ·  "}
                <XCircle size={13} className="red" style={{ verticalAlign: "-2px" }} /> Execution Failed
              </span>
              <b className="tile-value red">Reason: {pipeline.final_reason || "Execution failed"}</b>
            </>
          ) : executionAttempted ? (
            <>
              <span className="tile-label">
                Previous Execution Attempt ({pipeline.symbol})
                {pipeline.is_historical_attempt && (
                  <span className="badge" style={{ marginLeft: 8, fontSize: 10 }}>
                    <Clock size={10} style={{ verticalAlign: "-1px" }} /> Historical
                  </span>
                )}
              </span>
              {pipeline.is_historical_attempt && !executionOk && expandedHistoricalAt !== pipeline.last_attempt_at ? (
                <>
                  <span className="regime-desc" style={{ fontSize: 12, opacity: 0.75 }}>
                    An old attempt failed - it is no longer today's blocker.
                  </span>
                  <button
                    className="mini-btn"
                    style={{ marginTop: 6 }}
                    onClick={() => setExpandedHistoricalAt(pipeline.last_attempt_at)}
                  >
                    View historical failure details
                  </button>
                </>
              ) : (
                <>
                  <b className={`tile-value ${executionOk ? "green" : pipeline.is_historical_attempt ? "" : "red"}`}>
                    {executionOk ? "Order Accepted" : `Failed — ${pipeline.final_reason || "unknown reason"}`}
                  </b>
                  {pipeline.is_historical_attempt && !executionOk && (
                    <div>
                      <button
                        className="mini-btn"
                        style={{ marginTop: 6 }}
                        onClick={() => setExpandedHistoricalAt(null)}
                      >
                        Dismiss historical attempt
                      </button>
                    </div>
                  )}
                </>
              )}
            </>
          ) : (
            <>
              <span className="tile-label">Execution Status ({pipeline.symbol})</span>
              <b className="tile-value">No live order attempt yet this cycle</b>
            </>
          )}
          {pipeline.last_attempt_at && (
            <p className="regime-desc">
              {executionAttempted ? "Recorded at" : "Last attempt"}: {fmtLocalDateTime(pipeline.last_attempt_at)}
            </p>
          )}
        </div>
      )}

      <div className="pipeline-stages">
        {orderedStages.map((s, i) => (
          <div key={s.stage} className="pipeline-stage-row">
            <div className="pipeline-stage-marker">
              <span className={`pipeline-dot pipeline-dot-${s.status}`} />
              {i < orderedStages.length - 1 && <span className="pipeline-connector" />}
            </div>
            <div className="pipeline-stage-body">
              <b className={`pipeline-stage-label ${s.status}`}>{STAGE_LABELS[s.stage] || s.stage}</b>
              {s.reason && <span className="pipeline-stage-reason">{s.reason}</span>}
            </div>
          </div>
        ))}
      </div>

      {/* -------- Sections 8+9: optimal sizing + pre-trade simulation -------- */}
      {simulation && (
        <>
          <div className="card-title" style={{ fontSize: 13, marginTop: 16 }}>Pre-Trade Simulation — if order is placed now</div>
          <div className="kv-grid">
            <div><span className="tile-label">Entry Price</span><b className="tile-value">{na(simulation.entry_price, fmtUsd)}</b></div>
            <div className="align-right"><span className="tile-label">Side</span><b className={`tile-value ${simulation.side === "LONG" ? "green" : "red"}`}>{na(simulation.side)}</b></div>
            <div><span className="tile-label">Take Profit</span><b className="tile-value green">{na(simulation.take_profit, fmtUsd)}</b></div>
            <div className="align-right"><span className="tile-label">Stop Loss</span><b className="tile-value red">{na(simulation.stop_loss, fmtUsd)}</b></div>
            <div><span className="tile-label">Expected Profit</span><b className="tile-value green">{na(simulation.expected_profit, fmtUsd)}</b></div>
            <div className="align-right"><span className="tile-label">Expected Loss</span><b className="tile-value red">{na(simulation.expected_loss, fmtUsd)}</b></div>
            <div><span className="tile-label">Fees</span><b className="tile-value">{na(simulation.fees, (v) => fmtUsd(v, 4))}</b></div>
            <div className="align-right"><span className="tile-label">Liquidation Price</span><b className="tile-value orange">{na(simulation.liquidation_price, fmtUsd)}</b></div>
            <div><span className="tile-label">Required Margin</span><b className="tile-value">{na(simulation.required_margin, fmtUsd)}</b></div>
            <div className="align-right"><span className="tile-label">Confidence</span><b className="tile-value">{na(simulation.confidence, (v) => fmtPct(v, 1))}</b></div>
            <div><span className="tile-label">Champion Model</span><b className="tile-value">{na(simulation.active_model)}</b></div>
            <div className="align-right"><span className="tile-label">Market Regime</span><b className="tile-value">{na(simulation.market_regime, (v) => String(v).replace(/_/g, " "))}</b></div>
            <div><span className="tile-label">Optimal Quantity</span><b className="tile-value">{na(simulation.optimal_quantity)}</b></div>
            <div className="align-right"><span className="tile-label">Optimal Leverage</span><b className="tile-value">{na(simulation.optimal_leverage, (v) => `${v}x`)}</b></div>
            <div><span className="tile-label">Optimal Margin</span><b className="tile-value">{na(simulation.optimal_margin, fmtUsd)}</b></div>
            <div className="align-right"><span className="tile-label">Expected Risk %</span><b className="tile-value red">{na(simulation.expected_risk_pct, (v) => fmtPct(v, 1))}</b></div>
            <div><span className="tile-label">Expected Reward %</span><b className="tile-value green">{na(simulation.expected_reward_pct, (v) => fmtPct(v, 1))}</b></div>
          </div>
        </>
      )}

      <div className="controls" style={{ marginTop: 16 }}>
        <button className="mini-btn" onClick={toggleLogs}>
          {logsOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Execution Logs (last 100)
        </button>
        <button className="mini-btn" disabled={testBusy} onClick={handleTestOrder}>
          <FlaskConical size={13} /> {testBusy ? "Testing…" : "Test Order"}
        </button>
      </div>

      {testResult && (
        <div className={`regime-focus ${testResult.submit?.ok ? "allowed" : "blocked"}`} style={{ marginTop: 12 }}>
          <span className="tile-label">Test Order Result</span>
          {testResult.error ? (
            <p className="regime-desc">
              <AlertTriangle size={13} /> {testResult.error}
            </p>
          ) : (
            <>
              <b className={`tile-value ${testResult.submit?.ok ? "green" : "red"}`}>
                Submit: {testResult.submit?.ok ? "Accepted" : `Rejected — ${testResult.submit?.reason}`}
              </b>
              {testResult.close && (
                <p className="regime-desc">
                  Close: {testResult.close.ok ? "Closed successfully" : `Failed — ${testResult.close.reason}`}
                </p>
              )}
              <pre className="exec-raw-response">{JSON.stringify(testResult, null, 2)}</pre>
            </>
          )}
        </div>
      )}

      {logsOpen && (
        <div style={{ marginTop: 14 }}>
          {logsLoading ? (
            <p className="analytics-empty">Loading execution logs…</p>
          ) : (
            <AutoCardTable
              columns={EXECUTION_ATTEMPT_COLUMNS}
              rows={logs?.attempts || []}
              keyField={(a: any) => a.id}
              titleColumn="symbol"
              statusColumn="status"
              renderDetail={(a: any) => <ExecutionAttemptDetail a={a} />}
              emptyMessage="No execution attempts recorded yet"
            />
          )}
        </div>
      )}
    </div>
  );
}
