import Card from "../Layout/Card";
import { fmtLocalDateTime, fmtPct, fmtUsd } from "../../lib/format";

/** Response shape of GET /api/trading/binance/decision-status - the
 *  real-money counterpart of the Paper tab's Bot Decision Status card.
 *  Model/strategy direction and confidence come from the shared prediction
 *  pipeline every mode reads; final_direction, risk_gate_allowed and every
 *  blocked reason reflect the LIVE gate only (mode, locks, kill switch,
 *  real account data) - never the paper ledger. */
export type BinanceDecisionStatus = {
  status?: "NO_DATA";
  message?: string;
  symbol?: string;
  timeframe?: string;
  model_direction?: string | null;
  strategy_direction?: string | null;
  final_direction?: "LONG" | "SHORT" | "NO_TRADE" | "BLOCKED";
  confidence?: number | null;
  required_confidence?: number | null;
  risk_gate_allowed?: boolean;
  risk_gate_reason?: string | null;
  blocked_reasons?: string[];
  active_model?: string | null;
  active_strategy?: string | null;
  intended_notional?: number | null;
  intended_leverage?: number | null;
  take_profit?: number | null;
  stop_loss?: number | null;
  last_decision_at?: string | null;
  last_execution_attempt_at?: string | null;
  last_order_status?: string | null;
  active_mode?: string;
};

function na(value: unknown, render: (v: any) => string = String): string {
  return value === null || value === undefined ? "Not available" : render(value);
}

type Props = {
  data: BinanceDecisionStatus | null;
  loading?: boolean;
  errored?: boolean;
  onRefresh?: () => void;
};

export default function BinanceDecisionStatusCard({ data, loading, errored, onRefresh }: Props) {
  if (errored) {
    return (
      <Card title="Binance Bot Decision Status" full className="live-danger-card">
        <div className="regime-focus blocked">
          <span className="tile-label">Binance Bot Decision Status unavailable</span>
          <p className="regime-desc">Could not read the live decision feed. Try refreshing.</p>
        </div>
        {onRefresh && (
          <div className="controls" style={{ marginTop: 14 }}>
            <button className="mini-btn" onClick={onRefresh}>
              Refresh Binance Status
            </button>
          </div>
        )}
      </Card>
    );
  }

  if (!loading && (!data || data.status === "NO_DATA")) {
    return (
      <Card title="Binance Bot Decision Status" full>
        <div className="regime-focus">
          <span className="tile-label">No Live Decision Yet</span>
          <p className="regime-desc">
            {data?.message || "No Binance live decision has been evaluated yet."}
          </p>
        </div>
      </Card>
    );
  }

  const blocked = data?.final_direction === "BLOCKED" || data?.risk_gate_allowed === false;
  const reasons = data?.blocked_reasons || [];
  const secondaryReasons = reasons.slice(1);

  return (
    <Card title="Binance Bot Decision Status" full>
      <div className={`regime-focus ${data ? (blocked ? "blocked" : "allowed") : ""}`}>
        <span className="tile-label">Current Trade</span>
        <b className={`tile-value ${data ? (blocked ? "red" : "green") : ""}`}>
          {data?.final_direction || "Awaiting decision"}
        </b>
        <p className="regime-desc">
          {!data
            ? "Waiting for the live risk gate to evaluate this signal."
            : blocked
              ? `Blocked: ${data?.risk_gate_reason || "Live risk gate did not pass."}`
              : "Live risk gate clears this signal — Binance execution is armed."}
        </p>
        {secondaryReasons.length > 0 && (
          <ul className="reason-list">
            {secondaryReasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="kv-grid" style={{ marginTop: 14 }}>
        <div>
          <span className="tile-label">Current Confidence</span>
          <b className="tile-value">{na(data?.confidence, (v) => fmtPct(v, 1))}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Required Confidence</span>
          <b className="tile-value">{na(data?.required_confidence, (v) => fmtPct(v, 1))}</b>
        </div>
        <div>
          <span className="tile-label">Live Risk Gate</span>
          <b className={`tile-value ${data ? (data.risk_gate_allowed ? "green" : "red") : ""}`}>
            {data ? (data.risk_gate_allowed ? "ALLOWED" : "BLOCKED") : "Not available"}
          </b>
        </div>
        <div className="align-right">
          <span className="tile-label">Active Mode</span>
          <b className="tile-value">{data?.active_mode || "Not available"}</b>
        </div>
        <div>
          <span className="tile-label">Model Direction</span>
          <b className="tile-value">{na(data?.model_direction)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Strategy Direction</span>
          <b className="tile-value">{na(data?.strategy_direction)}</b>
        </div>
        <div>
          <span className="tile-label">Active Model</span>
          <b className="tile-value">{na(data?.active_model)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Active Strategy</span>
          <b className="tile-value">{na(data?.active_strategy)}</b>
        </div>
        <div>
          <span className="tile-label">Symbol</span>
          <b className="tile-value">{na(data?.symbol)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Timeframe</span>
          <b className="tile-value">{na(data?.timeframe)}</b>
        </div>
        <div>
          <span className="tile-label">Intended Notional</span>
          <b className="tile-value">{na(data?.intended_notional, fmtUsd)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Intended Leverage</span>
          <b className="tile-value">{na(data?.intended_leverage, (v) => `${v}x`)}</b>
        </div>
        <div>
          <span className="tile-label">Intended TP</span>
          <b className="tile-value">{na(data?.take_profit, fmtUsd)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Intended SL</span>
          <b className="tile-value">{na(data?.stop_loss, fmtUsd)}</b>
        </div>
        <div>
          <span className="tile-label">Last Decision</span>
          <b className="tile-value">{na(data?.last_decision_at, fmtLocalDateTime)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Last Execution Attempt</span>
          <b className="tile-value">{na(data?.last_execution_attempt_at, fmtLocalDateTime)}</b>
        </div>
        <div>
          <span className="tile-label">Last Binance Order Result</span>
          <b className="tile-value">{na(data?.last_order_status)}</b>
        </div>
      </div>
    </Card>
  );
}
