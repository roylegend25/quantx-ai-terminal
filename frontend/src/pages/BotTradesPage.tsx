import { useCallback, useEffect, useState } from "react";
import Card from "../components/Layout/Card";
import AutoCardTable, { type AutoCardColumn } from "../components/Responsive/AutoCardTable";
import { api } from "../services/api";
import { fmtLocalDateTime, fmtNum, fmtUsd, toneClass, toneOf } from "../lib/format";

const POLL_MS = 15000;

const ENGINE_LABEL: Record<string, string> = {
  champion_ml: "Champion ML",
  strategy_ensemble: "Strategy Ensemble",
  fallback: "Fallback",
  manual: "Manual",
};

const PAPER_COLUMNS: AutoCardColumn<any>[] = [
  { key: "id", label: "ID", render: (t) => `#${t.id}` },
  {
    key: "label",
    label: "Label",
    render: (t) => (
      <span className={`badge ${t.label === "BOT_TRADE" ? "badge-green" : ""}`} style={{ fontSize: 10 }}>
        {t.label === "BOT_TRADE" ? "BOT" : "MANUAL"}
      </span>
    ),
  },
  { key: "symbol", label: "Symbol", render: (t) => <b>{t.symbol}</b> },
  { key: "side", label: "Side", render: (t) => <span className={t.side === "LONG" ? "green" : "red"}>{t.side}</span> },
  {
    key: "opened",
    label: "Opened / Closed",
    render: (t) => `${fmtLocalDateTime(t.opened_at)}${t.closed_at ? ` → ${fmtLocalDateTime(t.closed_at)}` : ""}`,
  },
  { key: "entryExit", label: "Entry → Exit", render: (t) => `${fmtNum(t.entry)} → ${t.exit != null ? fmtNum(t.exit) : "open"}` },
  { key: "pnl", label: "PnL", render: (t) => <span className={toneClass(toneOf(t.realized_pnl))}>{fmtUsd(t.realized_pnl)}</span> },
  { key: "tpSl", label: "TP / SL", hideOnCard: true, render: (t) => `${t.tp != null ? fmtNum(t.tp) : "—"} / ${t.sl != null ? fmtNum(t.sl) : "—"}` },
  { key: "liq", label: "Est. Liq.", hideOnCard: true, render: (t) => (t.liquidation_price != null ? fmtNum(t.liquidation_price) : "—") },
  { key: "margin", label: "Margin", hideOnCard: true, render: (t) => fmtUsd(t.margin_used) },
  { key: "lev", label: "Lev.", hideOnCard: true, render: (t) => (t.leverage != null ? `${t.leverage}x` : "—") },
  { key: "engine", label: "Engine", hideOnCard: true, render: (t) => ENGINE_LABEL[t.decision_mode] || t.strategy_used || t.decision_mode || "—" },
  { key: "conf", label: "Conf.", hideOnCard: true, render: (t) => (t.confidence != null ? `${fmtNum(t.confidence, 1)}%` : "—") },
  {
    key: "reason",
    label: "Reason",
    hideOnCard: true,
    render: (t) => <span className="bot-trade-reason">{Array.isArray(t.decision_reasons) ? t.decision_reasons[0] : t.risk_reason || "—"}</span>,
  },
];

function PaperTradeDetail({ t }: { t: any }) {
  return (
    <div className="auto-card-grid">
      <div>
        <span className="tile-label">TP / SL</span>
        <b className="tile-value">{t.tp != null ? fmtNum(t.tp) : "—"} / {t.sl != null ? fmtNum(t.sl) : "—"}</b>
      </div>
      <div>
        <span className="tile-label">Est. Liq.</span>
        <b className="tile-value orange">{t.liquidation_price != null ? fmtNum(t.liquidation_price) : "—"}</b>
      </div>
      <div>
        <span className="tile-label">Margin</span>
        <b className="tile-value">{fmtUsd(t.margin_used)}</b>
      </div>
      <div>
        <span className="tile-label">Leverage</span>
        <b className="tile-value">{t.leverage != null ? `${t.leverage}x` : "—"}</b>
      </div>
      <div>
        <span className="tile-label">Engine</span>
        <b className="tile-value">{ENGINE_LABEL[t.decision_mode] || t.strategy_used || t.decision_mode || "—"}</b>
      </div>
      <div>
        <span className="tile-label">Confidence</span>
        <b className="tile-value">{t.confidence != null ? `${fmtNum(t.confidence, 1)}%` : "—"}</b>
      </div>
      <div style={{ gridColumn: "1 / -1" }}>
        <span className="tile-label">Reason</span>
        <b className="tile-value" style={{ fontWeight: 400 }}>
          {Array.isArray(t.decision_reasons) ? t.decision_reasons[0] : t.risk_reason || "—"}
        </b>
      </div>
    </div>
  );
}

const BINANCE_COLUMNS: AutoCardColumn<any>[] = [
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

function BinanceTradeDetail({ t }: { t: any }) {
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

/** Bot Trades (Phase 23): the two bot journals, strictly separate - the
 *  simulated paper ledger and the real Binance order journal. */
export default function BotTradesPage() {
  const [tab, setTab] = useState<"paper" | "binance">("paper");
  const [paper, setPaper] = useState<any[]>([]);
  const [binance, setBinance] = useState<any[]>([]);

  const load = useCallback(async () => {
    const [p, b] = await Promise.all([
      api.botTradesPaper().catch(() => null),
      api.botTradesBinance().catch(() => null),
    ]);
    if (p) setPaper(p.trades || []);
    if (b) setBinance(b.trades || []);
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  return (
    <div className="page-grid">
      <Card
        full
        title="Bot Trades"
        right={
          <div className="mode-toggle" role="group" aria-label="Bot trade journal">
            <button className={`mode-toggle-btn ${tab === "paper" ? "on paper" : ""}`} onClick={() => setTab("paper")}>
              Paper Bot Trades ({paper.length})
            </button>
            <button className={`mode-toggle-btn ${tab === "binance" ? "on live" : ""}`} onClick={() => setTab("binance")}>
              Binance Real Bot Trades ({binance.length})
            </button>
          </div>
        }
      >
        <p className="regime-desc">
          {tab === "paper"
            ? "Simulated positions the bot opened in the paper ledger, with full decision provenance."
            : "Real Binance orders this system placed and Binance accepted — journaled with decision provenance and the risk-gate result. Manual exchange activity never appears here."}
        </p>
      </Card>

      {tab === "paper" ? (
        <Card title="Paper Bot Trades" full>
          <AutoCardTable
            columns={PAPER_COLUMNS}
            rows={paper.slice(0, 50)}
            keyField={(t) => t.id}
            titleColumn="symbol"
            statusColumn="side"
            renderDetail={(t) => <PaperTradeDetail t={t} />}
            emptyMessage="No paper bot trades yet"
          />
        </Card>
      ) : (
        <Card title="Binance Real Bot Trades" full className="live-danger-card">
          <AutoCardTable
            columns={BINANCE_COLUMNS}
            rows={binance.slice(0, 50)}
            keyField={(t) => t.id}
            titleColumn="symbol"
            statusColumn="side"
            renderDetail={(t) => <BinanceTradeDetail t={t} />}
            emptyMessage="No real bot trades yet — the bot journals every accepted Binance order here"
          />
        </Card>
      )}
    </div>
  );
}
