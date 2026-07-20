import { useState } from "react";
import { Activity, PauseCircle, PlayCircle, RefreshCcw, Shield, StopCircle } from "lucide-react";
import Card from "../components/Layout/Card";
import PaperLiveTabs, { type PaperLiveTab } from "../components/Trading/PaperLiveTabs";
import DecisionEngineSettings from "../components/Trading/DecisionEngineSettings";
import RiskSettingsForm from "../components/Trading/RiskSettingsForm";
import ServerTradingControlCard from "../components/Trading/ServerTradingControlCard";
import UserLiveConfirmationCard from "../components/Trading/UserLiveConfirmationCard";
import ApiKeyStatusCard from "../components/Trading/ApiKeyStatusCard";
import { useTradingStatus } from "../components/Trading/TradingShared";
import { api } from "../services/api";
import { fmtUsd } from "../lib/format";
import type { AppData } from "../hooks/useAppData";
import TradingHorizonSettings from "../components/Trading/TradingHorizonSettings";

export default function BotSettingsPage(props: AppData) {
  const { botStatus, dashboard, load, showToast } = props;
  const status = (botStatus?.status || dashboard?.bot?.status || "loading").toUpperCase();
  const mode = (botStatus?.mode || dashboard?.mode || "paper").toUpperCase();
  const liveEnabled = botStatus?.live_trading_enabled ?? dashboard?.bot?.live_trading ?? false;

  const [tab, setTab] = useState<PaperLiveTab | "engine" | "horizon">("paper");
  const { status: liveStatus, reload: reloadLiveStatus } = useTradingStatus();

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

          {/* A. User live bot settings - shared with Paper: the real risk
             gate reads the identical settings row the paper gate reads, so
             there is only one form to edit, not two. */}
          <RiskSettingsForm
            showToast={showToast}
            title="User Live Bot Settings"
            note="Applies to both Paper and Binance Live execution - the real-order risk gate reads this exact settings row. Binance real orders are additionally gated by the server live lock, live unlock, symbol allowlist and kill switch below."
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
