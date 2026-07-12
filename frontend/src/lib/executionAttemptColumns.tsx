import type { AutoCardColumn } from "../components/Responsive/AutoCardTable";
import { fmtLocalDateTime, fmtNum, fmtUsd } from "./format";

/** Columns + expandable detail for the Execution Pipeline card's "last 100
 *  execution attempts" log (GET /api/trading/binance/execution-logs) -
 *  every real Binance order attempt, success or failure, stage-by-stage. */

export const EXECUTION_ATTEMPT_COLUMNS: AutoCardColumn<any>[] = [
  { key: "time", label: "Time", render: (a) => fmtLocalDateTime(a.created_at) },
  { key: "symbol", label: "Symbol", render: (a) => <b>{a.symbol}</b> },
  { key: "side", label: "Side", render: (a) => <span className={a.side === "LONG" ? "green" : "red"}>{a.side || "—"}</span> },
  {
    key: "status", label: "Result",
    render: (a) => (
      <span className={a.final_status === "order_accepted" ? "green" : "red"}>
        {a.is_test ? "TEST · " : ""}
        {a.final_status === "order_accepted" ? "Accepted" : "Failed"}
      </span>
    ),
  },
  { key: "confidence", label: "Confidence", render: (a) => (a.confidence != null ? `${fmtNum(a.confidence, 1)}%` : "—") },
  { key: "reqQty", label: "Req. Qty", render: (a) => fmtNum(a.requested_quantity, 6) },
  { key: "adjQty", label: "Adj. Qty", render: (a) => fmtNum(a.adjusted_quantity, 6) },
  { key: "leverage", label: "Leverage", hideOnCard: true, render: (a) => (a.leverage != null ? `${a.leverage}x` : "—") },
  { key: "margin", label: "Margin Req.", hideOnCard: true, render: (a) => fmtUsd(a.margin_required) },
  { key: "latency", label: "Latency", hideOnCard: true, render: (a) => (a.latency_ms != null ? `${fmtNum(a.latency_ms, 0)}ms` : "—") },
  { key: "orderId", label: "Order ID", hideOnCard: true, render: (a) => a.order_id ?? "—" },
  { key: "reason", label: "Reason", hideOnCard: true, render: (a) => <span className="bot-trade-reason">{a.final_reason || "—"}</span> },
];

export function ExecutionAttemptDetail({ a }: { a: any }) {
  return (
    <div className="auto-card-grid">
      <div>
        <span className="tile-label">Mode</span>
        <b className="tile-value">{a.mode || "—"}</b>
      </div>
      <div>
        <span className="tile-label">Order ID</span>
        <b className="tile-value">{a.order_id ?? "—"}</b>
      </div>
      <div>
        <span className="tile-label">Leverage</span>
        <b className="tile-value">{a.leverage != null ? `${a.leverage}x` : "—"}</b>
      </div>
      <div>
        <span className="tile-label">Margin Required</span>
        <b className="tile-value">{fmtUsd(a.margin_required)}</b>
      </div>
      <div>
        <span className="tile-label">Requested Notional</span>
        <b className="tile-value">{fmtUsd(a.requested_notional)}</b>
      </div>
      <div>
        <span className="tile-label">Execution Latency</span>
        <b className="tile-value">{a.latency_ms != null ? `${fmtNum(a.latency_ms, 0)}ms` : "—"}</b>
      </div>
      <div style={{ gridColumn: "1 / -1" }}>
        <span className="tile-label">Reason</span>
        <b className="tile-value" style={{ fontWeight: 400 }}>{a.final_reason || "—"}</b>
      </div>
      <div style={{ gridColumn: "1 / -1" }}>
        <span className="tile-label">Stages</span>
        <ul className="reason-list">
          {(a.stages || []).map((s: any) => (
            <li key={s.stage} className={s.status === "failed" ? "red" : s.status === "success" ? "" : "yellow"}>
              {s.stage.replace(/_/g, " ")}: {s.status}
              {s.reason ? ` — ${s.reason}` : ""}
            </li>
          ))}
        </ul>
      </div>
      {a.exchange_response && (
        <div style={{ gridColumn: "1 / -1" }}>
          <span className="tile-label">Exchange Response</span>
          <pre className="exec-raw-response">{JSON.stringify(a.exchange_response, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
