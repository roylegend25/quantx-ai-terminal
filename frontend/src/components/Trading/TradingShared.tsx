import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle } from "lucide-react";
import { api } from "../../services/api";

/** Safe status from GET /api/trading/mode — the Phase 23 shape, plus the
 *  canonical Binance connectivity fields (app.api.exchange.
 *  binance_connectivity, merged in by the /mode handler) every page should
 *  read instead of computing its own connection status. Distinguishes
 *  public market reachability (binance_public_connected) from key
 *  configuration (binance_api_key/secret_configured) from the actual
 *  signed account read (binance_account_connected/binance_signed_read_ok)
 *  - `binance_connected` is kept as a back-compat alias of the latter. */
export type TradingStatus = {
  active_mode: "PAPER" | "BINANCE_LIVE_LOCKED" | "BINANCE_LIVE" | string;
  paper_available: boolean;
  binance_live_available: boolean;
  binance_configured: boolean;
  binance_connected?: boolean;
  binance_api_key_configured?: boolean;
  binance_api_secret_configured?: boolean;
  binance_public_connected?: boolean;
  binance_account_connected?: boolean;
  binance_signed_read_ok?: boolean;
  binance_account_error?: string | null;
  binance_live_enabled_by_server: boolean;
  binance_live_unlocked_by_user: boolean;
  can_trade_binance_live: boolean;
  reason: string;
  kill_switch_active: boolean;
  allowed_symbols: string[];
  max_leverage: number;
  max_notional_per_trade: number;
  max_daily_loss_usdt: number;
  credential_status?: {
    configured: boolean; connection_valid: boolean; permissions_valid: boolean;
    environment: "paper" | "testnet" | "real" | string; account_mode: string;
    last_verified_at: string | null; validation_error: string | null;
  };
};

export const UNLOCK_PHRASE = "I UNDERSTAND LIVE TRADING RISK";

export const UNLOCK_CHECKS: { key: string; label: string }[] = [
  { key: "real_money_understood", label: "I know this will use real money" },
  { key: "withdrawal_permission_disabled", label: "I have disabled withdrawal permissions on the API key" },
  { key: "ip_whitelisted", label: "I have IP-whitelisted this VM on Binance" },
  { key: "losses_possible_understood", label: "I understand the bot can lose money" },
  { key: "risk_limits_accepted", label: "I accept the max trade size, leverage, and daily loss limits" },
  { key: "tested_in_paper_mode", label: "I have tested the strategy in paper mode" },
];

export function useTradingStatus(pollMs = 10000) {
  const [status, setStatus] = useState<TradingStatus | null>(null);
  const reload = useCallback(async () => {
    const [s, credentialStatus] = await Promise.all([
      api.tradingMode().catch(() => null),
      api.binanceCredentialStatus().catch(() => null),
    ]);
    if (s) setStatus({ ...s, credential_status: credentialStatus ?? undefined });
  }, []);
  useEffect(() => {
    reload();
    const id = window.setInterval(reload, pollMs);
    return () => window.clearInterval(id);
  }, [reload, pollMs]);
  return { status, reload };
}

export function ModeBadge({ mode, killSwitch }: { mode?: string; killSwitch?: boolean }) {
  const label =
    mode === "BINANCE_LIVE" ? "BINANCE REAL — LIVE" : mode === "BINANCE_LIVE_LOCKED" ? "BINANCE REAL — LOCKED" : "PAPER";
  const cls =
    mode === "BINANCE_LIVE" ? "badge badge-red" : mode === "BINANCE_LIVE_LOCKED" ? "badge badge-orange" : "badge";
  return (
    <>
      <span className={cls}>{label}</span>
      {killSwitch && <span className="badge badge-red">KILL SWITCH</span>}
    </>
  );
}

type UnlockModalProps = {
  status: TradingStatus;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
  showToast: (message: string, tone?: "success" | "error") => void;
};

/** The blocking confirmation shown when switching bot execution to Binance
 *  Real Money. Completing the ceremony calls the backend unlock endpoint;
 *  while the server env lock is off, only a locked "view" switch is offered. */
