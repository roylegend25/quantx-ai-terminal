import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Cpu,
  Database,
  Layers,
  MemoryStick,
  Server,
  XCircle,
  Zap,
} from "lucide-react";
import Card from "../components/Layout/Card";
import ExchangeStatusCard from "../components/Dashboard/ExchangeStatusCard";
import { fmtDuration, fmtNum, fmtPct, fmtRelativeTime } from "../lib/format";
import type { AppData } from "../hooks/useAppData";

type Props = AppData;

function StatusPill({ ok, label }: { ok: boolean | undefined; label: string }) {
  const known = ok !== undefined && ok !== null;
  return (
    <div className="analytics-tile status-tile">
      <span className="tile-label">{label}</span>
      <b className={`tile-value ${known ? (ok ? "green" : "red") : ""}`} style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {known ? (ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />) : "—"}
        {known ? (ok ? "Healthy" : "Down") : "Unknown"}
      </b>
    </div>
  );
}

export default function SystemStatusPage(props: Props) {
  const { systemStatus: s, exchangeStatus, exchangeRiskCheck, exchangeBalances, exchangePositions, exchangeOpenOrders } = props;
  const alerts: { level: string; message: string }[] = s?.alerts || [];

  return (
    <div className="page-grid">
      <Card title="System Health" wide>
        <div className="analytics-grid">
          <StatusPill ok={s?.backend?.status === "up"} label="Backend" />
          <StatusPill ok={s?.database?.ok} label="Database" />
          <StatusPill ok={s?.redis?.ok} label="Redis" />
          <StatusPill ok={s?.scheduler?.running} label="Scheduler" />
        </div>
      </Card>

      <Card title="Alerts">
        {alerts.length === 0 ? (
          <div className="analytics-tile" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <CheckCircle2 size={16} className="green" />
            <span>All systems normal</span>
          </div>
        ) : (
          <div className="chip-row" style={{ marginTop: 0, flexDirection: "column", alignItems: "stretch" }}>
            {alerts.map((a, i) => (
              <div
                key={i}
                className={`regime-focus ${a.level === "critical" ? "blocked" : ""}`}
                style={{ display: "flex", alignItems: "center", gap: 10 }}
              >
                <AlertTriangle size={16} className={a.level === "critical" ? "red" : "yellow"} />
                <span className="regime-desc" style={{ margin: 0 }}>{a.message}</span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Activity" full>
        <div className="analytics-grid">
          <div className="analytics-tile">
            <span className="tile-label">Last Prediction</span>
            <b className="tile-value">{fmtRelativeTime(s?.last_prediction_time)}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Last Paper Trade</span>
            <b className="tile-value">{fmtRelativeTime(s?.last_paper_trade_time)}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Last Model Training</span>
            <b className="tile-value">{fmtRelativeTime(s?.last_model_training_time)}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Uptime</span>
            <b className="tile-value">{fmtDuration(s?.uptime_seconds)}</b>
          </div>
        </div>

        <div className="indicators-row">
          <span className="tile-label">Active Strategies</span>
          <div className="chip-row">
            {(s?.active_strategies || []).map((name: string) => (
              <span className="chip" key={name}>
                <Layers size={14} /> {name.replace(/_/g, " ")}
              </span>
            ))}
            {(!s?.active_strategies || s.active_strategies.length === 0) && (
              <span className="analytics-empty">No strategies reported.</span>
            )}
          </div>
        </div>
      </Card>

      <Card title="Performance">
        <div className="analytics-grid">
          <div className="analytics-tile">
            <span className="tile-label">
              <Zap size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              API Latency
            </span>
            <b className="tile-value">{s?.api_latency_ms != null ? `${fmtNum(s.api_latency_ms, 0)}ms` : "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">
              <Cpu size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              CPU
            </span>
            <b className="tile-value">{fmtPct(s?.cpu_percent, 1)}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">
              <MemoryStick size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Memory
            </span>
            <b className="tile-value">{fmtPct(s?.memory?.system_percent, 1)}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">
              <Server size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Process RSS
            </span>
            <b className="tile-value">{s?.memory?.process_mb != null ? `${fmtNum(s.memory.process_mb, 0)}MB` : "—"}</b>
          </div>
        </div>
      </Card>

      <Card title="Paper Trading" right={<Clock size={16} className="green" />}>
        <div className="analytics-grid">
          <div className="analytics-tile">
            <span className="tile-label">
              <Activity size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Win Rate
            </span>
            <b className="tile-value">{fmtPct(s?.portfolio?.win_rate, 1)}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">
              <Database size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Total PnL
            </span>
            <b className={`tile-value ${(s?.portfolio?.total_pnl ?? 0) >= 0 ? "green" : "red"}`}>
              {s?.portfolio?.total_pnl != null ? `$${fmtNum(s.portfolio.total_pnl, 2)}` : "—"}
            </b>
          </div>
        </div>
      </Card>

      <Card title="Exchange Status" full>
        <ExchangeStatusCard
          exchangeStatus={exchangeStatus}
          exchangeRiskCheck={exchangeRiskCheck}
          exchangeBalances={exchangeBalances}
          exchangePositions={exchangePositions}
          exchangeOpenOrders={exchangeOpenOrders}
        />
      </Card>
    </div>
  );
}
