import { useEffect, useState } from "react";
import Card from "../Layout/Card";
import { fmtLocalDateTime } from "../../lib/format";
import { api } from "../../services/api";

/** Editable risk-limits form (confidence, point margin, total evidence,
 *  risk/trade, loss limits, open positions, cooldown, long/short toggles,
 *  ...), extracted from RiskPage.tsx so both the Paper and Binance Real
 *  tabs of Bot Settings can render this same control set against their own
 *  independent scope (GET/PUT/POST /api/risk/settings?scope=paper|
 *  binance_real). Paper and Binance Real are separate rows with a full
 *  audit trail - this is deliberately no longer a single shared surface;
 *  the real risk gate (app/trading/real_risk_gate.py) reads the
 *  binance_real scope specifically. Lowering confidence/point-margin/
 *  evidence on Binance Real requires an explicit risk-acknowledgement
 *  confirmation before it saves - it never enables live execution by
 *  itself. */

export type RiskScope = "paper" | "binance_real";

type RiskSettingsData = {
  min_confidence_to_trade: number;
  min_point_margin: number;
  min_total_evidence: number;
  max_risk_per_trade_pct: number;
  max_daily_loss_pct: number;
  max_weekly_loss_pct: number;
  max_drawdown_pct: number;
  max_consecutive_losses: number;
  max_open_positions: number;
  max_position_size_usd: number;
  allow_long: boolean;
  allow_short: boolean;
  cooldown_minutes: number;
  paper_trading_enabled: boolean;
  scope?: string;
  version?: number;
  updated_at: string | null;
};

const LOWER_IS_RISKIER: (keyof RiskSettingsData)[] = ["min_confidence_to_trade", "min_point_margin", "min_total_evidence"];

const OPEN_POSITIONS_OPTIONS = [1, 2, 3, 4, 5];

const PRESETS: { key: string; label: string; badge?: string; confidence: number; risk: number }[] = [
  { key: "conservative", label: "Conservative", confidence: 0.7, risk: 0.5 },
  { key: "balanced", label: "Balanced", confidence: 0.6, risk: 1.0 },
  { key: "aggressive", label: "Aggressive", badge: "PAPER ONLY", confidence: 0.45, risk: 1.5 },
];

function RiskSlider({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
  tooltip,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
  tooltip?: string;
}) {
  return (
    <div className="risk-field">
      <div className="risk-field-head">
        <span className="tile-label" title={tooltip}>
          {label}
          {tooltip && <span className="risk-field-info"> ⓘ</span>}
        </span>
        <b className="risk-field-value">{format(value)}</b>
      </div>
      <input
        type="range"
        className="risk-slider"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <input
        type="number"
        className="risk-number-input"
        style={{ marginTop: 6, width: "100%" }}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

function RiskToggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="risk-toggle-row">
      <span className="tile-label" style={{ marginBottom: 0 }}>
        {label}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        className={`toggle-switch ${checked ? "on" : ""}`}
        onClick={() => onChange(!checked)}
      >
        <span className="toggle-knob" />
      </button>
    </div>
  );
}

type PreviewImpact = {
  sample_size: number;
  sample_too_small: boolean;
  decisions_qualifying_now: number;
  decisions_qualifying_under_proposed: number;
  signal_frequency_change: number;
};

type Props = {
  scope: RiskScope;
  showToast: (message: string, tone?: "success" | "error") => void;
  /** Override the default explanatory paragraph. */
  note?: string;
  /** Card title - defaults to "Risk Limits". */
  title?: string;
};

