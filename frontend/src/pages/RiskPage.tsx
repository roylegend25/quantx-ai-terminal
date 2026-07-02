import Card from "../components/Layout/Card";
import RiskStatusCard from "../components/Dashboard/RiskStatusCard";
import { fmtPct } from "../lib/format";
import type { AppData } from "../hooks/useAppData";

export default function RiskPage(props: AppData) {
  const { portfolio, positions, history, dashboard, prediction } = props;
  const risk = dashboard?.risk;
  const tradeRisk = prediction?.risk;

  return (
    <div className="page-grid">
      <RiskStatusCard portfolio={portfolio} positions={positions} history={history} />

      <Card title="Risk Manager Decision">
        <div className={`regime-focus ${tradeRisk ? (tradeRisk.allowed ? "allowed" : "blocked") : ""}`}>
          <span className="tile-label">Current Trade</span>
          <b className={`tile-value ${tradeRisk ? (tradeRisk.allowed ? "green" : "red") : ""}`}>
            {tradeRisk ? (tradeRisk.allowed ? "ALLOWED" : "BLOCKED") : "Awaiting decision"}
          </b>
          <p className="regime-desc">{tradeRisk?.reason ?? "Waiting for the risk gate to evaluate this trade."}</p>
        </div>
      </Card>

      <Card title="Risk Limits" full>
        <p className="regime-desc">Configured server-side by the risk manager. Editing limits is not yet exposed.</p>
        <div className="kv-grid" style={{ marginTop: 12 }}>
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
    </div>
  );
}
