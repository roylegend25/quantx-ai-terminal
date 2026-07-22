import { useState } from "react";
import { Activity, PauseCircle, PlayCircle, RefreshCcw, Shield, StopCircle } from "lucide-react";
import Card from "../components/Layout/Card";
import PaperLiveTabs, { type PaperLiveTab } from "../components/Trading/PaperLiveTabs";
import DecisionEngineSettings from "../components/Trading/DecisionEngineSettings";
import RiskSettingsForm from "../components/Trading/RiskSettingsForm";
import BinanceRealSafetyPanel from "../components/Trading/BinanceRealSafetyPanel";
import ServerTradingControlCard from "../components/Trading/ServerTradingControlCard";
import UserLiveConfirmationCard from "../components/Trading/UserLiveConfirmationCard";
import ApiKeyStatusCard from "../components/Trading/ApiKeyStatusCard";
import { useTradingStatus } from "../components/Trading/TradingShared";
import { api } from "../services/api";
import { fmtUsd } from "../lib/format";
import type { AppData } from "../hooks/useAppData";
import TradingHorizonSettings from "../components/Trading/TradingHorizonSettings";

type CopyDirection = "paper_to_real" | "real_to_paper";

function CopySettingsControls({
  showToast,
  onCopied,
}: {
  showToast: (message: string, tone?: "success" | "error") => void;
  onCopied: () => void;
}) {
  const [pending, setPending] = useState<CopyDirection | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  async function doCopy() {
    if (!pending) return;
    setBusy(true);
    try {
      const [fromScope, toScope] = pending === "paper_to_real" ? ["paper", "binance_real"] : ["binance_real", "paper"];
      await api.riskSettingsCopy(fromScope, toScope, reason || undefined, true);
      showToast(`Settings copied from ${fromScope} to ${toScope}. This never enables live execution.`, "success");
      setPending(null);
      setReason("");
      onCopied();
    } catch (e: any) {
      showToast(e?.message || "Failed to copy settings", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="controls" style={{ marginBottom: 12 }}>
      <button onClick={() => setPending("paper_to_real")}>Copy Paper Settings to Binance Real</button>
      <button onClick={() => setPending("real_to_paper")}>Copy Binance Real Settings to Paper</button>

      {pending && (
        <div className="modal-overlay">
          <div className="modal-content">
            <h3>Confirm copy</h3>
            <p className="regime-desc">
              {pending === "paper_to_real"
                ? "This overwrites Binance Real's confidence, point margin, evidence, and risk limits with Paper's current values."
                : "This overwrites Paper's confidence, point margin, evidence, and risk limits with Binance Real's current values."}{" "}
              This never enables live execution.
            </p>
            <input
              className="risk-number-input"
              style={{ width: "100%" }}
              placeholder="Reason (optional)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <div className="controls" style={{ marginTop: 16 }}>
              <button onClick={doCopy} disabled={busy}>
                {busy ? "Copying…" : "Confirm Copy"}
              </button>
              <button onClick={() => setPending(null)} disabled={busy}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function BotSettingsPage(props: AppData) {
  const { botStatus, dashboard, load, showToast } = props;
  const status = (botStatus?.status || dashboard?.bot?.status || "loading").toUpperCase();
  const mode = (botStatus?.mode || dashboard?.mode || "paper").toUpperCase();
  const liveEnabled = botStatus?.live_trading_enabled ?? dashboard?.bot?.live_trading ?? false;

  const [tab, setTab] = useState<PaperLiveTab | "engine" | "horizon">("paper");
  const { status: liveStatus, reload: reloadLiveStatus } = useTradingStatus();
  // Bumped after a settings copy so both RiskSettingsForm instances remount
  // and refetch, instead of a disruptive full-page reload.
  const [settingsRefreshKey, setSettingsRefreshKey] = useState(0);

  const refreshBinanceStatus = async () => {
    await Promise.all([reloadLiveStatus(), api.liveReadiness().catch(() => null)]);
    showToast("Binance status refreshed", "success");
  };

  return (
    <div className="page-grid">
      <Card title="Bot Settings" full right={<div className="bot-settings-tabs"><PaperLiveTabs active={tab === "engine" || tab === "horizon" ? "paper" : tab} onChange={setTab} /><button role="tab" aria-selected={tab === "engine"} className={tab === "engine" ? "mode-toggle-btn on paper" : "mode-toggle-btn"} onClick={() => setTab("engine")}>Decision Engine</button><button role="tab" aria-selected={tab === "horizon"} className={tab === "horizon" ? "mode-toggle-btn on paper" : "mode-toggle-btn"} onClick={() => setTab("horizon")}>Trading Horizon</button></div>}>
        <p className="regime-desc">
          {tab === "horizon" ? "Select a trading profile and inspect its authoritative execution timeframe, strict multi-timeframe readiness, invalidation, and blocker explanations."
            : tab === "engine"
            ? "Choose the single authoritative server-side decision engine. V2 is the default; V1 remains available for manual rollback."
            : tab === "paper"
            ? "Global bot lifecycle controls (start/pause/stop, mode switch). Confidence threshold, max open positions and strategy settings live on the Risk Management page."
            : "Live bot settings for real Binance Futures trading: user-editable risk limits (shared with Paper), server-protected limits (admin-only), and API key status. Keys are never exposed here."}
        </p>
      </Card>

      {tab === "horizon" ? (
        <Card title="Trading Horizon Bot Settings" full><TradingHorizonSettings /></Card>
      ) : tab === "engine" ? (
        <Card title="Decision Engine" full><DecisionEngineSettings showToast={showToast} /></Card>
      ) : tab === "paper" ? (
        <>
          <Card title="Bot Status">
            <div className="kv-grid">
              <div>
                <span className="tile-label">Status</span>
                <b className={`tile-value ${status === "RUNNING" ? "green" : status === "PAUSED" ? "yellow" : "red"}`}>
                  {status}
                </b>
              </div>
              <div className="align-right">
                <span className="tile-label">Trading Mode</span>
                <b className="tile-value">{mode}</b>
              </div>
              <div>
                <span className="tile-label">Live Trading</span>
                <b className={`tile-value ${liveEnabled ? "red" : "green"}`}>{liveEnabled ? "ENABLED" : "LOCKED"}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Last Action</span>
                <b className="tile-value">{botStatus?.last_action ?? "—"}</b>
              </div>
            </div>
          </Card>

          <Card title="Bot Controls" wide>
            <p className="regime-desc">
              Controls call the live bot-management API. Live trading is intentionally locked server-side until API
              keys, risk limits, and execution safeguards are configured.
            </p>
            <div className="controls" style={{ marginTop: 14 }}>
              <button onClick={() => props.botAction("start")}>
                <PlayCircle size={16} /> Start
              </button>
              <button onClick={() => props.botAction("pause")}>
                <PauseCircle size={16} /> Pause
              </button>
              <button onClick={() => props.botAction("stop")}>
                <StopCircle size={16} /> Stop
              </button>
              <button onClick={() => props.botAction("paper")}>
                <Shield size={16} /> Paper Mode
              </button>
              <button onClick={() => props.botAction("live")}>
                <Activity size={16} /> Live Mode
              </button>
              <button onClick={load}>
                <RefreshCcw size={16} /> Refresh
              </button>
            </div>
          </Card>

          <CopySettingsControls showToast={showToast} onCopied={() => setSettingsRefreshKey((k) => k + 1)} />
          <RiskSettingsForm key={`paper-${settingsRefreshKey}`} scope="paper" showToast={showToast} title="Paper Trading Settings" />
        </>
      ) : (
        <>
          <Card title="Live Snapshot" full className="live-danger-card">
            <div className="kv-grid">
              <div>
                <span className="tile-label">Active Mode</span>
                <b className="tile-value">{liveStatus?.active_mode || "—"}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Binance API</span>
                <b className="tile-value">{liveStatus?.binance_configured ? "Configured" : "Not Configured"}</b>
              </div>
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
                <span className="tile-label">Allowed Symbols</span>
                <b className="tile-value" style={{ fontSize: 13 }}>{(liveStatus?.allowed_symbols || []).join(", ") || "—"}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Max Leverage</span>
                <b className="tile-value">{liveStatus?.max_leverage != null ? `${liveStatus.max_leverage}x` : "—"}</b>
              </div>
              <div>
                <span className="tile-label">Max Notional / Trade</span>
                <b className="tile-value">{fmtUsd(liveStatus?.max_notional_per_trade)}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">Max Daily Loss</span>
                <b className="tile-value">{fmtUsd(liveStatus?.max_daily_loss_usdt)}</b>
              </div>
            </div>
          </Card>

          {/* A0. User live-risk confirmation - the missing button this page
             used to lack entirely (status-only "Locked" with nothing to
             click). Same shared card/ceremony as the Binance Real page. */}
          <UserLiveConfirmationCard status={liveStatus} onChanged={refreshBinanceStatus} showToast={showToast} />

          {/* A0b. Read-only safety section (Part 1): live execution status,
             server live lock, maintenance, Binance auth, real positions/orders. */}
          <BinanceRealSafetyPanel />

          <CopySettingsControls showToast={showToast} onCopied={() => setSettingsRefreshKey((k) => k + 1)} />

          {/* A. Binance Real's own settings - separate scope/row from Paper,
             with its own audit trail. Changing these never auto-enables
             live execution; that remains gated by the server live lock,
             live unlock ceremony, and kill switch below. */}
          <RiskSettingsForm
            key={`binance-${settingsRefreshKey}`}
            scope="binance_real"
            showToast={showToast}
            title="Binance Real Bot Settings"
          />

          {/* B. Server protected settings - admin-only, self-hides for
             non-admin tokens. Never reads/writes API keys. */}
          <ServerTradingControlCard showToast={showToast} />

          {/* C. API key status only - never the keys themselves. Every
             field comes from the same shared TradingStatus as the Binance
             Real page, Live Snapshot above, and Server Trading Control -
             no independent connectivity calculation. */}
          <ApiKeyStatusCard status={liveStatus} onRefresh={refreshBinanceStatus} />
        </>
      )}
    </div>
  );
}
