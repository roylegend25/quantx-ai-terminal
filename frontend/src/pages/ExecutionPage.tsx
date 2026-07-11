import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  RotateCw,
  ShieldAlert,
  Target,
  XCircle,
  Zap,
} from "lucide-react";
import Card from "../components/Layout/Card";
import { fmtNum, fmtPct } from "../lib/format";
import LocalTime from "../components/LocalTime";
import AutoCardTable, { type AutoCardColumn } from "../components/Responsive/AutoCardTable";
import type { AppData } from "../hooks/useAppData";

type Props = AppData;

const QUALITY_TONE: Record<string, string> = {
  EXCELLENT: "green",
  GOOD: "green",
  FAIR: "yellow",
  POOR: "red",
  PARTIAL: "yellow",
};

function QualityBadge({ quality }: { quality?: string | null }) {
  if (!quality) return <span className="badge">—</span>;
  const tone = QUALITY_TONE[quality] || "";
  return <span className={`badge ${tone === "green" ? "badge-green" : tone === "red" ? "badge-red" : ""}`}>{quality}</span>;
}

function StatusIcon({ status }: { status: string }) {
  if (status === "FILLED") return <CheckCircle2 size={14} className="green" />;
  if (status === "PARTIAL") return <AlertTriangle size={14} className="yellow" />;
  return <XCircle size={14} className="red" />;
}

const EXECUTION_COLUMNS: AutoCardColumn<any>[] = [
  { key: "time", label: "Time", render: (r) => <LocalTime value={r.recorded_at} label="Recorded" /> },
  { key: "symbol", label: "Symbol", render: (r) => <b>{r.symbol}</b> },
  { key: "side", label: "Side", render: (r) => <span className={r.side === "LONG" ? "green" : "red"}>{r.side}</span> },
  { key: "type", label: "Type", render: (r) => r.order_type },
  {
    key: "status",
    label: "Status",
    render: (r) => (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <StatusIcon status={r.status} /> {r.status}
      </span>
    ),
  },
  {
    key: "filled",
    label: "Filled",
    render: (r) => (r.filled_qty != null ? `${fmtNum(r.filled_qty, 4)}/${fmtNum(r.requested_qty, 4)}` : "—"),
  },
  { key: "fillPrice", label: "Fill Price", render: (r) => (r.avg_fill_price != null ? fmtNum(r.avg_fill_price, 2) : "—") },
  {
    key: "slippage",
    label: "Slippage",
    render: (r) => (r.actual_slippage_bps != null ? `${fmtNum(r.actual_slippage_bps, 2)} bps` : "—"),
  },
  { key: "latency", label: "Latency", render: (r) => `${fmtNum(r.latency_ms, 0)}ms` },
  { key: "retries", label: "Retries", render: (r) => r.retries },
  { key: "quality", label: "Quality", render: (r) => <QualityBadge quality={r.execution_quality} /> },
];

