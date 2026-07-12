import { useState } from "react";
import Card from "../Layout/Card";
import { api } from "../../services/api";
import { LiveUnlockModal, type TradingStatus } from "./TradingShared";

type Props = {
  status: TradingStatus | null;
  onChanged: () => Promise<void> | void;
  showToast: (message: string, tone?: "success" | "error") => void;
};

/** First blocking reason (in the exact priority order the spec calls for)
 *  for starting the live-risk ceremony, or null once it's safe to open it.
 *  `binance_account_connected` is the precise signed-read field (falls
 *  back to the older `binance_connected` alias so this still works against
 *  any response shape that hasn't been through the connectivity fix). */
export function userLiveUnlockBlockedReason(status: TradingStatus | null): string | null {
  if (!status) return "Loading Binance status…";
  if (!status.binance_live_enabled_by_server) return "Enable Server Live Lock first.";
  if (!status.binance_configured) return "Binance API is not configured.";
  const accountConnected = status.binance_account_connected ?? status.binance_connected;
  if (accountConnected === false) return "Binance account signed read failed.";
  if (status.kill_switch_active) return "Kill switch is active.";
  return null;
}

/** Step 2 of the Binance Real page's readiness sequence: the per-session
 *  live-risk ceremony, surfaced as its own card/button instead of being
 *  buried behind the Paper/Binance segmented toggle. Locking and unlocking
 *  both reuse the existing endpoints (unlock-live / lock-live) - no new
 *  backend surface. */
export default function UserLiveConfirmationCard({ status, onChanged, showToast }: Props) {
  const [showModal, setShowModal] = useState(false);
  const [busy, setBusy] = useState(false);

  const unlocked = !!status?.binance_live_unlocked_by_user;
  const reason = userLiveUnlockBlockedReason(status);
  const isLive = status?.active_mode === "BINANCE_LIVE";

  const lock = async () => {
    setBusy(true);
    try {
      await api.lockBinanceLive();
      showToast("User live access locked", "success");
      await onChanged();
    } catch (e: any) {
      showToast(e?.message || "Failed to lock user live access", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="User Live Confirmation" full className={unlocked ? "live-danger-card" : ""}>
      <div className="controls" style={{ marginBottom: 10 }}>
        <span className={`badge ${unlocked ? "badge-red" : ""}`}>User Live Unlock: {unlocked ? "Active" : "Locked"}</span>
        <span className={`badge ${status?.binance_live_enabled_by_server ? "badge-red" : ""}`}>
          Server Live Lock: {status?.binance_live_enabled_by_server ? "Enabled" : "Disabled"}
        </span>
        <span className={`badge ${isLive ? "badge-red" : ""}`}>Active Mode: {isLive ? "BINANCE_LIVE" : "PAPER"}</span>
        <span className={`badge ${status?.kill_switch_active ? "badge-red" : ""}`}>
          Kill Switch: {status?.kill_switch_active ? "Active" : "Inactive"}
        </span>
      </div>
      <p className="regime-desc">
        {unlocked
          ? "Live-risk confirmation is complete for this session. Switch the active execution mode to Binance Live to allow real orders."
          : "Complete the live-risk ceremony (typed phrase and safety acknowledgements) to unlock Binance real-money trading for this session."}
      </p>
      <div className="controls">
        {unlocked ? (
          <>
            <button className="btn-long" disabled>
              User Live Unlock: Active
            </button>
            <button className="mini-btn" disabled={busy} onClick={lock}>
              Lock User Live Access
            </button>
          </>
        ) : (
          <button
            className="btn-danger"
            disabled={!!reason || busy}
            title={reason || ""}
            onClick={() => setShowModal(true)}
          >
            Complete Live Risk Confirmation
          </button>
        )}
      </div>
      {reason && !unlocked && (
        <p className="regime-desc" style={{ marginTop: 8 }}>
          {reason}
        </p>
      )}
      {showModal && status && (
        <LiveUnlockModal
          status={status}
          onClose={() => setShowModal(false)}
          onChanged={onChanged}
          showToast={showToast}
        />
      )}
    </Card>
  );
}
