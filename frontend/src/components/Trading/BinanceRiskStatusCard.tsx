import Card from "../Layout/Card";
import { fmtPct, fmtUsd } from "../../lib/format";

/** Response shape of GET /api/trading/binance/risk-status - the real-money
 *  counterpart of the Paper tab's RiskStatusCard. Every field is read from
 *  live Binance account state or the server's own trading-control state
 *  (app.api.trading_control.binance_risk_status) - nothing here is ever
 *  copied from the paper ledger. */
export type BinanceRiskStatus = {
  active_mode?: string;
  account_connected?: boolean;
  available?: boolean;
  reason?: string | null;
  risk_level?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | "UNKNOWN";
  risk_score?: number | null;
  blocked_reasons?: string[];
  binance_live_enabled_by_server?: boolean;
  binance_live_unlocked_by_user?: boolean;
  kill_switch_active?: boolean;
  wallet_balance?: number | null;
  available_balance?: number | null;
  margin_balance?: number | null;
  free_margin?: number | null;
  margin_used?: number | null;
  margin_usage_pct?: number | null;
  daily_loss_used_usdt?: number | null;
  daily_loss_used_pct?: number | null;
  max_daily_loss_usdt?: number | null;
  total_notional_exposure?: number | null;
  max_notional_per_trade?: number | null;
  nearest_liquidation_distance_pct?: number | null;
  current_effective_leverage?: number | null;
  max_leverage?: number | null;
  open_positions?: number | null;
  max_open_positions?: number | null;
};

const TONE_BY_LEVEL: Record<string, "green" | "yellow" | "orange" | "red"> = {
  LOW: "green",
  MEDIUM: "yellow",
  HIGH: "orange",
  CRITICAL: "red",
};

function na(value: unknown, render: (v: any) => string = String): string {
  return value === null || value === undefined ? "Not available" : render(value);
}

type Props = {
  data: BinanceRiskStatus | null;
  loading?: boolean;
  errored?: boolean;
  onRefresh?: () => void;
};

export default function BinanceRiskStatusCard({ data, loading, errored, onRefresh }: Props) {
  if (errored || (!loading && !data)) {
    return (
      <Card title="Binance Live Risk Status" full className="live-danger-card">
        <div className="regime-focus blocked">
          <span className="tile-label">Binance Live Risk Status unavailable</span>
          <p className="regime-desc">
            {data?.reason ||
              "Could not read the Binance account. This can mean the account isn't connected, an IP-whitelist or " +
                "permission issue, or the exchange is temporarily unreachable."}
          </p>
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

  const level = data?.risk_level || "UNKNOWN";
  const tone = TONE_BY_LEVEL[level] || "yellow";
  const score = typeof data?.risk_score === "number" ? data.risk_score : null;
  const reasons = data?.blocked_reasons || [];
  const [primaryReason, ...secondaryReasons] = reasons;

  return (
    <div className="stack-card full">
      <div className="card-title">Binance Live Risk Status</div>
      <p className="regime-desc">Real account metrics — Live Risk Gate.</p>

      <div style={{ maxWidth: 340, margin: "0 auto", width: "100%" }}>
        <div className={`half-dial tone-${tone}`} style={{ ["--pct" as any]: score ?? 0 }}>
          <div className="half-dial-fill" />
          <div className="half-dial-inner">
            <b className={tone}>{level}</b>
            <span>{score !== null ? fmtPct(score, 1) : "Risk score unavailable"}</span>
          </div>
        </div>
      </div>

      {reasons.length > 0 && (
        <div className={`regime-focus blocked`}>
          <span className="tile-label">Live trading not ready</span>
          <b className="tile-value red">{primaryReason}</b>
          {secondaryReasons.length > 0 && (
            <ul className="reason-list">
              {secondaryReasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="kv-grid" style={{ marginTop: 6 }}>
        <div>
          <span className="tile-label">Margin Usage</span>
          <b className="tile-value">{na(data?.margin_usage_pct, (v) => fmtPct(v, 1))}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Daily Loss Used</span>
          <b className="tile-value red">{na(data?.daily_loss_used_pct, (v) => fmtPct(v, 1))}</b>
        </div>
        <div>
          <span className="tile-label">Max Daily Loss</span>
          <b className="tile-value">{na(data?.max_daily_loss_usdt, fmtUsd)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Current Notional Exposure</span>
          <b className="tile-value">{na(data?.total_notional_exposure, fmtUsd)}</b>
        </div>
        <div>
          <span className="tile-label">Max Notional / Trade</span>
          <b className="tile-value">{na(data?.max_notional_per_trade, fmtUsd)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Open Positions</span>
          <b className="tile-value">
            {na(data?.open_positions)}
            {data?.max_open_positions != null ? ` of ${data.max_open_positions}` : ""}
          </b>
        </div>
        <div>
          <span className="tile-label">Nearest Liquidation Distance</span>
          <b className="tile-value orange">{na(data?.nearest_liquidation_distance_pct, (v) => fmtPct(v, 1))}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Available Balance</span>
          <b className="tile-value">{na(data?.available_balance, fmtUsd)}</b>
        </div>
        <div>
          <span className="tile-label">Wallet Balance</span>
          <b className="tile-value">{na(data?.wallet_balance, fmtUsd)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Free Margin</span>
          <b className="tile-value">{na(data?.free_margin, fmtUsd)}</b>
        </div>
        <div>
          <span className="tile-label">Current Leverage</span>
          <b className="tile-value">{na(data?.current_effective_leverage, (v) => `${v}x`)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Max Leverage</span>
          <b className="tile-value">{na(data?.max_leverage, (v) => `${v}x`)}</b>
        </div>
        <div>
          <span className="tile-label">Kill Switch</span>
          <b className={`tile-value ${data?.kill_switch_active ? "red" : "green"}`}>
            {data?.kill_switch_active ? "ACTIVE" : "OFF"}
          </b>
        </div>
        <div className="align-right">
          <span className="tile-label">Server Live Lock</span>
          <b className={`tile-value ${data?.binance_live_enabled_by_server ? "green" : "red"}`}>
            {data?.binance_live_enabled_by_server ? "OPEN" : "ENGAGED"}
          </b>
        </div>
        <div>
          <span className="tile-label">User Live Unlock</span>
          <b className={`tile-value ${data?.binance_live_unlocked_by_user ? "green" : "red"}`}>
            {data?.binance_live_unlocked_by_user ? "UNLOCKED" : "LOCKED"}
          </b>
        </div>
        <div className="align-right">
          <span className="tile-label">Active Execution Mode</span>
          <b className="tile-value">{data?.active_mode || "Not available"}</b>
        </div>
      </div>
    </div>
  );
}
