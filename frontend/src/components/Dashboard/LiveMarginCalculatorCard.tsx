import { useState } from "react";
import { RefreshCw, Wand2 } from "lucide-react";
import { fmtPct, fmtUsd } from "../../lib/format";
import { api } from "../../services/api";
import { useAdminServerConfig } from "../../hooks/useAdminServerConfig";

const GAUGE_TONE: Record<string, "green" | "yellow" | "orange" | "red"> = {
  GREEN: "green",
  YELLOW: "yellow",
  ORANGE: "orange",
  RED: "red",
};

function na(value: unknown, render: (v: any) => string = String): string {
  return value === null || value === undefined ? "Not available" : render(value);
}

type Props = {
  symbol: string;
  data: any;
  loading?: boolean;
  errored?: boolean;
  liveActive: boolean;
  onRefresh: () => void | Promise<void>;
  showToast: (message: string, tone?: "success" | "error") => void;
};

export default function LiveMarginCalculatorCard({ symbol, data, loading, errored, liveActive, onRefresh, showToast }: Props) {
  const [autoConfiguring, setAutoConfiguring] = useState(false);
  const { forbidden: adminForbidden, reload: reloadAdminConfig } = useAdminServerConfig();

  async function handleAutoConfigure() {
    if (!data?.recommendation) return;
    const rec = data.recommendation;
    const safeNotional = Math.max(0, Math.floor(rec.recommended_max_notional * 100) / 100);
    if (
      !window.confirm(
        `Set max position size to $${safeNotional.toFixed(2)} (from $${rec.current_setting_notional.toFixed(2)})?\n\n` +
          "Leverage is left unchanged. This writes to the server .env (a timestamped backup is created first) " +
          "and reloads immediately - no restart needed."
      )
    ) {
      return;
    }
    setAutoConfiguring(true);
    try {
      await api.adminUpdateRiskLimits({ BINANCE_MAX_NOTIONAL_PER_TRADE: safeNotional });
      showToast(`Max position size updated to ${fmtUsd(safeNotional)}`, "success");
      await Promise.all([reloadAdminConfig(), onRefresh()]);
    } catch (e: any) {
      showToast(e?.message || "Auto Configure Risk failed", "error");
    } finally {
      setAutoConfiguring(false);
    }
  }

  if (!liveActive) {
    return (
      <div className="stack-card full">
        <div className="card-title">Live Margin Calculator</div>
        <p className="regime-desc">Switch to Binance Live Trading to see real-time margin calculations.</p>
      </div>
    );
  }

  if (errored || (!loading && !data?.available)) {
    return (
      <div className="stack-card full">
        <div className="card-title">Live Margin Calculator</div>
        <div className="regime-focus blocked">
          <span className="tile-label">Live Margin Calculator unavailable</span>
          <p className="regime-desc">{data?.reason || "Could not read the Binance account."}</p>
        </div>
        <div className="controls" style={{ marginTop: 10 }}>
          <button className="mini-btn" onClick={() => onRefresh()}>
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
      </div>
    );
  }

  const account = data?.account;
  const market = data?.market;
  const sizing = data?.position_sizing;
  const rec = data?.recommendation;
  const breakdown = data?.breakdown_at_current_setting;
  const capacity = data?.risk_capacity;
  const tone = capacity ? GAUGE_TONE[capacity.level] || "yellow" : "yellow";

  return (
    <div className="stack-card full">
      <div className="card-title">Live Margin Calculator</div>
      <p className="regime-desc">Refreshes every 2 seconds — {symbol} · real Binance account &amp; exchange filters.</p>

      {/* -------- Section 7: Risk Capacity gauge -------- */}
      <div style={{ maxWidth: 340, margin: "10px auto 0", width: "100%" }}>
        <div className={`half-dial tone-${tone}`} style={{ ["--pct" as any]: capacity?.score ?? 0 }}>
          <div className="half-dial-fill" />
          <div className="half-dial-inner">
            <b className={tone}>Risk Capacity — {capacity?.level || "—"}</b>
            <span>{capacity ? fmtPct(capacity.score, 1) : "—"}</span>
          </div>
        </div>
      </div>

      {/* -------- Section 1: Account -------- */}
      <div className="card-title" style={{ fontSize: 13, marginTop: 8 }}>Account</div>
      <div className="kv-grid">
        <div><span className="tile-label">Wallet Balance</span><b className="tile-value">{na(account?.wallet_balance, fmtUsd)}</b></div>
        <div className="align-right"><span className="tile-label">Available Balance</span><b className="tile-value">{na(account?.available_balance, fmtUsd)}</b></div>
        <div><span className="tile-label">Margin Mode</span><b className="tile-value">{na(account?.margin_mode)}</b></div>
        <div className="align-right"><span className="tile-label">Symbol</span><b className="tile-value">{na(data?.symbol)}</b></div>
        <div><span className="tile-label">Initial Margin (Used)</span><b className="tile-value">{na(account?.margin_used, fmtUsd)}</b></div>
        <div className="align-right"><span className="tile-label">Free Margin</span><b className="tile-value">{na(account?.free_margin, fmtUsd)}</b></div>
        <div><span className="tile-label">Maintenance Margin</span><b className="tile-value">{na(account?.maintenance_margin_used, fmtUsd)}</b></div>
        <div className="align-right"><span className="tile-label">Unrealized PnL</span><b className={`tile-value ${(account?.unrealized_pnl ?? 0) >= 0 ? "green" : "red"}`}>{na(account?.unrealized_pnl, fmtUsd)}</b></div>
        <div><span className="tile-label">Liquidation Buffer</span><b className="tile-value orange">{na(account?.liquidation_buffer_pct, (v) => fmtPct(v, 1))}</b></div>
        <div className="align-right"><span className="tile-label">Maximum Buying Power</span><b className="tile-value">{na(account?.maximum_buying_power, fmtUsd)}</b></div>
        <div><span className="tile-label">Current Leverage</span><b className="tile-value">{na(account?.current_leverage, (v) => `${v}x`)}</b></div>
        <div className="align-right"><span className="tile-label">Current Price</span><b className="tile-value">{na(market?.current_price, fmtUsd)}</b></div>
      </div>

      {/* -------- Section 2: Position Size Calculator -------- */}
      <div className="card-title" style={{ fontSize: 13, marginTop: 14 }}>Position Size Calculator</div>
      <div className="kv-grid">
        <div><span className="tile-label">Maximum Tradable Quantity</span><b className="tile-value">{na(sizing?.maximum_tradable_quantity)}</b></div>
        <div className="align-right"><span className="tile-label">Maximum Tradable Notional</span><b className="tile-value">{na(sizing?.maximum_tradable_notional, fmtUsd)}</b></div>
        <div><span className="tile-label">Required Margin (at max)</span><b className="tile-value">{na(sizing?.required_margin_at_max, fmtUsd)}</b></div>
        <div className="align-right"><span className="tile-label">Estimated Fees (at max)</span><b className="tile-value">{na(sizing?.estimated_fees_at_max, fmtUsd)}</b></div>
        <div><span className="tile-label">Safety Buffer</span><b className="tile-value">{na(sizing?.safety_buffer, fmtUsd)}</b></div>
        <div className="align-right"><span className="tile-label">Maximum Safe Position</span><b className="tile-value green">{na(sizing?.maximum_safe_position_notional, fmtUsd)}</b></div>
        <div><span className="tile-label">Maximum Aggressive Position</span><b className="tile-value red">{na(sizing?.maximum_aggressive_position_notional, fmtUsd)}</b></div>
      </div>

      {/* -------- Section 3: Calculation breakdown (never hidden) -------- */}
      <div className="card-title" style={{ fontSize: 13, marginTop: 14 }}>Calculation Breakdown</div>
      <div className={`regime-focus ${breakdown?.status === "PASS" ? "allowed" : "blocked"}`}>
        <div className="kv-grid" style={{ marginTop: 0 }}>
          <div><span className="tile-label">Wallet Balance</span><b className="tile-value">{na(account?.wallet_balance, fmtUsd)}</b></div>
          <div className="align-right"><span className="tile-label">Available Margin</span><b className="tile-value">{na(breakdown?.available_margin, fmtUsd)}</b></div>
          <div><span className="tile-label">Requested Position</span><b className="tile-value">{na(breakdown?.notional, fmtUsd)}</b></div>
          <div className="align-right"><span className="tile-label">Leverage</span><b className="tile-value">{na(breakdown?.leverage, (v) => `${v}x`)}</b></div>
          <div><span className="tile-label">Exchange Initial Margin</span><b className="tile-value">{na(breakdown?.initial_margin, fmtUsd)}</b></div>
          <div className="align-right"><span className="tile-label">Estimated Trading Fees</span><b className="tile-value">{na(breakdown?.estimated_fees, (v) => fmtUsd(v, 4))}</b></div>
          <div><span className="tile-label">Maintenance Margin</span><b className="tile-value">{na(breakdown?.maintenance_margin, (v) => fmtUsd(v, 4))}</b></div>
          <div className="align-right"><span className="tile-label">Safety Buffer</span><b className="tile-value">{na(breakdown?.safety_buffer, fmtUsd)}</b></div>
          <div><span className="tile-label">Total Required</span><b className="tile-value">{na(breakdown?.total_required, fmtUsd)}</b></div>
          <div className="align-right">
            <span className="tile-label">Status</span>
            <b className={`tile-value ${breakdown?.status === "PASS" ? "green" : "red"}`}>
              {breakdown?.status === "PASS" ? "✓ PASS" : "✗ FAIL"}
            </b>
          </div>
          {breakdown?.status === "FAIL" && (
            <>
              <div><span className="tile-label">Available</span><b className="tile-value">{na(breakdown?.available_margin, fmtUsd)}</b></div>
              <div className="align-right"><span className="tile-label">Missing</span><b className="tile-value red">{na(breakdown?.missing, fmtUsd)}</b></div>
            </>
          )}
        </div>
      </div>

      {/* -------- Section 4: Recommendation + Auto Configure -------- */}
      <div className="card-title" style={{ fontSize: 13, marginTop: 14 }}>Maximum Safe Position Recommendation</div>
      <div className="kv-grid">
        <div><span className="tile-label">Current Balance</span><b className="tile-value">{na(account?.wallet_balance, fmtUsd)}</b></div>
        <div className="align-right"><span className="tile-label">Recommended</span><b className="tile-value green">{na(rec?.recommended_max_notional, fmtUsd)}</b></div>
        <div><span className="tile-label">Current Setting</span><b className="tile-value">{na(rec?.current_setting_notional, fmtUsd)}</b></div>
        <div className="align-right">
          <span className="tile-label">Status</span>
          <b className={`tile-value ${rec?.status === "OK" ? "green" : rec?.status === "TOO_HIGH" ? "red" : "yellow"}`}>
            {rec?.status === "OK" ? "OK" : rec?.status === "TOO_HIGH" ? "Too High" : rec?.status === "TOO_LOW" ? "Room to grow" : "—"}
          </b>
        </div>
        <div style={{ gridColumn: "1 / -1" }}>
          <span className="tile-label">{rec?.adjust_by < 0 ? "Reduce by" : rec?.adjust_by > 0 ? "Room to increase by" : "Adjustment"}</span>
          <b className={`tile-value ${rec?.adjust_by < 0 ? "red" : "green"}`}>{na(rec?.adjust_by, (v) => fmtUsd(Math.abs(v)))}</b>
        </div>
      </div>

      <div className="controls" style={{ marginTop: 12 }}>
        <button
          className="btn-long"
          disabled={autoConfiguring || adminForbidden || rec?.status === "OK" || !rec}
          title={adminForbidden ? "Admin privileges required" : rec?.status === "OK" ? "Already within the safe range" : ""}
          onClick={handleAutoConfigure}
        >
          <Wand2 size={14} /> {autoConfiguring ? "Applying…" : "Auto Configure Risk"}
        </button>
        <button className="mini-btn" onClick={() => onRefresh()}>
          <RefreshCw size={13} /> Refresh Now
        </button>
      </div>
    </div>
  );
}
