import { useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle } from "lucide-react";

export const CLOSE_ALL_PHRASE = "CLOSE ALL POSITIONS";

/** Typed-confirmation gate for closing every open Binance position in one
 *  go, extracted from RiskPage.tsx so PositionsPage's Binance tab can offer
 *  the exact same high-risk action. Reuses the existing single-position
 *  close endpoint per position (no new backend bulk-close surface), so each
 *  close still goes through the same server-side risk-gate and audit
 *  checks as any other close. */
export default function CloseAllPositionsModal({
  positionCount,
  busy,
  onClose,
  onConfirm,
}: {
  positionCount: number;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const [text, setText] = useState("");
  const ready = text.trim() === CLOSE_ALL_PHRASE;

  return createPortal(
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-card risk-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Close ALL {positionCount} Binance position{positionCount === 1 ? "" : "s"}?</h3>
        <p className="risk-modal-error">
          <AlertTriangle size={14} /> Sends a real reduce-only MARKET order for every open Binance position, one at a
          time. This uses real funds and cannot be undone.
        </p>
        <label className="live-unlock-phrase">
          <span className="tile-label">
            Type <b>{CLOSE_ALL_PHRASE}</b> to confirm
          </span>
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={CLOSE_ALL_PHRASE}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <div className="modal-actions">
          <button className="mini-btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn-danger" onClick={onConfirm} disabled={!ready || busy}>
            {busy ? "Closing…" : "Close All Positions"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
