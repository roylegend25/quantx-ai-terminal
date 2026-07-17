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
/** Phase 31: a short-lived, single-use live-order authorization lease -
 *  independent of, and in addition to, binance_live_enabled_by_server and
 *  binance_live_unlocked_by_user. Null means no order can reach the real
 *  exchange right now no matter what those two other fields say. */
export type LiveLease = {
  id: number;
  user: string;
  symbol_scope: string | null;
  created_at: string | null;
  expires_at: string | null;
  seconds_remaining: number;
  actions_remaining: number;
  revoked: boolean;
  revoked_reason: string | null;
  active: boolean;
} | null;

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
  binance_live_credentials_configured: boolean;
  binance_live_unlocked_by_user: boolean;
  can_trade_binance_live: boolean;
  live_authorization_lease: LiveLease;
  final_order_routing_eligible: boolean;
  automatic_execution_mode: "PAPER" | "LIVE";
  reason: string;
  kill_switch_active: boolean;
  allowed_symbols: string[];
  max_leverage: number;
  max_notional_per_trade: number;
  max_daily_loss_usdt: number;
};

export const UNLOCK_PHRASE = "I UNDERSTAND LIVE TRADING RISK";
export const SECOND_CONFIRMATION_PHRASE = "CONFIRM LIVE EXECUTION NOW";

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
    const s = await api.tradingMode().catch(() => null);
    if (s) setStatus(s);
  }, []);
  useEffect(() => {
    reload();
    const id = window.setInterval(reload, pollMs);
    return () => window.clearInterval(id);
  }, [reload, pollMs]);
  return { status, reload };
}

/** Persistent, always-visible banner distinguishing "automatic strategy
 *  execution" (what the scheduler/bot does unattended every cycle) from the
 *  server/UI capability flags nearby, which must never be read as
 *  equivalent to it. Phase 31: root-caused by a real incident where
 *  "Configured"/"Enabled"/"Unlocked" badges were mistaken for "an order can
 *  actually be placed right now" - this banner states the one fact that
 *  actually matters, plainly, every time. */
export function AutomaticExecutionBanner({ status }: { status: TradingStatus | null }) {
  const mode = status?.automatic_execution_mode ?? "PAPER";
  const isLive = mode === "LIVE";
  return (
    <div className={`auto-execution-banner ${isLive ? "live" : "paper"}`} role="status">
      <AlertTriangle size={14} />
      <span>AUTOMATIC STRATEGY EXECUTION: {mode} MODE</span>
      {isLive && status?.live_authorization_lease && (
        <LiveLeaseCountdown lease={status.live_authorization_lease} compact />
      )}
    </div>
  );
}

/** Live-only countdown for the currently active authorization lease -
 *  renders nothing when there is no active lease (which in PAPER mode, or
 *  between orders in LIVE mode, is the normal state). */
export function LiveLeaseCountdown({ lease, compact }: { lease: LiveLease; compact?: boolean }) {
  if (!lease || !lease.active) return null;
  const mm = String(Math.floor(lease.seconds_remaining / 60)).padStart(2, "0");
  const ss = String(lease.seconds_remaining % 60).padStart(2, "0");
  return (
    <span className={`live-lease-countdown ${compact ? "compact" : ""}`}>
      Lease expires in {mm}:{ss} · {lease.actions_remaining} action{lease.actions_remaining === 1 ? "" : "s"} left
      {lease.symbol_scope && lease.symbol_scope !== "ALL" ? ` · scoped to ${lease.symbol_scope}` : ""}
    </span>
  );
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
  const [password, setPassword] = useState("");
  const [text, setText] = useState("");
  const [secondText, setSecondText] = useState("");
  const [accountText, setAccountText] = useState("");
  const [symbolChoice, setSymbolChoice] = useState("");
  const [acks, setAcks] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  const serverLocked = !status.binance_live_enabled_by_server;
  const credentialsMissing = !status.binance_live_credentials_configured;
  const ready =
    !serverLocked &&
    !credentialsMissing &&
    !!password &&
    text.trim() === UNLOCK_PHRASE &&
    secondText.trim() === SECOND_CONFIRMATION_PHRASE &&
    !!accountText.trim() &&
    !!symbolChoice &&
    UNLOCK_CHECKS.every((c) => acks[c.key]);

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
      const result = await api.unlockBinanceLive({
        password,
        confirmation: text.trim(),
        second_confirmation: secondText.trim(),
        account_confirmation: accountText.trim(),
        symbol_confirmation: symbolChoice,
        acknowledgements: acks,
      });
      const seconds = result?.lease?.seconds_remaining ?? 0;
      showToast(
        `Live trading authorized for ${seconds}s or one order, whichever comes first.`,
        "success"
      );
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
        ) : credentialsMissing ? (
          <>
            <p className="risk-modal-error">
              <AlertTriangle size={14} /> Live-write credentials are not configured on the server. Set{" "}
              <code>BINANCE_LIVE_API_KEY</code> and <code>BINANCE_LIVE_API_SECRET</code> in the backend .env - these
              are deliberately separate from the read-only/testnet key and are never loaded during paper operation.
            </p>
            <div className="modal-actions">
              <button className="mini-btn" onClick={onClose} disabled={busy}>
                Close
              </button>
            </div>
          </>
        ) : (
          <div className="live-unlock-panel">
            <p className="risk-modal-error">
              <AlertTriangle size={14} /> This grants a one-time, short-lived authorization for REAL-MONEY orders on
              Binance Futures. It expires automatically and is consumed after one order - it does not survive a
              browser refresh, logout, or backend restart, and re-confirming is required every time.
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
              <span className="tile-label">Re-enter your password to confirm it's really you, right now</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
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
            <label className="live-unlock-phrase">
              <span className="tile-label">
                Type <b>{SECOND_CONFIRMATION_PHRASE}</b> as a second, separate confirmation
              </span>
              <input
                type="text"
                value={secondText}
                onChange={(e) => setSecondText(e.target.value)}
                placeholder={SECOND_CONFIRMATION_PHRASE}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <label className="live-unlock-phrase">
              <span className="tile-label">Type your account username to confirm which account this is for</span>
              <input
                type="text"
                value={accountText}
                onChange={(e) => setAccountText(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <label className="live-unlock-phrase">
              <span className="tile-label">Confirm exactly which symbol this authorization is for</span>
              <select value={symbolChoice} onChange={(e) => setSymbolChoice(e.target.value)}>
                <option value="">Select a symbol…</option>
                {status.allowed_symbols.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
                <option value="ALL">ALL (unrestricted)</option>
              </select>
            </label>
            <div className="modal-actions">
              <button className="mini-btn" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button className="link-btn" onClick={selectLockedView} disabled={busy} title="Show the Binance Real view without enabling trading">
                View only
              </button>
              <button className="btn-danger" onClick={unlock} disabled={!ready || busy}>
                {busy ? "Authorizing…" : "Authorize One Live Order"}
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