export default function RiskSettingsForm({ scope, showToast, note, title = "Risk Limits" }: Props) {
  const [settings, setSettings] = useState<RiskSettingsData | null>(null);
  const [draft, setDraft] = useState<RiskSettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmChecked, setConfirmChecked] = useState(false);
  const [preview, setPreview] = useState<PreviewImpact | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .riskSettingsGet(scope)
      .then((data: RiskSettingsData) => {
        if (cancelled) return;
        setSettings(data);
        setDraft(data);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scope]);

  function patchDraft(patch: Partial<RiskSettingsData>) {
    setDraft((d) => (d ? { ...d, ...patch } : d));
  }

  function applyPreset(preset: (typeof PRESETS)[number]) {
    patchDraft({ min_confidence_to_trade: preset.confidence, max_risk_per_trade_pct: preset.risk });
  }

  function loweredFields(): (keyof RiskSettingsData)[] {
    if (!draft || !settings) return [];
    return LOWER_IS_RISKIER.filter((f) => (draft[f] as number) < (settings[f] as number));
  }

  async function doSave(confirmLowering: boolean) {
    if (!draft) return;
    setSaving(true);
    try {
      const { updated_at, scope: _s, version: _v, ...patch } = draft;
      const res = await api.riskSettingsUpdate({ ...patch, confirm_risk_lowering: confirmLowering }, scope);
      setSettings(res);
      setDraft(res);
      setConfirmOpen(false);
      setConfirmChecked(false);
      setPreview(null);
      showToast("Risk settings saved");
    } catch (e: any) {
      if (e?.status === 409 && e?.detail?.code === "CONFIRMATION_REQUIRED") {
        await openConfirmModal();
      } else {
        showToast(e?.message || "Failed to save risk settings", "error");
      }
    } finally {
      setSaving(false);
    }
  }

  async function openConfirmModal() {
    setConfirmOpen(true);
    setPreviewLoading(true);
    try {
      if (draft) {
        const { updated_at, scope: _s, version: _v, ...patch } = draft;
        const result = await api.riskSettingsPreviewImpact(scope, patch);
        setPreview(result);
      }
    } catch {
      setPreview(null);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handleSave() {
    if (scope === "binance_real" && loweredFields().length > 0) {
      await openConfirmModal();
      return;
    }
    await doSave(false);
  }

  async function handleReset() {
    setSaving(true);
    try {
      const res = await api.riskSettingsReset(scope);
      setSettings(res);
      setDraft(res);
      showToast("Risk settings reset to defaults");
    } catch (e: any) {
      showToast(e?.message || "Failed to reset risk settings", "error");
    } finally {
      setSaving(false);
    }
  }

  const confidenceLowered = !!(draft && settings && draft.min_confidence_to_trade < settings.min_confidence_to_trade);
  const riskIncreased = !!(draft && settings && draft.max_risk_per_trade_pct > settings.max_risk_per_trade_pct);
  const dirty = !!(draft && settings && JSON.stringify(draft) !== JSON.stringify(settings));
  const isBinanceReal = scope === "binance_real";

  return (
    <Card title={title} full>
      {loading || !draft ? (
        <p className="regime-desc">Loading risk settings…</p>
      ) : (
        <>
          <p className="regime-desc">
            {note ??
              (isBinanceReal
                ? "These are Binance Real's own settings, stored separately from Paper. Changing them never enables live " +
                  "execution by itself - that remains gated by the server live lock, live unlock ceremony and kill switch."
                : "Changes take effect on the next prediction/scheduler cycle - no restart needed. These are Paper's own " +
                  "settings, stored separately from Binance Real.")}
          </p>

          <div className="risk-presets">
            {PRESETS.map((preset) => (
              <button key={preset.key} type="button" className="preset-btn" onClick={() => applyPreset(preset)}>
                {preset.label}
                {preset.badge && <span className="badge badge-red risk-preset-badge">{preset.badge}</span>}
                <span className="preset-btn-sub">
                  {Math.round(preset.confidence * 100)}% conf · {preset.risk}% risk
                </span>
              </button>
            ))}
          </div>

          <div className="risk-settings-grid">
            <RiskSlider
              label="Confidence Threshold"
              value={draft.min_confidence_to_trade}
              min={0.05}
              max={0.95}
              step={0.01}
              format={(v) => `${Math.round(v * 100)}%`}
              onChange={(v) => patchDraft({ min_confidence_to_trade: v })}
              tooltip="Minimum calibrated directional confidence required before a signal is actionable."
            />
            <RiskSlider
              label="Minimum Point Margin"
              value={draft.min_point_margin}
              min={0}
              max={50}
              step={0.1}
              format={(v) => v.toFixed(1)}
              onChange={(v) => patchDraft({ min_point_margin: v })}
              tooltip="abs(long_score - short_score): the minimum absolute difference between the final LONG and SHORT decision scores required before a directional prediction may become actionable. Separate from Confidence - point margin measures directional separation, confidence measures certainty."
            />
            <RiskSlider
              label="Minimum Total Evidence"
              value={draft.min_total_evidence}
              min={0}
              max={100}
              step={0.5}
              format={(v) => v.toFixed(1)}
              onChange={(v) => patchDraft({ min_total_evidence: v })}
              tooltip="Total usable signal strength (sum of absolute family points after caps) required before a decision is evaluated at all."
            />
            <RiskSlider
              label="Risk Per Trade"
              value={draft.max_risk_per_trade_pct}
              min={0.1}
              max={5}
              step={0.1}
              format={(v) => `${v.toFixed(1)}%`}
              onChange={(v) => patchDraft({ max_risk_per_trade_pct: v })}
            />
            <RiskSlider
              label="Daily Loss Limit"
              value={draft.max_daily_loss_pct}
              min={0.5}
              max={10}
              step={0.5}
              format={(v) => `${v.toFixed(1)}%`}
              onChange={(v) => patchDraft({ max_daily_loss_pct: v })}
            />
            <RiskSlider
              label="Weekly Loss Limit"
              value={draft.max_weekly_loss_pct}
              min={1}
              max={30}
              step={0.5}
              format={(v) => `${v.toFixed(1)}%`}
              onChange={(v) => patchDraft({ max_weekly_loss_pct: v })}
            />
            <RiskSlider
              label="Max Drawdown"
              value={draft.max_drawdown_pct}
              min={1}
              max={50}
              step={1}
              format={(v) => `${v.toFixed(0)}%`}
              onChange={(v) => patchDraft({ max_drawdown_pct: v })}
            />
          </div>

          {confidenceLowered && (
            <div className="risk-warning">
              Lower confidence threshold will increase trade frequency but may reduce accuracy.
            </div>
          )}
          {riskIncreased && <div className="risk-warning">Higher risk per trade can increase drawdown.</div>}
          {isBinanceReal && loweredFields().length > 0 && (
            <div className="risk-warning risk-warning-severe">
              ⚠ You are lowering {loweredFields().join(", ")} for Binance Real. This makes more signals pass the
              gate. You will be asked to confirm before this saves.
            </div>
          )}

          <div className="risk-settings-grid" style={{ marginTop: 18 }}>
            <div className="risk-field">
              <span className="tile-label">Max Open Positions</span>
              <div className="tf-group" style={{ marginTop: 6 }}>
                {OPEN_POSITIONS_OPTIONS.map((n) => (
                  <button
                    key={n}
                    className={draft.max_open_positions === n ? "tf-btn active" : "tf-btn"}
                    onClick={() => patchDraft({ max_open_positions: n })}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>

            <div className="risk-field">
              <span className="tile-label">Cooldown Minutes</span>
              <input
                type="number"
                className="risk-number-input"
                min={0}
                max={1440}
                step={5}
                value={draft.cooldown_minutes}
                onChange={(e) => patchDraft({ cooldown_minutes: Number(e.target.value) })}
              />
            </div>

            <div className="risk-field">
              <span className="tile-label">Max Consecutive Losses</span>
              <input
                type="number"
                className="risk-number-input"
                min={1}
                max={20}
                step={1}
                value={draft.max_consecutive_losses}
                onChange={(e) => patchDraft({ max_consecutive_losses: Number(e.target.value) })}
              />
            </div>

            <div className="risk-field">
              <span className="tile-label">Max Position Size (USD)</span>
              <input
                type="number"
                className="risk-number-input"
                min={10}
                max={100000}
                step={10}
                value={draft.max_position_size_usd}
                onChange={(e) => patchDraft({ max_position_size_usd: Number(e.target.value) })}
              />
            </div>

            <RiskToggle
              label="Long Enabled"
              checked={draft.allow_long}
              onChange={(v) => patchDraft({ allow_long: v })}
            />
            <RiskToggle
              label="Short Enabled"
              checked={draft.allow_short}
              onChange={(v) => patchDraft({ allow_short: v })}
            />
            {!isBinanceReal && (
              <RiskToggle
                label="Paper Trading Enabled"
                checked={draft.paper_trading_enabled}
                onChange={(v) => patchDraft({ paper_trading_enabled: v })}
              />
            )}
          </div>

          <div className="controls" style={{ marginTop: 20 }}>
            <button onClick={handleSave} disabled={saving || !dirty}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button onClick={handleReset} disabled={saving}>
              Reset Defaults
            </button>
          </div>

          <p className="regime-desc" style={{ marginTop: 10 }}>
            Scope: {scope === "binance_real" ? "Binance Real" : "Paper"} · Version: {settings?.version ?? "—"} ·
            Last updated: {settings?.updated_at ? fmtLocalDateTime(settings.updated_at) : "—"}
          </p>

          {confirmOpen && (
            <div className="modal-overlay">
              <div className="modal-content">
                <h3>Confirm risk-lowering change to Binance Real</h3>
                <p className="regime-desc">
                  You are lowering {loweredFields().join(", ")}. This increases how often signals will qualify for
                  real execution once trading is unlocked. Review the impact before saving.
                </p>
                <table className="simple-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Old value</th>
                      <th>New value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {loweredFields().map((f) => (
                      <tr key={f}>
                        <td>{f}</td>
                        <td>{String(settings?.[f])}</td>
                        <td>{String(draft?.[f])}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {previewLoading ? (
                  <p className="regime-desc">Loading preview impact…</p>
                ) : preview ? (
                  <div className="risk-preview-impact">
                    <p>
                      Sample size (last 14 days): <b>{preview.sample_size}</b>
                    </p>
                    {preview.sample_too_small && (
                      <div className="risk-warning">
                        Sample too small to estimate impact reliably ({preview.sample_size} decisions).
                      </div>
                    )}
                    <p>
                      Decisions that would have qualified now vs. proposed:{" "}
                      <b>
                        {preview.decisions_qualifying_now} → {preview.decisions_qualifying_under_proposed}
                      </b>{" "}
                      ({preview.signal_frequency_change >= 0 ? "+" : ""}
                      {preview.signal_frequency_change})
                    </p>
                  </div>
                ) : (
                  <p className="regime-desc">Preview impact unavailable.</p>
                )}

                <label className="risk-confirm-checkbox">
                  <input type="checkbox" checked={confirmChecked} onChange={(e) => setConfirmChecked(e.target.checked)} />
                  I understand this lowers Binance Real's directional-quality bar and want to proceed. This does not
                  enable live execution.
                </label>

                <div className="controls" style={{ marginTop: 16 }}>
                  <button onClick={() => doSave(true)} disabled={!confirmChecked || saving}>
                    {saving ? "Saving…" : "Confirm and Save"}
                  </button>
                  <button
                    onClick={() => {
                      setConfirmOpen(false);
                      setConfirmChecked(false);
                      setPreview(null);
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
