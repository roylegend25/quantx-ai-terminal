import { useMemo, useState } from "react";
import { fmtNum, fmtUsd } from "../../lib/format";
import type { Position } from "../../lib/portfolioStats";

export type RiskPatch = { stop_loss: number | null; take_profit: number | null; trailing_stop: number | null };

type Props = {
  position: Position;
  onSave: (id: number, patch: RiskPatch) => Promise<void>;
  onClose: () => void;
};

// Mirrors the server's minimum SL/TP distance from the mark price. The
// server remains authoritative - this only gives instant feedback.
const MIN_DISTANCE_PCT = 0.05;

function parseField(raw: string): number | null | "invalid" {
  const s = raw.trim();
  if (s === "") return null;
  const v = Number(s);
  if (!Number.isFinite(v)) return "invalid";
  return v;
}

/** Client-side mirror of app/trading/margin.py validate_risk_levels. */
function validate(side: string, mark: number, sl: number | null, tp: number | null, ts: number | null): string | null {
  const minDist = (mark * MIN_DISTANCE_PCT) / 100;
  for (const [label, v] of [["Stop loss", sl], ["Take profit", tp]] as const) {
    if (v === null) continue;
    if (v <= 0) return `${label} must be a positive price`;
    if (Math.abs(v - mark) < minDist) return `${label} is too close to the current price (min ${MIN_DISTANCE_PCT}%)`;
  }
  if (sl !== null && tp !== null && sl === tp) return "Take profit must differ from stop loss";
  if (side === "LONG") {
    if (sl !== null && sl >= mark) return "Stop loss must be below the current price for a LONG";
    if (tp !== null && tp <= mark) return "Take profit must be above the current price for a LONG";
  } else {
    if (sl !== null && sl <= mark) return "Stop loss must be above the current price for a SHORT";
    if (tp !== null && tp >= mark) return "Take profit must be below the current price for a SHORT";
  }
  if (ts !== null) {
    if (ts <= 0) return "Trailing stop must be a positive price distance";
    if (ts < minDist) return `Trailing stop distance is below the minimum (${MIN_DISTANCE_PCT}% of price)`;
    if (ts >= mark) return "Trailing stop distance must be smaller than the price";
  }
  return null;
}

export default function EditRiskModal({ position: p, onSave, onClose }: Props) {
  const [slRaw, setSlRaw] = useState(p.sl != null ? String(p.sl) : "");
  const [tpRaw, setTpRaw] = useState(p.tp != null ? String(p.tp) : "");
  const [tsRaw, setTsRaw] = useState(p.trailing_stop != null ? String(p.trailing_stop) : "");
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState("");

  const sl = parseField(slRaw);
  const tp = parseField(tpRaw);
  const ts = parseField(tsRaw);
  const parseError =
    sl === "invalid" ? "Stop loss is not a number" : tp === "invalid" ? "Take profit is not a number" : ts === "invalid" ? "Trailing stop is not a number" : null;

  const validationError = useMemo(() => {
    if (parseError) return parseError;
    return validate(p.side, p.mark, sl as number | null, tp as number | null, ts as number | null);
  }, [parseError, p.side, p.mark, sl, tp, ts]);

  const long = p.side === "LONG";
  const pnlAt = (price: number | null) =>
    price === null ? null : (long ? price - p.entry : p.entry - price) * p.qty;
  const distPct = (price: number | null) =>
    price === null || !p.mark ? null : (Math.abs(price - p.mark) / p.mark) * 100;

  const tpPnl = !parseError ? pnlAt(tp as number | null) : null;
  const slPnl = !parseError ? pnlAt(sl as number | null) : null;
  const rr =
    tpPnl !== null && slPnl !== null && slPnl !== 0 ? Math.abs(tpPnl) / Math.abs(slPnl) : null;

  const handleSave = async () => {
    if (validationError) return;
    setSaving(true);
    setServerError("");
    try {
      await onSave(p.id, {
        stop_loss: sl as number | null,
        take_profit: tp as number | null,
        trailing_stop: ts as number | null,
      });
      onClose();
    } catch (e: any) {
      setServerError(e?.message || "Failed to update risk levels");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={() => !saving && onClose()}>
      <div className="modal-card risk-modal" onClick={(e) => e.stopPropagation()}>
        <h3>
          Edit Risk — {p.symbol}{" "}
          <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span>
          {p.leverage != null && <span className="lev-badge">{p.leverage}x</span>}
        </h3>

        <div className="risk-modal-context">
          <div>
            <span className="tile-label">Entry</span>
            <b className="tile-value">{fmtNum(p.entry)}</b>
          </div>
          <div>
            <span className="tile-label">Mark</span>
            <b className="tile-value">{fmtNum(p.mark)}</b>
          </div>
          <div>
            <span className="tile-label">Size</span>
            <b className="tile-value">{p.qty}</b>
          </div>
          <div>
            <span className="tile-label">Est. Liquidation</span>
            <b className="tile-value orange">
              {p.liquidation_price != null ? fmtNum(p.liquidation_price) : p.leverage === 1 ? "None at 1x" : "—"}
            </b>
          </div>
        </div>

        <div className="risk-modal-fields">
          <label>
            <span className="tile-label green">Take Profit</span>
            <input
              type="number"
              inputMode="decimal"
              step="any"
              placeholder={long ? "above mark" : "below mark"}
              value={tpRaw}
              onChange={(e) => setTpRaw(e.target.value)}
            />
            <small>
              {tp !== null && tp !== "invalid" && tpPnl !== null
                ? `Est. PnL ${fmtUsd(tpPnl)} · ${fmtNum(distPct(tp as number), 2)}% from mark`
                : "empty = no take profit"}
            </small>
          </label>
          <label>
            <span className="tile-label red">Stop Loss</span>
            <input
              type="number"
              inputMode="decimal"
              step="any"
              placeholder={long ? "below mark" : "above mark"}
              value={slRaw}
              onChange={(e) => setSlRaw(e.target.value)}
            />
            <small>
              {sl !== null && sl !== "invalid" && slPnl !== null
                ? `Est. loss ${fmtUsd(slPnl)} · ${fmtNum(distPct(sl as number), 2)}% from mark`
                : "empty = no stop loss"}
            </small>
          </label>
          <label>
            <span className="tile-label">Trailing Stop (distance)</span>
            <input
              type="number"
              inputMode="decimal"
              step="any"
              placeholder="optional, price distance"
              value={tsRaw}
              onChange={(e) => setTsRaw(e.target.value)}
            />
            <small>ratchets the stop {long ? "up behind" : "down behind"} the price</small>
          </label>
        </div>

        <div className="risk-modal-summary">
          <span className="tile-label">Risk / Reward</span>
          <b className="tile-value">{rr !== null ? `1 : ${rr.toFixed(2)}` : "—"}</b>
        </div>

        {(validationError || serverError) && (
          <p className="risk-modal-error">{validationError || serverError}</p>
        )}

        <div className="modal-actions">
          <button
            className="link-btn"
            disabled={saving}
            onClick={() => {
              setSlRaw("");
              setTpRaw("");
              setTsRaw("");
            }}
          >
            Reset TP/SL
          </button>
          <button className="mini-btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn-long" onClick={handleSave} disabled={saving || !!validationError}>
            {saving ? "Saving…" : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
