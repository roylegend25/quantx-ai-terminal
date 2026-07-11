import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, OctagonX } from "lucide-react";
import Card from "../components/Layout/Card";
import RiskStatusCard from "../components/Dashboard/RiskStatusCard";
import PaperLiveTabs, { type PaperLiveTab } from "../components/Trading/PaperLiveTabs";
import { useTradingStatus } from "../components/Trading/TradingShared";
import { useBinanceAccount } from "../hooks/useBinanceAccount";
import { fmtLocalDateTime, fmtPct, fmtUsd } from "../lib/format";
import { api } from "../services/api";
import type { AppData } from "../hooks/useAppData";

const CLOSE_ALL_PHRASE = "CLOSE ALL POSITIONS";

/** Typed-confirmation gate for the one genuinely new, high-risk action on
 *  this page - closing every open Binance position in one go. Reuses the
 *  existing single-position close endpoint per position (no new backend
 *  bulk-close surface), so each close still goes through the same
 *  server-side risk-gate and audit checks as any other close. */
function CloseAllPositionsModal({
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

export default function RiskPage(props: AppData) {
  const { portfolio, positions, history, dashboard, prediction, showToast } = props;
  const risk = dashboard?.risk;
  const tradeRisk = prediction?.risk;

  const [tab, setTab] = useState<PaperLiveTab>("paper");
  const [settings, setSettings] = useState<RiskSettingsData | null>(null);
  const [draft, setDraft] = useState<RiskSettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const { status: liveStatus, reload: reloadStatus } = useTradingStatus();
  const { summary, positionRows: binancePositionRows, busy: binanceBusy, run: runBinance, reload: reloadBinance } =
    useBinanceAccount(showToast);
  const [showCloseAll, setShowCloseAll] = useState(false);
  const [killSwitchBusy, setKillSwitchBusy] = useState(false);

  async function handleCloseAllPositions() {
    setShowCloseAll(false);
    let ok = 0;
    let failed = 0;
    await runBinance(async () => {
      for (const p of binancePositionRows) {
        try {
          await api.binanceClosePosition({ symbol: p.symbol, position_id: p.id });
          ok += 1;
        } catch {
          failed += 1;
        }
      }
      if (failed > 0) throw new Error(`Closed ${ok}, ${failed} failed — check Open Positions`);
    }, `Closed ${ok} Binance position${ok === 1 ? "" : "s"}`);
  }

  async function handleEmergencyStop(active: boolean) {
    setKillSwitchBusy(true);
    try {
      await api.killSwitch(active, active ? "risk page emergency stop" : undefined);
      showToast(active ? "Kill switch ACTIVATED — all trading halted" : "Kill switch deactivated", "success");
      await Promise.all([reloadStatus(), reloadBinance()]);
    } catch (e: any) {
      showToast(e?.message || "Kill switch action failed", "error");
    } finally {
      setKillSwitchBusy(false);
    }
  }

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

  const confidence = prediction?.confidence;
  const requiredConfidence = tradeRisk?.required_confidence;
  const lastDecisionTime = prediction?.computed_at ? fmtLocalDateTime(prediction.computed_at) : "—";

  return (
    <div className="page-grid">
      <Card title="Risk Management" full right={<PaperLiveTabs active={tab} onChange={setTab} />}>
        <p className="regime-desc">
          {tab === "paper"
            ? "Editable paper-mode risk limits and the bot's live decision status."
            : "Read-only Binance Real Money risk snapshot, plus emergency controls. Limits are edited from Binance Real → Server Trading Control (admin-only)."}
        </p>
      </Card>

      {tab === "binance" ? (
        <>
          <Card title="Binance Live Risk" full className="live-danger-card">
            <div className="kv-grid">
              <div>
                <span className="tile-label">Server Live Lock</span>
                <b className={`tile-value ${liveStatus?.binance_live_enabled_by_server ? "red" : "green"}`}>
                  {liveStatus?.binance_live_enabled_by_server ? "OPEN" : "ENGAGED"}
                </b>
              </div>
              <div className="align-right">
                <span className="tile-label">User Live Unlock</span>
                <b className={`tile-value ${liveStatus?.binance_live_unlocked_by_user ? "red" : "green"}`}>
                  {liveStatus?.binance_live_unlocked_by_user ? "UNLOCKED" : "LOCKED"}
                </b>
              </div>
              <div>
                <span className="tile-label">Max Leverage</span>
                <b className="tile-value">{liveStatus?.max_leverage != null ? `${liveStatus.max_leverage}x` : "—"}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Max Notional / Trade</span>
                <b className="tile-value">{fmtUsd(liveStatus?.max_notional_per_trade)}</b>
              </div>
              <div>
                <span className="tile-label">Max Daily Loss</span>
                <b className="tile-value">{fmtUsd(liveStatus?.max_daily_loss_usdt)}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Available Balance</span>
                <b className="tile-value">{summary?.available ? fmtUsd(summary?.available_balance) : "—"}</b>
              </div>
              <div>
                <span className="tile-label">Margin Used</span>
                <b className="tile-value">{summary?.available ? fmtUsd(summary?.margin_used) : "—"}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Nearest Liq. Distance</span>
                <b className="tile-value orange">
                  {summary?.available && summary?.nearest_liquidation_distance_pct != null
                    ? fmtPct(summary.nearest_liquidation_distance_pct)
                    : "—"}
                </b>
              </div>
              <div>
                <span className="tile-label">Open Positions</span>
                <b className="tile-value">{binancePositionRows.length}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Kill Switch</span>
                <b className={`tile-value ${liveStatus?.kill_switch_active ? "red" : "green"}`}>
                  {liveStatus?.kill_switch_active ? "ACTIVE" : "OFF"}
                </b>
              </div>
            </div>

            <div className="controls" style={{ marginTop: 18 }}>
              {liveStatus?.kill_switch_active ? (
                <button className="btn-long" disabled={killSwitchBusy} onClick={() => handleEmergencyStop(false)}>
                  Re-enable Trading
                </button>
              ) : (
                <button
                  className="btn-danger"
                  disabled={killSwitchBusy}
                  onClick={() => {
                    if (window.confirm("EMERGENCY STOP?\n\nStops the bot and blocks ALL new paper and real trades. Open positions are NOT auto-closed.")) {
                      handleEmergencyStop(true);
                    }
                  }}
                >
                  <OctagonX size={14} /> Emergency Stop
                </button>
              )}
              <button
                className="mini-btn"
                disabled={binanceBusy}
                onClick={() =>
                  runBinance(() => api.binanceCancelAllOrders(), "All Binance orders canceled")
                }
              >
                Cancel All Binance Orders
              </button>
              <button
                className="btn-danger"
                disabled={binanceBusy || binancePositionRows.length === 0}
                onClick={() => setShowCloseAll(true)}
              >
                Close All Binance Positions
              </button>
            </div>
          </Card>

          {showCloseAll && (
            <CloseAllPositionsModal
              positionCount={binancePositionRows.length}
              busy={binanceBusy}
              onClose={() => setShowCloseAll(false)}
              onConfirm={handleCloseAllPositions}
            />
          )}
        </>
      ) : (
      <>
      <RiskStatusCard portfolio={portfolio} positions={positions} history={history} />

      <Card title="Bot Decision Status">
        <div className={`regime-focus ${tradeRisk ? (tradeRisk.allowed ? "allowed" : "blocked") : ""}`}>
          <span className="tile-label">Current Trade</span>
          <b className={`tile-value ${tradeRisk ? (tradeRisk.allowed ? "green" : "red") : ""}`}>
            {tradeRisk ? (tradeRisk.allowed ? "ALLOWED" : "BLOCKED") : "Awaiting decision"}
          </b>
          <p className="regime-desc">
            {tradeRisk
              ? tradeRisk.allowed
                ? "Bot is running and this signal clears the current risk settings."
                : `Bot is running but waiting because: ${tradeRisk.reason}`
              : "Waiting for the risk gate to evaluate this trade."}
          </p>
        </div>

        <div className="kv-grid" style={{ marginTop: 14 }}>
          <div>
            <span className="tile-label">Current Confidence</span>
            <b className="tile-value">{typeof confidence === "number" ? fmtPct(confidence, 1) : "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Required Confidence</span>
            <b className="tile-value">{typeof requiredConfidence === "number" ? fmtPct(requiredConfidence, 1) : "—"}</b>
          </div>
          <div>
            <span className="tile-label">Risk Gate</span>
            <b className={`tile-value ${tradeRisk ? (tradeRisk.allowed ? "green" : "red") : ""}`}>
              {tradeRisk ? (tradeRisk.allowed ? "Allowed" : "Blocked") : "—"}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">Last Decision Time</span>
            <b className="tile-value">{lastDecisionTime}</b>
          </div>
        </div>
      </Card>

      <Card title="Risk Limits" full>
        {loading || !draft ? (
          <p className="regime-desc">Loading risk settings…</p>
        ) : (
          <>
            <p className="regime-desc">
              Editable paper-mode limits. Changes take effect on the next prediction/scheduler cycle - no restart
              needed. Live trading remains permanently locked in this build.
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

        <div className="kv-grid" style={{ marginTop: 20 }}>
          <div>
            <span className="tile-label">Max Risk / Trade</span>
            <b className="tile-value">{fmtPct(risk?.max_risk_per_trade_pct, 2)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Daily Loss Limit</span>
            <b className="tile-value red">{fmtPct(risk?.daily_loss_limit_pct, 2)}</b>
          </div>
          <div>
            <span className="tile-label">Live Mode</span>
            <b className="tile-value">{risk?.live_mode_locked ? "LOCKED" : "UNLOCKED"}</b>
          </div>
        </div>
      </Card>
      </>
      )}
    </div>
  );
}
