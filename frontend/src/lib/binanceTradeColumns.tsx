import type { AutoCardColumn } from "../components/Responsive/AutoCardTable";
import { fmtLocalDateTime, fmtNum } from "./format";

/** Shared "real Binance trade" table columns + detail panel - originally
 *  built for BotTradesPage.tsx's Binance journal, reused wherever else a
 *  closed/recent Binance trade list needs to render (Performance and
 *  Execution pages' Binance Live tabs) so the shape stays identical
 *  everywhere instead of drifting across three hand-copied definitions. */

export const BINANCE_TRADE_COLUMNS: AutoCardColumn<any>[] = [
  { key: "time", label: "Time", render: (t) => fmtLocalDateTime(t.created_at) },
  { key: "label", label: "Label", render: () => <span className="badge badge-green" style={{ fontSize: 10 }}>BOT</span> },
  { key: "symbol", label: "Symbol", render: (t) => <b>{t.symbol}</b> },
  { key: "side", label: "Side", render: (t) => <span className={t.side === "LONG" ? "green" : "red"}>{t.side}</span> },
  { key: "action", label: "Action", render: (t) => t.action?.toUpperCase() },
  { key: "qty", label: "Qty", render: (t) => fmtNum(t.quantity, 6) },
  { key: "avgFill", label: "Avg Fill", render: (t) => (t.avg_fill_price != null ? fmtNum(t.avg_fill_price) : "—") },
  { key: "status", label: "Status", render: (t) => t.status },
  { key: "orderId", label: "Order ID", hideOnCard: true, render: (t) => t.order_id },
  { key: "clientOrderId", label: "Client Order ID", hideOnCard: true, render: (t) => t.client_order_id },
  { key: "type", label: "Type", hideOnCard: true, render: (t) => t.order_type },
  { key: "reduceOnly", label: "Reduce Only", hideOnCard: true, render: (t) => (t.reduce_only ? "Yes" : "No") },
  { key: "tpSlOrders", label: "TP/SL Orders", hideOnCard: true, render: (t) => `${t.tp_order_id ?? "—"} / ${t.sl_order_id ?? "—"}` },
  { key: "engine", label: "Engine", hideOnCard: true, render: (t) => t.model || t.strategy || "—" },
  { key: "conf", label: "Conf.", hideOnCard: true, render: (t) => (t.confidence != null ? `${fmtNum(t.confidence, 1)}%` : "—") },
  { key: "reason", label: "Reason", hideOnCard: true, render: (t) => <span className="bot-trade-reason">{t.decision_reason || "—"}</span> },
];

export function BinanceTradeDetail({ t }: { t: any }) {
  return (
    <div className="auto-card-grid">
      <div>
        <span className="tile-label">Order ID</span>
        <b className="tile-value">{t.order_id}</b>
      </div>
      <div>
        <span className="tile-label">Client Order ID</span>
        <b className="tile-value" style={{ fontSize: 11 }}>{t.client_order_id}</b>
      </div>
      <div>
        <span className="tile-label">Type</span>
        <b className="tile-value">{t.order_type}</b>
      </div>
      <div>
        <span className="tile-label">Reduce Only</span>
        <b className="tile-value">{t.reduce_only ? "Yes" : "No"}</b>
      </div>
      <div>
        <span className="tile-label">TP/SL Orders</span>
        <b className="tile-value" style={{ fontSize: 11 }}>{t.tp_order_id ?? "—"} / {t.sl_order_id ?? "—"}</b>
      </div>
      <div>
        <span className="tile-label">Engine</span>
        <b className="tile-value">{t.model || t.strategy || "—"}</b>
      </div>
      <div>
        <span className="tile-label">Confidence</span>
        <b className="tile-value">{t.confidence != null ? `${fmtNum(t.confidence, 1)}%` : "—"}</b>
      </div>
      <div style={{ gridColumn: "1 / -1" }}>
        <span className="tile-label">Reason</span>
        <b className="tile-value" style={{ fontWeight: 400 }}>{t.decision_reason || "—"}</b>
      </div>
    </div>
  );
}