export function LiveUnlockModal({ status, onClose, onChanged, showToast }: UnlockModalProps) {
  const [text, setText] = useState("");
  const [acks, setAcks] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  const serverLocked = !status.binance_live_enabled_by_server;
  const ready = !serverLocked && text.trim() === UNLOCK_PHRASE && UNLOCK_CHECKS.every((c) => acks[c.key]);

  const selectLockedView = async () => {
    setBusy(true);
    try {
      await api.setTradingMode("BINANCE_LIVE");
      showToast("Binance Real selected — trading stays locked", "success");
      await onChanged();
      onClose();
    } catch (e: any) {
      showToast(e?.message || "Failed to switch mode", "error");
    } finally {
      setBusy(false);
    }
  };

  const unlock = async () => {
    setBusy(true);
    try {
      await api.unlockBinanceLive(text.trim(), acks);
      showToast("User Live Confirmation completed.", "success");
      await onChanged();
      onClose();
    } catch (e: any) {
      showToast(e?.message || "Unlock refused", "error");
    } finally {
      setBusy(false);
    }
  };

  // Portaled to <body>: cards use `contain: layout paint`, which would
  // otherwise clip this fixed-position overlay to the card that opened it.
  return createPortal(
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-card risk-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Enable Binance Real Money Trading?</h3>

        {!status.binance_configured ? (
          <p className="risk-modal-error">
            <AlertTriangle size={14} /> Binance API keys are not configured on the server. Add BINANCE_API_KEY and
            BINANCE_API_SECRET to the backend .env first.
          </p>
        ) : serverLocked ? (
          <>
            <p className="risk-modal-error">
              <AlertTriangle size={14} /> Server live trading lock is engaged. Set{" "}
              <code>BINANCE_LIVE_ENABLED=true</code> in the backend .env only when ready. You can still switch to the
              Binance Real view — all trading actions stay disabled.
            </p>
            <div className="modal-actions">
              <button className="mini-btn" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button className="btn-long" onClick={selectLockedView} disabled={busy}>
                {busy ? "Switching…" : "Switch View (stay locked)"}
              </button>
            </div>
          </>
        ) : (
          <div className="live-unlock-panel">
            <p className="risk-modal-error">
              <AlertTriangle size={14} /> This enables REAL-MONEY orders on Binance Futures with the server-configured
              API key. Losses are possible on every trade.
            </p>
            {UNLOCK_CHECKS.map((c) => (
              <label key={c.key} className="live-unlock-check">
                <input
                  type="checkbox"
                  checked={!!acks[c.key]}
                  onChange={(e) => setAcks((prev) => ({ ...prev, [c.key]: e.target.checked }))}
                />
                <span>{c.label}</span>
              </label>
            ))}
            <label className="live-unlock-phrase">
              <span className="tile-label">
                Type <b>{UNLOCK_PHRASE}</b> to confirm
              </span>
              <input
                type="text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={UNLOCK_PHRASE}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <div className="modal-actions">
              <button className="mini-btn" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button className="link-btn" onClick={selectLockedView} disabled={busy} title="Show the Binance Real view without enabling trading">
                View only
              </button>
              <button className="btn-danger" onClick={unlock} disabled={!ready || busy}>
                {busy ? "Unlocking…" : "Unlock REAL Trading"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}

type ToggleProps = {
  status: TradingStatus | null;
  onChanged: () => Promise<void> | void;
  showToast: (message: string, tone?: "success" | "error") => void;
};

/** Segmented Paper / Binance Real Money execution-mode switch. */
export function ModeToggle({ status, onChanged, showToast }: ToggleProps) {
  const [busy, setBusy] = useState(false);
  const [showUnlock, setShowUnlock] = useState(false);
  const mode = status?.active_mode || "PAPER";
  const onBinance = mode === "BINANCE_LIVE" || mode === "BINANCE_LIVE_LOCKED";

  const toPaper = async () => {
    if (!onBinance) return;
    setBusy(true);
    try {
      await api.lockBinanceLive();
      showToast("Paper trading mode active — live lock re-armed", "success");
      await onChanged();
    } catch (e: any) {
      showToast(e?.message || "Failed to switch mode", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="mode-toggle" role="group" aria-label="Active trading mode">
        <button className={`mode-toggle-btn ${!onBinance ? "on paper" : ""}`} disabled={busy} onClick={toPaper}>
          Paper Trading
        </button>
        <button
          className={`mode-toggle-btn ${onBinance ? (mode === "BINANCE_LIVE" ? "on live" : "on locked") : ""}`}
          disabled={busy}
          onClick={() => !onBinance && setShowUnlock(true)}
        >
          Binance Real Money
        </button>
      </div>
      {showUnlock && status && (
        <LiveUnlockModal status={status} onClose={() => setShowUnlock(false)} onChanged={onChanged} showToast={showToast} />
      )}
    </>
  );
}
