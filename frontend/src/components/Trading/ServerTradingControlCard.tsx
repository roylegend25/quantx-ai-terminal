import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, OctagonX, RefreshCw, ShieldCheck, SlidersHorizontal } from "lucide-react";
import Card from "../Layout/Card";
import { api } from "../../services/api";
import { fmtUsd } from "../../lib/format";
import { UNLOCK_PHRASE } from "./TradingShared";

const POLL_MS = 15000;

// The SERVER-lock ceremony (distinct from the user live-unlock ceremony -
// both must be completed before a real order is possible).
const SERVER_LOCK_CHECKS: { key: string; label: string }[] = [
  { key: "real_money", label: "I know this can place real Binance Futures orders" },
  { key: "withdrawals_disabled", label: "I have disabled withdrawals on the API key" },
  { key: "ip_whitelisted", label: "I have IP-whitelisted this VM on Binance" },
  { key: "loss_possible", label: "I understand the bot can lose money" },
  { key: "risk_limits_checked", label: "I checked max leverage, notional and daily loss limits" },
];

type Props = {
  showToast: (message: string, tone?: "success" | "error") => void;
};

/** Server Trading Control (Phase 24): admin-only panel over the backend's
 *  allowlisted .env trading settings. Renders nothing for non-admin tokens
 *  (the config endpoint answers 403 and we stay hidden). Secrets are never
 *  fetched, held, or displayed - only configured yes/no flags. */
