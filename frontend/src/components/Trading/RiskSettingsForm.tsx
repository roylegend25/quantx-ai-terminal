import { useEffect, useState } from "react";
import Card from "../Layout/Card";
import { fmtLocalDateTime } from "../../lib/format";
import { api } from "../../services/api";

/** Editable risk-limits form (min confidence, risk/trade, loss limits, open
 *  positions, cooldown, long/short toggles, ...), extracted from
 *  RiskPage.tsx so both the Paper and Binance Live tabs of Bot Settings /
 *  Risk Management can render the exact same form against the exact same
 *  backend (GET/PUT/POST /api/risk/settings). This is intentionally a
 *  single shared surface, not two - the real risk gate
 *  (app/trading/real_risk_gate.py) reads the identical settings row the
 *  paper gate reads, so there is only one set of limits to edit. */

type RiskSettingsData = {
  min_confidence_to_trade: number;
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
  updated_at: string | null;
};

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
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="risk-field">
      <div className="risk-field-head">
        <span className="tile-label">{label}</span>
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

type Props = {
  showToast: (message: string, tone?: "success" | "error") => void;
  /** Override the default explanatory paragraph (e.g. Bot Settings' Binance
   *  tab wants to stress the shared-with-paper note more prominently). */
  note?: string;
  /** Card title - defaults to "Risk Limits". */
  title?: string;
};

export default function RiskSettingsForm({ showToast, note, title = "Risk Limits" }: Props) {
  const [settings, setSettings] = useState<RiskSettingsData | null>(null);
  const [draft, setDraft] = useState<RiskSettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .riskSettingsGet()
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
  }, []);

  function patchDraft(patch: Partial<RiskSettingsData>) {
    setDraft((d) => (d ? { ...d, ...patch } : d));
  }

  function applyPreset(preset: (typeof PRESETS)[number]) {
    patchDraft({ min_confidence_to_trade: preset.confidence, max_risk_per_trade_pct: preset.risk });
  }

  async function handleSave() {
    if (!draft) return;
    setSaving(true);
    try {
      const { updated_at, ...patch } = draft;
      const res = await api.riskSettingsUpdate(patch);
      setSettings(res);
      setDraft(res);
      showToast("Risk settings saved");
    } catch (e: any) {
      showToast(e?.message || "Failed to save risk settings", "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    setSaving(true);
    try {
      const res = await api.riskSettingsReset();
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

  return (
    <Card title={title} full>
      {loading || !draft ? (
        <p className="regime-desc">Loading risk settings…</p>
      ) : (
        <>
          <p className="regime-desc">
            {note ??
              "Changes take effect on the next prediction/scheduler cycle - no restart needed. These limits are " +
                "shared between Paper and Binance Live execution (the real risk gate reads the same settings); " +
                "Binance real orders are gated additionally by the server live lock, live unlock and kill switch."}
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
            <RiskToggle
              label="Paper Trading Enabled"
              checked={draft.paper_trading_enabled}
              onChange={(v) => patchDraft({ paper_trading_enabled: v })}
            />
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
            Last updated: {settings?.updated_at ? fmtLocalDateTime(settings.updated_at) : "—"}
          </p>
        </>
      )}
    </Card>
  );
}
