import { useState } from "react";
import { createPortal } from "react-dom";

/** Admin-only real-trading risk-limits editor, extracted from
 *  ServerTradingControlCard.tsx so both it and RiskPage's Binance tab can
 *  open the exact same form against PATCH /api/admin/server-config/risk-limits
 *  instead of duplicating the fields/validation. `config` is the shape
 *  returned by GET /api/admin/server-config (useAdminServerConfig). */
export default function RiskLimitsModal({ config, busy, onClose, onSave }: {
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