export default function ServerTradingControlCard({ showToast }: Props) {
  const [config, setConfig] = useState<any>(null);
  const [forbidden, setForbidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState<null | "enable" | "disable" | "limits">(null);

  const load = useCallback(async () => {
    try {
      setConfig(await api.adminServerConfig());
      setForbidden(false);
    } catch (e: any) {
      if (String(e?.message || "").includes("Admin")) setForbidden(true);
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  if (forbidden || !config) return null;

  const liveEnabled = !!config.binance_live_enabled;
  const mode = config.active_mode || "PAPER";
  const killSwitchOn = !!config.kill_switch_active;
  const realTradingPossible = mode === "BINANCE_LIVE" && liveEnabled && !killSwitchOn;

  const run = async (fn: () => Promise<any>, okMessage: string) => {
    setBusy(true);
    try {
      const res = await fn();
      showToast(res?.message || okMessage, "success");
      setModal(null);
    } catch (e: any) {
      showToast(e?.message || "Action failed", "error");
    } finally {
      setBusy(false);
      await load();
    }
  };

  return (
    <Card
      title="Server Trading Control"
      full
      className={realTradingPossible ? "live-danger-card" : ""}
      right={
        <div className="controls">
          <span className={`badge ${liveEnabled ? "badge-red" : ""}`}>
            Server Live Lock: {liveEnabled ? "ON" : "OFF"}
          </span>
          {realTradingPossible && <span className="badge badge-red">Real Money Trading Possible</span>}
          {killSwitchOn && <span className="badge badge-red">Emergency Stop Active</span>}
        </div>
      }
    >
      <div className="server-control-body">
        {killSwitchOn && (
          <p className="risk-modal-error">
            <OctagonX size={14} /> Emergency Stop Active — all new trades blocked.
          </p>
        )}

        <div className="server-control-grid">
          <div>
            <span className="tile-label">Active Mode</span>
            <b className={`tile-value ${mode === "BINANCE_LIVE" ? "red" : mode === "BINANCE_LIVE_LOCKED" ? "orange" : ""}`}>
              {mode === "BINANCE_LIVE" ? "BINANCE REAL ACTIVE" : mode === "BINANCE_LIVE_LOCKED" ? "BINANCE REAL LOCKED" : "PAPER"}
            </b>
          </div>
          <div>
            <span className="tile-label">Binance API</span>
            <b className="tile-value">
              {config.binance_api_key_configured && config.binance_api_secret_configured
                ? "Configured"
                : "Not Configured"}
            </b>
          </div>
          <div>
            <span className="tile-label">Server Live Lock</span>
            <b className={`tile-value ${liveEnabled ? "red" : "green"}`}>{liveEnabled ? "Enabled" : "Disabled"}</b>
          </div>
          <div>
            <span className="tile-label">User Live Unlock</span>
            <b className={`tile-value ${config.binance_live_unlocked_by_user ? "red" : "green"}`}>
              {config.binance_live_unlocked_by_user ? "Unlocked" : "Locked"}
            </b>
          </div>
          <div>
            <span className="tile-label">Kill Switch</span>
            <b className={`tile-value ${killSwitchOn ? "red" : "green"}`}>{killSwitchOn ? "Active" : "Inactive"}</b>
          </div>
          <div>
            <span className="tile-label">Max Notional</span>
            <b className="tile-value">{fmtUsd(config.max_notional_per_trade)}</b>
          </div>
          <div>
            <span className="tile-label">Max Daily Loss</span>
            <b className="tile-value">{fmtUsd(config.max_daily_loss_usdt)}</b>
          </div>
          <div>
            <span className="tile-label">Max Leverage</span>
            <b className="tile-value">{config.max_leverage != null ? `${config.max_leverage}x` : "—"}</b>
          </div>
        </div>

        <p className="regime-desc">
          {mode === "PAPER" && "Paper mode active — no real Binance orders. "}
          This only changes the server permission. The user live-risk confirmation is still required before real
          orders can be placed.
        </p>

        <div className="controls">
          {liveEnabled ? (
            <button className="btn-danger" disabled={busy} onClick={() => setModal("disable")}>
              Disable Server Live Trading Lock
            </button>
          ) : (
            <button className="btn-danger" disabled={busy} onClick={() => setModal("enable")}>
              Enable Server Live Trading Lock
            </button>
          )}
          <button className="mini-btn" disabled={busy} onClick={() => setModal("limits")}>
            <SlidersHorizontal size={13} /> Edit Risk Limits
          </button>
          <button
            className="mini-btn"
            disabled={busy}
            onClick={() => run(() => api.adminReloadConfig(), "Config reloaded")}
          >
            <RefreshCw size={13} /> Reload Backend Config
          </button>
          {killSwitchOn ? (
            <button className="btn-long" disabled={busy}
                    onClick={() => run(() => api.killSwitch(false), "Kill switch deactivated")}>
              <ShieldCheck size={14} /> Re-enable Trading
            </button>
          ) : (
            <button
              className="btn-danger"
              disabled={busy}
              onClick={() => {
                if (window.confirm("EMERGENCY STOP?\n\nStops the bot and blocks ALL new paper and real trades. Open positions are NOT auto-closed.")) {
                  run(() => api.killSwitch(true, "server control emergency stop"), "Kill switch ACTIVATED");
                }
              }}
            >
              <OctagonX size={14} /> Emergency Stop
            </button>
          )}
        </div>
      </div>

      {modal === "enable" && (
        <EnableServerLockModal busy={busy} onClose={() => setModal(null)} onConfirm={(phrase, acks) =>
          run(() => api.adminSetBinanceLive(true, phrase, acks), "Server live permission enabled")
        } />
      )}
      {modal === "disable" && (
        <ConfirmModal
          title="Disable Binance real trading server permission?"
          body="BINANCE_LIVE_ENABLED will be set to false. Binance real trading locks immediately and no new real orders can be placed."
          busy={busy}
          onClose={() => setModal(null)}
          onConfirm={() => run(() => api.adminSetBinanceLive(false), "Server live permission disabled")}
        />
      )}
      {modal === "limits" && (
        <RiskLimitsModal
          config={config}
          busy={busy}
          onClose={() => setModal(null)}
          onSave={(limits) => run(() => api.adminUpdateRiskLimits(limits), "Risk limits updated")}
        />
      )}
    </Card>
  );
}

function ConfirmModal({ title, body, busy, onClose, onConfirm }: {
  title: string; body: string; busy: boolean; onClose: () => void; onConfirm: () => void;
}) {
  return createPortal(
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <p className="regime-desc">{body}</p>
        <div className="modal-actions">
          <button className="mini-btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button className="btn-danger" disabled={busy} onClick={onConfirm}>
            {busy ? "Working…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

function EnableServerLockModal({ busy, onClose, onConfirm }: {
  busy: boolean; onClose: () => void;
  onConfirm: (phrase: string, acks: Record<string, boolean>) => void;
}) {
  const [text, setText] = useState("");
  const [acks, setAcks] = useState<Record<string, boolean>>({});
  const ready = text.trim() === UNLOCK_PHRASE && SERVER_LOCK_CHECKS.every((c) => acks[c.key]);

  return createPortal(
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-card risk-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Enable Binance Real Money Server Lock?</h3>
        <div className="live-unlock-panel">
          <p className="risk-modal-error">
            <AlertTriangle size={14} /> This allows the backend to permit real Binance orders after the separate
            live unlock is completed. This can result in real financial loss.
          </p>
          {SERVER_LOCK_CHECKS.map((c) => (
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
            <span className="tile-label">Type <b>{UNLOCK_PHRASE}</b> to confirm</span>
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
            <button className="mini-btn" disabled={busy} onClick={onClose}>Cancel</button>
            <button className="btn-danger" disabled={!ready || busy} onClick={() => onConfirm(text.trim(), acks)}>
              {busy ? "Enabling…" : "Enable Server Live Permission"}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}

function RiskLimitsModal({ config, busy, onClose, onSave }: {
  config: any; busy: boolean; onClose: () => void;
  onSave: (limits: Record<string, unknown>) => void;
}) {
  const [defaultLev, setDefaultLev] = useState(String(config.default_leverage ?? 1));
  const [maxLev, setMaxLev] = useState(String(config.max_leverage ?? 1));
  const [notional, setNotional] = useState(String(config.max_notional_per_trade ?? 10));
  const [dailyLoss, setDailyLoss] = useState(String(config.max_daily_loss_usdt ?? 5));
  const [symbols, setSymbols] = useState((config.allowed_symbols || []).join(","));
  const [allowHighLev, setAllowHighLev] = useState(false);

  const validate = (): string | null => {
    const dl = Number(defaultLev), ml = Number(maxLev), n = Number(notional), loss = Number(dailyLoss);
    if (!Number.isFinite(dl) || dl < 1) return "Default leverage must be at least 1";
    if (!Number.isFinite(ml) || ml < 1) return "Max leverage must be at least 1";
    if (ml > 3 && !allowHighLev) return "Max leverage above 3x requires the explicit high-leverage checkbox";
    if (dl > ml) return "Default leverage cannot exceed max leverage";
    if (!Number.isFinite(n) || n <= 0) return "Max notional must be positive";
    if (!Number.isFinite(loss) || loss <= 0) return "Max daily loss must be positive";
    const list = symbols.split(",").map((s: string) => s.trim().toUpperCase()).filter(Boolean);
    if (list.length === 0) return "Allowed symbols cannot be empty";
    if (list.some((s: string) => !/^[A-Z0-9]{2,20}USDT$/.test(s))) return "Symbols must be uppercase USDT futures pairs";
    return null;
  };
  const error = validate();

  return createPortal(
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-card risk-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Edit Real Trading Risk Limits</h3>
        <div className="risk-modal-fields">
          <label>
            <span className="tile-label">Default Leverage</span>
            <input type="number" min={1} step="1" value={defaultLev} onChange={(e) => setDefaultLev(e.target.value)} />
            <small>applied to bot orders (recommended: 1)</small>
          </label>
          <label>
            <span className="tile-label">Max Leverage</span>
            <input type="number" min={1} step="1" value={maxLev} onChange={(e) => setMaxLev(e.target.value)} />
            <small>hard cap for any real order (recommended: 1)</small>
          </label>
          <label>
            <span className="tile-label">Max Notional / Trade (USDT)</span>
            <input type="number" min={1} step="any" value={notional} onChange={(e) => setNotional(e.target.value)} />
            <small>recommended: 10</small>
          </label>
          <label>
            <span className="tile-label">Max Daily Loss (USDT)</span>
            <input type="number" min={1} step="any" value={dailyLoss} onChange={(e) => setDailyLoss(e.target.value)} />
            <small>real orders stop after this loss (recommended: 5)</small>
          </label>
          <label>
            <span className="tile-label">Allowed Symbols</span>
            <input type="text" value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="BTCUSDT,ETHUSDT" />
            <small>comma-separated USDT futures pairs</small>
          </label>
          <label className="live-unlock-check">
            <input type="checkbox" checked={allowHighLev} onChange={(e) => setAllowHighLev(e.target.checked)} />
            <span>Explicitly allow leverage above 3x (not recommended)</span>
          </label>
        </div>
        {error && <p className="risk-modal-error">{error}</p>}
        <div className="modal-actions">
          <button className="mini-btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button
            className="btn-long"
            disabled={busy || !!error}
            onClick={() =>
              onSave({
                BINANCE_DEFAULT_LEVERAGE: Number(defaultLev),
                BINANCE_MAX_LEVERAGE: Number(maxLev),
                BINANCE_MAX_NOTIONAL_PER_TRADE: Number(notional),
                BINANCE_MAX_DAILY_LOSS_USDT: Number(dailyLoss),
                BINANCE_ALLOWED_SYMBOLS: symbols.split(",").map((s: string) => s.trim().toUpperCase()).filter(Boolean).join(","),
                allow_high_leverage: allowHighLev,
              })
            }
          >
            {busy ? "Saving…" : "Save Limits"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
