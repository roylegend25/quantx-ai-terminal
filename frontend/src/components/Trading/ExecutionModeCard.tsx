import { useState } from "react";
import Card from "../Layout/Card";
import { api } from "../../services/api";
import type { TradingStatus } from "./TradingShared";

type Props = {
  status: TradingStatus | null;
  onChanged: () => Promise<void> | void;
  showToast: (message: string, tone?: "success" | "error") => void;
};

/** Reasons "Switch to Binance Live" is blocked, checked client-side before
 *  calling POST /api/trading/mode - the endpoint itself only rejects an
 *  unconfigured account (see backend/app/trading/modes.set_mode), so
 *  without this the request would silently degrade to
 *  BINANCE_LIVE_LOCKED instead of telling the user why. */
function blockedReason(status: TradingStatus | null): string | null {
  if (!status) return "Loading Binance status…";
  if (!status.binance_live_enabled_by_server) return "Server Live Lock is OFF — enable it in Server Trading Control first.";
  if (!status.binance_live_unlocked_by_user) return "Complete User Live Confirmation first.";
  if (status.kill_switch_active) return "Kill switch is active.";
  return null;
}

function modeLabel(mode: string): string {
  if (mode === "BINANCE_LIVE") return "BINANCE_LIVE";
  if (mode === "BINANCE_LIVE_LOCKED") return "BINANCE_LIVE (locked)";
  return "PAPER";
}

/** Step 3 of the Binance Real page's readiness sequence: the compact
 *  execution-mode switch that replaces the old Paper/Binance segmented
 *  toggle on this page. Switching to Binance Live still requires the
 *  server lock, user unlock, and kill-switch-off gates - enforced here so
 *  the blocked reason is exact instead of a silent no-op. */
export default function ExecutionModeCard({ status, onChanged, showToast }: Props) {
  const [busy, setBusy] = useState(false);
  const mode = status?.active_mode || "PAPER";
  const isLive = mode === "BINANCE_LIVE";
  const reason = blockedReason(status);

  const switchToLive = async () => {
    if (reason) {
      showToast(reason, "error");
      return;
    }
    if (!window.confirm("Switch execution mode to Binance Live? Real orders may be placed once the risk gate approves each trade.")) {
      return;
    }
    setBusy(true);
    try {
      await api.setTradingMode("BINANCE_LIVE");
      showToast("Execution mode switched to Binance Live", "success");
      await onChanged();
    } catch (e: any) {
      showToast(e?.message || "Failed to switch mode", "error");
    } finally {
      setBusy(false);
    }
  };

  const switchToPaper = async () => {
    setBusy(true);
    try {
      await api.lockBinanceLive();
      showToast("Switched back to Paper trading", "success");
      await onChanged();
    } catch (e: any) {
      showToast(e?.message || "Failed to switch mode", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Execution Mode" className={isLive ? "live-danger-card" : ""}>
      <div className="kv-grid">
        <div>
          <span className="tile-label">Current</span>
          <b className={`tile-value ${mode === "BINANCE_LIVE" ? "red" : mode === "BINANCE_LIVE_LOCKED" ? "orange" : ""}`}>
            {modeLabel(mode)}
          </b>
        </div>
      </div>
      {!isLive && reason && (
        <p className="regime-desc" style={{ marginTop: 8 }}>
          {reason}
        </p>
      )}
      <div className="controls" style={{ marginTop: 12 }}>
        <button className="btn-danger" disabled={busy || isLive || !!reason} title={reason || ""} onClick={switchToLive}>
          Switch to Binance Live
        </button>
        <button className="mini-btn" disabled={busy || mode === "PAPER"} onClick={switchToPaper}>
          Switch back to Paper
        </button>
      </div>
    </Card>
  );
}
