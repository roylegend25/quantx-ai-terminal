import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../../services/api";

const RISK_LABEL: Record<string, string> = {
  BELOW_FLOOR: "Below Floor",
  AGGRESSIVE: "Aggressive",
  MODERATE: "Moderate",
  CONSERVATIVE: "Conservative",
  VERY_CONSERVATIVE: "Very Conservative",
};

const RISK_TONE: Record<string, string> = {
  BELOW_FLOOR: "red",
  AGGRESSIVE: "yellow",
  MODERATE: "",
  CONSERVATIVE: "green",
  VERY_CONSERVATIVE: "green",
};

/** Admin-only editor for the calibrated directional confidence GATE
 *  threshold (the minimum a decision's own computed confidence must clear
 *  to be actionable) - never the confidence value itself, which the
 *  decision engine always computes from real evidence. A hard institutional
 *  floor is enforced server-side (env_manager.ACTIVE_DRIVE_MIN_CONFIDENCE_FLOOR);
 *  this form previews the real historical impact before saving. */
export default function ConfidenceThresholdModal({ config, busy, onClose, onSave }: {
  config: any; busy: boolean; onClose: () => void;
  onSave: (minConfidence: number) => void;
}) {
  const floor = config.active_drive_min_confidence_floor ?? 0.55;
  const [value, setValue] = useState(String(config.active_drive_min_confidence ?? 0.6));
  const [preview, setPreview] = useState<any>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const numeric = Number(value);
  const clientError = !Number.isFinite(numeric)
    ? "Must be a number"
    : numeric < floor
    ? `Cannot be set below the institutional safety floor of ${floor}`
    : numeric > 1
    ? "Cannot exceed 1.0"
    : null;

  useEffect(() => {
    if (clientError) {
      setPreview(null);
      setPreviewError(null);
      return;
    }
    let cancelled = false;
    setPreviewLoading(true);
    const id = window.setTimeout(async () => {
      try {
        const r = await api.adminPreviewConfidenceThreshold(numeric);
        if (!cancelled) {
          setPreview(r);
          setPreviewError(null);
        }
      } catch (e: any) {
        if (!cancelled) setPreviewError(e?.message || "Preview failed");
      } finally {
        if (!cancelled) setPreviewLoading(false);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(id);
    };
  }, [numeric, clientError]);

  return createPortal(
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-card risk-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Edit Calibrated Directional Confidence Gate</h3>
        <p className="regime-desc">
          Sets only the minimum threshold a decision's own computed confidence must clear to become actionable -
          it never edits the confidence value itself. Institutional safety floor: <b>{floor}</b>.
        </p>
        <div className="risk-modal-fields">
          <label>
            <span className="tile-label">Minimum Calibrated Directional Confidence</span>
            <input
              type="number"
              min={floor}
              max={1}
              step="0.01"
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
            <small>current: {config.active_drive_min_confidence ?? "—"} · floor: {floor}</small>
          </label>
        </div>

        {clientError && <p className="risk-modal-error">{clientError}</p>}

        {!clientError && (
          <div className="regime-focus" style={{ marginTop: 4 }}>
            {previewLoading && <p className="regime-desc">Loading preview…</p>}
            {previewError && <p className="risk-modal-error">{previewError}</p>}
            {preview && !previewLoading && (
              <>
                <span className="tile-label">Risk Classification</span>
                <b className={`tile-value ${RISK_TONE[preview.risk_classification] || ""}`}>
                  {RISK_LABEL[preview.risk_classification] || preview.risk_classification}
                </b>
                {preview.history.total_decisions === 0 ? (
                  <p className="regime-desc">{preview.history.note}</p>
                ) : (
                  <p className="regime-desc">
                    Last {preview.history.lookback_days} days · {preview.history.total_decisions} decisions ·{" "}
                    {preview.history.would_pass_at_current_threshold_pct}% cleared the current threshold vs{" "}
                    {preview.history.would_pass_at_proposed_threshold_pct}% would clear this one.
                  </p>
                )}
              </>
            )}
          </div>
        )}

        <div className="modal-actions">
          <button className="mini-btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button
            className="btn-long"
            disabled={busy || !!clientError}
            onClick={() => onSave(numeric)}
          >
            {busy ? "Saving…" : "Save Confidence Gate"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
