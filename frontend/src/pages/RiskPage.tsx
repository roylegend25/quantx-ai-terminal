import { useCallback, useEffect, useRef, useState } from "react";
import { OctagonX, RefreshCw, ShieldCheck, SlidersHorizontal } from "lucide-react";
import Card from "../components/Layout/Card";
import RiskStatusCard from "../components/Dashboard/RiskStatusCard";
import PaperLiveTabs, { type PaperLiveTab } from "../components/Trading/PaperLiveTabs";
import CloseAllPositionsModal from "../components/Trading/CloseAllPositionsModal";
import RiskLimitsModal from "../components/Trading/RiskLimitsModal";
import RiskSettingsForm from "../components/Trading/RiskSettingsForm";
import BinanceRiskStatusCard from "../components/Trading/BinanceRiskStatusCard";
import BinanceDecisionStatusCard from "../components/Trading/BinanceDecisionStatusCard";
import { useTradingStatus } from "../components/Trading/TradingShared";
import { useBinanceAccount } from "../hooks/useBinanceAccount";
import { useAdminServerConfig } from "../hooks/useAdminServerConfig";
import { fmtLocalDateTime, fmtPct, fmtUsd } from "../lib/format";
import { api } from "../services/api";
import type { AppData } from "../hooks/useAppData";

