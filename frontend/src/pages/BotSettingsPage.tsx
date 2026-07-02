import { Activity, PauseCircle, PlayCircle, RefreshCcw, Shield, StopCircle } from "lucide-react";
import Card from "../components/Layout/Card";
import type { AppData } from "../hooks/useAppData";

export default function BotSettingsPage(props: AppData) {
  const { botStatus, dashboard, load } = props;
  const status = (botStatus?.status || dashboard?.bot?.status || "loading").toUpperCase();
  const mode = (botStatus?.mode || dashboard?.mode || "paper").toUpperCase();
  const liveEnabled = botStatus?.live_trading_enabled ?? dashboard?.bot?.live_trading ?? false;

  return (
    <div className="page-grid">
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
            <b className={`tile-value ${liveEnabled ? "green" : "red"}`}>{liveEnabled ? "ENABLED" : "LOCKED"}</b>
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
    </div>
  );
}
