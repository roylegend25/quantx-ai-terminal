import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, OctagonX, ShieldCheck } from "lucide-react";
import Card from "../Layout/Card";
import { api } from "../../services/api";
import { fmtPct, fmtUsd, toneClass, toneOf } from "../../lib/format";
import { ModeBadge, ModeToggle, useTradingStatus } from "./TradingShared";

const POLL_MS = 15000;

type Props = {
  showToast: (message: string, tone?: "success" | "error") => void;
};

/** Dashboard row (Phase 23): Active Trading Mode switch + the two separate
 *  portfolio summaries - paper and Binance real - side by side, never merged. */
export default function TradingModeRow({ showToast }: Props) {
  const { status, reload } = useTradingStatus(POLL_MS);
  const [paper, setPaper] = useState<any>(null);
  const [binance, setBinance] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [p, b] = await Promise.all([
      api.paperSummary().catch(() => null),
      api.binanceSummary().catch(() => null),
    ]);
    if (p) setPaper(p);
    if (b) setBinance(b);
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const onChanged = useCallback(async () => {
    await Promise.all([reload(), load()]);
  }, [reload, load]);

  const killSwitch = async (active: boolean) => {
    setBusy(true);
    try {
      await api.killSwitch(active, active ? "dashboard emergency stop" : undefined);
      showToast(active ? "Kill switch ACTIVATED — all trading halted" : "Kill switch deactivated", "success");
      await onChanged();
    } catch (e: any) {
      showToast(e?.message || "Kill switch action failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const mode = status?.active_mode || "PAPER";

  return (
    <div className="dash-row-3 trading-mode-row">
      <Card title="Active Trading Mode" className={mode === "BINANCE_LIVE" ? "live-danger-card" : ""}>
        <div className="trading-mode-card">
          <div className="controls">
            <ModeBadge mode={mode} killSwitch={status?.kill_switch_active} />
          </div>
          <ModeToggle status={status} onChanged={onChanged} showToast={showToast} />
          <p className="regime-desc">
            {mode === "PAPER" && "Bot executes simulated paper orders only."}
            {mode === "BINANCE_LIVE_LOCKED" &&
              (status?.binance_live_enabled_by_server
                ? "Binance Real Money is locked — complete the risk acknowledgement to allow real orders."
                : "Binance Real Money Trading is locked. Enable BINANCE_LIVE_ENABLED=true on the server and complete the risk acknowledgement to allow real orders.")}
            {mode === "BINANCE_LIVE" && "⚠ Bot places REAL Binance Futures orders with real funds."}
          </p>
          {status?.kill_switch_active ? (
            <button className="btn-long" disabled={busy} onClick={() => killSwitch(false)}>
              <ShieldCheck size={14} /> Re-enable Trading
            </button>
          ) : (
            <button
              className="btn-danger"
              disabled={busy}
              onClick={() => {
                if (window.confirm("EMERGENCY STOP?\n\nStops the bot and blocks ALL new paper and real trades. Open positions are NOT auto-closed.")) {
                  killSwitch(true);
                }
              }}
            >
              <OctagonX size={14} /> Emergency Stop
            </button>
          )}
        </div>
      </Card>

      <Card title="Paper Trading Portfolio" right={<span className="badge">PAPER</span>}>
        <div className="kv-grid">
          <div>
            <span className="tile-label">Balance</span>
            <b className="tile-value">{fmtUsd(paper?.balance)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Equity</span>
            <b className="tile-value">{fmtUsd(paper?.equity)}</b>
          </div>
          <div>
            <span className="tile-label">Open Positions</span>
            <b className="tile-value">{paper?.open_positions ?? "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Daily PnL</span>
            <b className={`tile-value ${toneClass(toneOf(paper?.daily_pnl))}`}>{fmtUsd(paper?.daily_pnl)}</b>
          </div>
          <div>
            <span className="tile-label">Total PnL</span>
            <b className={`tile-value ${toneClass(toneOf(paper?.realized_pnl))}`}>{fmtUsd(paper?.realized_pnl)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Win Rate</span>
            <b className="tile-value">{paper?.win_rate != null ? fmtPct(paper.win_rate) : "—"}</b>
          </div>
        </div>
      </Card>

      <Card
        title="Binance Real Portfolio"
        className={mode === "BINANCE_LIVE" ? "live-danger-card" : ""}
        right={
          <span className={`badge ${status?.binance_connected ? "badge-green" : ""}`}>
            {status?.binance_configured
              ? status?.binance_connected
                ? "API: Connected"
                : "API: Configured"
              : "API: Not Configured"}
          </span>
        }
      >
        {binance?.available ? (
          <div className="kv-grid">
            <div>
              <span className="tile-label">Wallet Balance</span>
              <b className="tile-value">{fmtUsd(binance?.total_wallet_balance)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Available</span>
              <b className="tile-value">{fmtUsd(binance?.available_balance)}</b>
            </div>
            <div>
              <span className="tile-label">Margin Used</span>
              <b className="tile-value">{fmtUsd(binance?.margin_used)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Open Positions</span>
              <b className="tile-value">{binance?.open_positions ?? "—"}</b>
            </div>
            <div>
              <span className="tile-label">Unrealized PnL</span>
              <b className={`tile-value ${toneClass(toneOf(binance?.unrealized_pnl))}`}>
                {fmtUsd(binance?.unrealized_pnl)}
              </b>
            </div>
            <div className="align-right">
              <span className="tile-label">Daily PnL</span>
              <b className={`tile-value ${toneClass(toneOf(binance?.daily_pnl))}`}>{fmtUsd(binance?.daily_pnl)}</b>
            </div>
          </div>
        ) : (
          <p className="regime-desc">
            <AlertTriangle size={13} /> Binance account unavailable{binance?.reason ? ` — ${binance.reason}` : ""}
          </p>
        )}
      </Card>
    </div>
  );
}