export default function ExecutionPage({ executionStatus, executionMetrics }: Props) {
  const breaker = executionStatus?.circuit_breaker;
  const safety = executionStatus?.safety;
  const m = executionMetrics;
  const recent: any[] = m?.recent_executions || [];
  const last = m?.last_execution;
  const quality = m?.quality_breakdown || {};

  const healthy = executionStatus?.engine === "operational" && !breaker?.open;

  return (
    <div className="page-grid">
      <Card title="Execution Health" wide>
        <div className="analytics-grid">
          <div className="analytics-tile status-tile">
            <span className="tile-label">Engine</span>
            <b className={`tile-value ${healthy ? "green" : "red"}`} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {healthy ? <CheckCircle2 size={16} /> : <ShieldAlert size={16} />}
              {healthy ? "Healthy" : breaker?.open ? "Circuit Breaker Open" : "Unavailable"}
            </b>
          </div>
          <div className="analytics-tile status-tile">
            <span className="tile-label">Mode</span>
            <b className="tile-value">{(executionStatus?.mode || "paper").toUpperCase()}</b>
          </div>
          <div className="analytics-tile status-tile">
            <span className="tile-label">Circuit Breaker</span>
            <b className={`tile-value ${breaker?.open ? "red" : "green"}`}>
              {breaker ? (breaker.open ? "TRIPPED" : "CLOSED") : "—"}
            </b>
          </div>
          <div className="analytics-tile status-tile">
            <span className="tile-label">Consecutive Failures</span>
            <b className="tile-value">
              {breaker ? `${breaker.consecutive_failures} / ${breaker.threshold}` : "—"}
            </b>
          </div>
        </div>
      </Card>

      <Card title="Safety Configuration">
        <div className="kv-grid">
          <div>
            <span className="tile-label">Max Signal Age</span>
            <b className="tile-value">{safety?.max_signal_age_seconds != null ? `${safety.max_signal_age_seconds}s` : "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Duplicate Window</span>
            <b className="tile-value">{safety?.duplicate_window_seconds != null ? `${safety.duplicate_window_seconds}s` : "—"}</b>
          </div>
          <div>
            <span className="tile-label">Max Open Positions</span>
            <b className="tile-value">{safety?.max_open_positions ?? "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Max Risk / Trade</span>
            <b className="tile-value">{fmtPct(safety?.max_risk_per_trade_pct, 2)}</b>
          </div>
        </div>
      </Card>

      <Card title="Last Execution" right={<Clock size={16} className="green" />}>
        {!last ? (
          <p className="analytics-empty">No executions recorded yet.</p>
        ) : (
          <div className="kv-grid">
            <div>
              <span className="tile-label">Symbol / Side</span>
              <b className="tile-value">{last.symbol} {last.side}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Status</span>
              <b className="tile-value" style={{ display: "flex", alignItems: "center", gap: 6, justifyContent: "flex-end" }}>
                <StatusIcon status={last.status} /> {last.status}
              </b>
            </div>
            <div>
              <span className="tile-label">Fill Price</span>
              <b className="tile-value">{last.avg_fill_price != null ? fmtNum(last.avg_fill_price, 2) : "—"}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Quality</span>
              <b className="tile-value"><QualityBadge quality={last.execution_quality} /></b>
            </div>
            <div>
              <span className="tile-label">When</span>
              <b className="tile-value"><LocalTime value={last.recorded_at} label="Recorded" /></b>
            </div>
            <div className="align-right">
              <span className="tile-label">Reason</span>
              <b className="tile-value" style={{ fontWeight: 400, fontSize: 12 }}>{last.reason || "—"}</b>
            </div>
          </div>
        )}
      </Card>

      <Card title="Performance" full>
        <div className="analytics-grid">
          <div className="analytics-tile">
            <span className="tile-label">
              <Zap size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Avg Latency
            </span>
            <b className="tile-value">{m?.avg_latency_ms != null ? `${fmtNum(m.avg_latency_ms, 0)}ms` : "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">
              <Target size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Avg Slippage
            </span>
            <b className="tile-value">{m?.avg_slippage_bps != null ? `${fmtNum(m.avg_slippage_bps, 2)} bps` : "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Expected Slippage</span>
            <b className="tile-value">
              {m?.avg_expected_slippage_bps != null ? `${fmtNum(m.avg_expected_slippage_bps, 2)} bps` : "—"}
            </b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">
              <RotateCw size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Total Retries
            </span>
            <b className="tile-value">{m?.total_retries ?? "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Partial Fill Rate</span>
            <b className="tile-value">{fmtPct(m?.partial_fill_rate, 1)}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Successful Orders</span>
            <b className="tile-value green">{m?.successful_orders ?? "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Rejected Orders</span>
            <b className="tile-value red">{m?.rejected_orders ?? "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Total Orders</span>
            <b className="tile-value">{m?.total_orders ?? "—"}</b>
          </div>
        </div>

        {Object.keys(quality).length > 0 && (
          <div className="indicators-row">
            <span className="tile-label">Order Quality Breakdown</span>
            <div className="chip-row">
              {Object.entries(quality).map(([q, count]) => (
                <span className="chip" key={q}>
                  <Activity size={14} /> {q}: {count as number}
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>

      <Card title="Recent Executions" full>
        <AutoCardTable
          columns={EXECUTION_COLUMNS}
          rows={recent}
          keyField={(r) => r.order_id}
          titleColumn="symbol"
          statusColumn="status"
          emptyMessage="No executions recorded yet - the scheduler routes each paper trade through this engine."
        />
      </Card>
    </div>
  );
}
