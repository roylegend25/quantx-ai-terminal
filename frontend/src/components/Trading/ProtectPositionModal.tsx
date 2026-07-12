import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { fmtNum, fmtUsd } from "../../lib/format";

type Props = {
  position: any;
  busy: boolean;
  onClose: () => void;
  onConfirm: (takeProfit: number, stopLoss: number) => void;
};

const MIN_DISTANCE_PCT = 0.05;
const DEFAULT_OFFSET_PCT = 0.5; // tight, symmetric default bracket

function validate(side: string, reference: number, sl: number, tp: number): string | null {
  if (!(sl > 0)) return "Stop loss must be a positive price";
  if (!(tp > 0)) return "Take profit must be a positive price";
  const minDist = (reference * MIN_DISTANCE_PCT) / 100;
  if (Math.abs(sl - reference) < minDist) return `Stop loss is too close to the current price (min ${MIN_DISTANCE_PCT}%)`;
  if (Math.abs(tp - reference) < minDist) return `Take profit is too close to the current price (min ${MIN_DISTANCE_PCT}%)`;
  if (sl === tp) return "Take profit must differ from stop loss";
  if (side === "LONG") {
    if (sl >= reference) return "Stop loss must be below the current price for a LONG";
    if (tp <= reference) return "Take profit must be above the current price for a LONG";
  } else {
    if (sl <= reference) return "Stop loss must be above the current price for a SHORT";
    if (tp >= reference) return "Take profit must be below the current price for a SHORT";
  }
  return null;
}

export default function ProtectPositionModal({ position: p, busy, onClose, onConfirm }: Props) {
  const reference = p.mark_price ?? p.entry_price;
  const long = p.side === "LONG";
  const recommendedTp = long ? reference * (1 + DEFAULT_OFFSET_PCT / 100) : reference * (1 - DEFAULT_OFFSET_PCT / 100);
  const recommendedSl = long ? reference * (1 - DEFAULT_OFFSET_PCT / 100) : reference * (1 + DEFAULT_OFFSET_PCT / 100);

  const [tpRaw, setTpRaw] = useState(recommendedTp.toFixed(2));
  const [slRaw, setSlRaw] = useState(recommendedSl.toFixed(2));
  const [serverError, setServerError] = useState("");

  const tp = Number(tpRaw);
  const sl = Number(slRaw);
  const validationError = useMemo(() => {
    if (!Number.isFinite(tp) || !Number.isFinite(sl)) return "Enter valid numbers for both TP and SL";
    return validate(p.side, reference, sl, tp);
  }, [p.side, reference, sl, tp]);

  const qty = Number(p.quantity) || 0;
  const estProfit = Number.isFinite(tp) ? (long ? tp - p.entry_price : p.entry_price - tp) * qty : null;
  const estLoss = Number.isFinite(sl) ? (long ? sl - p.entry_price : p.entry_price - sl) * qty : null;

  const handleConfirm = () => {
    if (validationError) return;
    setServerError("");
    try {
      onConfirm(tp, sl);
    } catch (e: any) {
      setServerError(e?.message || "Failed to protect position");
    }
  };

  return createPortal(
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-card risk-modal" onClick={(e) => e.stopPropagation()}>
        <h3>
          Add TP/SL — {p.symbol} <span className={long ? "green" : "red"}>{p.side}</span>
        </h3>
        <p className="risk-modal-error" style={{ background: "none", color: "#aab0c8", border: "none", padding: 0 }}>
          This live position currently has no protective orders on Binance. Placing TP/SL here submits real
          reduce-only orders immediately.
        </p>

        <div className="risk-modal-context">
          <div>
            <span className="tile-label">Entry</span>
            <b className="tile-value">{fmtNum(p.entry_price)}</b>
          </div>
          <div>
            <span className="tile-label">Mark</span>
            <b className="tile-value">{fmtNum(p.mark_price)}</b>
          </div>
          <div>
            <span className="tile-label">Size</span>
            <b className="tile-value">{fmtNum(p.quantity, 6)}</b>
          </div>
          <div>
            <span className="tile-label">Liquidation</span>
            <b className="tile-value orange">{p.liquidation_price != null ? fmtNum(p.liquidation_price) : "—"}</b>
          </div>
        </div>

        <div className="risk-modal-fields">
          <label>
            <span className="tile-label green">Take Profit</span>
            <input
              type="number" inputMode="decimal" step="any"
              value={tpRaw} onChange={(e) => setTpRaw(e.target.value)} autoFocus
            />
            <small>{estProfit !== null ? `Est. profit ${fmtUsd(estProfit)}` : ""}</small>
          </label>
          <label>
            <span className="tile-label red">Stop Loss</span>
            <input
              type="number" inputMode="decimal" step="any"
              value={slRaw} onChange={(e) => setSlRaw(e.target.value)}
            />
            <small>{estLoss !== null ? `Est. loss ${fmtUsd(estLoss)}` : ""}</small>
          </label>
        </div>

        {(validationError || serverError) && <p className="risk-modal-error">{validationError || serverError}</p>}

        <div className="modal-actions">
          <button className="mini-btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn-long" onClick={handleConfirm} disabled={busy || !!validationError}>
            {busy ? "Protecting…" : "Confirm & Protect"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
