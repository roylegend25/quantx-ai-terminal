import { useState } from "react";
import { Activity, PauseCircle, PlayCircle, RefreshCcw, Shield, StopCircle } from "lucide-react";
import Card from "../components/Layout/Card";
import PaperLiveTabs, { type PaperLiveTab } from "../components/Trading/PaperLiveTabs";
import { useTradingStatus } from "../components/Trading/TradingShared";
import { fmtUsd } from "../lib/format";
import type { AppData } from "../hooks/useAppData";

export default function BotSettingsPage(props: AppData) {
  const { botStatus, dashboard, load } = props;
  const status = (botStatus?.status || dashboard?.bot?.status || "loading").toUpperCase();
  const mode = (botStatus?.mode || dashboard?.mode || "paper").toUpperCase();
  const liveEnabled = botStatus?.live_trading_enabled ?? dashboard?.bot?.live_trading ?? false;

  const [tab, setTab] = useState<PaperLiveTab>("paper");
  const { status: liveStatus } = useTradingStatus();

  return (
    <div className="page-grid">
      <Card title="Bot Settings" full right={<PaperLiveTabs active={tab} onChange={setTab} />}>
        <p className="regime-desc">
          {tab === "paper"
            ? "Global bot lifecycle controls (start/pause/stop, mode switch). Confidence threshold, max open positions and strategy settings live on the Risk Management page."
            : "Read-only recap of the server-side Binance Real Money configuration. API keys are managed on the server .env only — this page can never read or edit them."}
        </p>
      </Card>

      {tab === "paper" ? (
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
        <Card title="Binance Live Settings (read-only)" full className="live-danger-card">
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
          <p className="regime-desc" style={{ marginTop: 14 }}>
            To change these limits, open Binance Real → Server Trading Control (admin-only). API keys are managed on
            the server .env only.
          </p>
        </Card>
      )}
    </div>
  );
}