export default function RiskPage(props: AppData) {
  const { portfolio, positions, history, dashboard, prediction, showToast } = props;
  const risk = dashboard?.risk;
  const tradeRisk = prediction?.risk;

  const [tab, setTab] = useState<PaperLiveTab>("paper");

  const { status: liveStatus, reload: reloadStatus } = useTradingStatus();
  const { summary, positionRows: binancePositionRows, busy: binanceBusy, run: runBinance, reload: reloadBinance } =
    useBinanceAccount(showToast);
  const [showCloseAll, setShowCloseAll] = useState(false);
  const [killSwitchBusy, setKillSwitchBusy] = useState(false);
  const [switchingToPaper, setSwitchingToPaper] = useState(false);
  const [reloadingConfig, setReloadingConfig] = useState(false);
  const [showRiskLimits, setShowRiskLimits] = useState(false);
  const [riskStatus, setRiskStatus] = useState<any>(null);
  const [riskStatusError, setRiskStatusError] = useState(false);
  const [binanceDecision, setBinanceDecision] = useState<any>(null);
  const [binanceDecisionError, setBinanceDecisionError] = useState(false);
  // True only until the very first fetch attempt settles - distinguishes
  // "still loading" from "errored" so the cards don't flash an unavailable
  // state while a slow signed Binance read is still in flight.
  const [liveStatusLoading, setLiveStatusLoading] = useState(true);
  const { config: adminConfig, forbidden: adminForbidden, reload: reloadAdminConfig } = useAdminServerConfig();

  // Separate from the Paper tab's dashboard/prediction-driven state above -
  // these two calls hit the Binance/live-only endpoints and must never be
  // backfilled with paper values. liveStatusBusyRef prevents a slow request
  // (e.g. a real Binance read) from overlapping with the next poll tick.
  const liveStatusBusyRef = useRef(false);
  const reloadBinanceLiveStatus = useCallback(async () => {
    if (liveStatusBusyRef.current) return;
    liveStatusBusyRef.current = true;
    try {
      await Promise.all([
        api
          .binanceRiskStatus()
          .then((r: any) => {
            setRiskStatus(r);
            setRiskStatusError(false);
          })
          .catch(() => setRiskStatusError(true)),
        api
          .binanceDecisionStatus()
          .then((r: any) => {
            setBinanceDecision(r);
            setBinanceDecisionError(false);
          })
          .catch(() => setBinanceDecisionError(true)),
      ]);
    } finally {
      liveStatusBusyRef.current = false;
      setLiveStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    reloadBinanceLiveStatus();
    const id = window.setInterval(reloadBinanceLiveStatus, 8000);
    return () => window.clearInterval(id);
  }, [reloadBinanceLiveStatus]);

  // Extra immediate refresh the moment the Binance Live tab is opened, so
  // switching in never shows up-to-8s-stale numbers.
  useEffect(() => {
    if (tab === "binance") reloadBinanceLiveStatus();
  }, [tab, reloadBinanceLiveStatus]);

  async function handleSwitchToPaper() {
    setSwitchingToPaper(true);
    try {
      await api.lockBinanceLive();
      showToast("Switched back to Paper trading — live lock re-armed", "success");
      await Promise.all([reloadStatus(), reloadBinance(), reloadBinanceLiveStatus()]);
    } catch (e: any) {
      showToast(e?.message || "Failed to switch to Paper mode", "error");
    } finally {
      setSwitchingToPaper(false);
    }
  }

  async function handleReloadConfig() {
    setReloadingConfig(true);
    try {
      await api.adminReloadConfig();
      showToast("Backend config reloaded", "success");
      await Promise.all([reloadStatus(), reloadAdminConfig(), reloadBinanceLiveStatus()]);
    } catch (e: any) {
      showToast(e?.message || "Reload failed", "error");
    } finally {
      setReloadingConfig(false);
    }
  }

  async function handleSaveRiskLimits(limits: Record<string, unknown>) {
    try {
      await api.adminUpdateRiskLimits(limits);
      showToast("Risk limits updated", "success");
      setShowRiskLimits(false);
      await Promise.all([reloadStatus(), reloadAdminConfig(), reloadBinanceLiveStatus()]);
    } catch (e: any) {
      showToast(e?.message || "Failed to update risk limits", "error");
    }
  }

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
    await reloadBinanceLiveStatus();
  }

  async function handleEmergencyStop(active: boolean) {
    setKillSwitchBusy(true);
    try {
      await api.killSwitch(active, active ? "risk page emergency stop" : undefined);
      showToast(active ? "Kill switch ACTIVATED — all trading halted" : "Kill switch deactivated", "success");
      await Promise.all([reloadStatus(), reloadBinance(), reloadBinanceLiveStatus()]);
    } catch (e: any) {
      showToast(e?.message || "Kill switch action failed", "error");
    } finally {
      setKillSwitchBusy(false);
    }
  }

  const confidence = prediction?.confidence;
  const requiredConfidence = tradeRisk?.required_confidence;
  const lastDecisionTime = prediction?.computed_at ? fmtLocalDateTime(prediction.computed_at) : "—";

  return (
    <div className="page-grid">
      <Card title="Risk Management" full right={<PaperLiveTabs active={tab} onChange={setTab} />}>
        <p className="regime-desc">
          {tab === "paper"
            ? "Editable paper-mode risk limits and the bot's live decision status."
            : "Live Binance risk controls. Actions are protected by the server lock, live unlock, and risk gate."}
        </p>
      </Card>

      {tab === "binance" ? (
        <>
          <BinanceRiskStatusCard
            data={riskStatus}
            loading={liveStatusLoading}
            errored={riskStatusError}
            onRefresh={reloadBinanceLiveStatus}
          />

          <BinanceDecisionStatusCard
            data={binanceDecision}
            loading={liveStatusLoading}
            errored={binanceDecisionError}
            onRefresh={reloadBinanceLiveStatus}
          />

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
                <b className="tile-value">{fmtUsd(riskStatus?.max_daily_loss_usdt ?? liveStatus?.max_daily_loss_usdt)}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Daily Loss Used</span>
                <b className="tile-value red">{riskStatus?.available ? fmtUsd(riskStatus?.daily_loss_used_usdt) : "—"}</b>
              </div>
              <div>
                <span className="tile-label">Available Balance</span>
                <b className="tile-value">{summary?.available ? fmtUsd(summary?.available_balance) : "—"}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Margin Used</span>
                <b className="tile-value">{summary?.available ? fmtUsd(summary?.margin_used) : "—"}</b>
              </div>
              <div>
                <span className="tile-label">Free Margin</span>
                <b className="tile-value">{riskStatus?.available ? fmtUsd(riskStatus?.free_margin) : "—"}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Wallet Balance</span>
                <b className="tile-value">{riskStatus?.available ? fmtUsd(riskStatus?.wallet_balance) : "—"}</b>
              </div>
              <div>
                <span className="tile-label">Total Notional Exposure</span>
                <b className="tile-value">{riskStatus?.available ? fmtUsd(riskStatus?.total_notional_exposure) : "—"}</b>
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
                <b className="tile-value">
                  {binancePositionRows.length}
                  {riskStatus?.max_open_positions != null ? ` of ${riskStatus.max_open_positions}` : ""}
                </b>
              </div>
              <div className="align-right">
                <span className="tile-label">Open Order Risk</span>
                <b className="tile-value">{riskStatus?.open_orders ?? "—"}</b>
              </div>
              <div>
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
                  runBinance(() => api.binanceCancelAllOrders(), "All Binance orders canceled").then(
                    reloadBinanceLiveStatus
                  )
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
              <button
                className="mini-btn"
                disabled={switchingToPaper || (liveStatus?.active_mode || "PAPER") === "PAPER"}
                onClick={handleSwitchToPaper}
                title="Re-arms the live lock — the unlock ceremony is required again to return to Binance Live"
              >
                <ShieldCheck size={13} /> {switchingToPaper ? "Switching…" : "Switch back to Paper"}
              </button>
              <button
                className="mini-btn"
                disabled={adminForbidden || !adminConfig}
                title={adminForbidden ? "Admin privileges required" : "Edit server-side risk limits"}
                onClick={() => setShowRiskLimits(true)}
              >
                <SlidersHorizontal size={13} /> Edit Risk Limits
              </button>
              <button
                className="mini-btn"
                disabled={adminForbidden || reloadingConfig}
                title={adminForbidden ? "Admin privileges required" : "Re-apply the server .env's allowed trading keys"}
                onClick={handleReloadConfig}
              >
                <RefreshCw size={13} /> {reloadingConfig ? "Reloading…" : "Reload Backend Config"}
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

          {showRiskLimits && adminConfig && (
            <RiskLimitsModal
              config={adminConfig}
              busy={false}
              onClose={() => setShowRiskLimits(false)}
              onSave={handleSaveRiskLimits}
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

      <RiskSettingsForm showToast={showToast} />

      <Card title="Live Decision Risk Summary">
        <div className="kv-grid">
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
