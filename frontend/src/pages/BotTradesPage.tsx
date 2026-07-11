import { useCallback, useEffect, useState } from "react";
import Card from "../components/Layout/Card";
import { api } from "../services/api";
import { fmtLocalDateTime, fmtNum, fmtUsd, toneClass, toneOf } from "../lib/format";

const POLL_MS = 15000;

const ENGINE_LABEL: Record<string, string> = {
  champion_ml: "Champion ML",
  strategy_ensemble: "Strategy Ensemble",
  fallback: "Fallback",
  manual: "Manual",
};

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
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Label</th>
                  <th>Opened / Closed</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Entry → Exit</th>
                  <th>TP / SL</th>
                  <th>Est. Liq.</th>
                  <th>Margin</th>
                  <th>Lev.</th>
                  <th>Engine</th>
                  <th>Conf.</th>
                  <th>Reason</th>
                  <th>PnL</th>
                </tr>
              </thead>
              <tbody>
                {paper.length === 0 && (
                  <tr>
                    <td colSpan={14}>No paper bot trades yet</td>
                  </tr>
                )}
                {paper.slice(0, 50).map((t: any) => (
                  <tr key={t.id}>
                    <td>#{t.id}</td>
                    <td>
                      <span className={`badge ${t.label === "BOT_TRADE" ? "badge-green" : ""}`} style={{ fontSize: 10 }}>
                        {t.label === "BOT_TRADE" ? "BOT" : "MANUAL"}
                      </span>
                    </td>
                    <td>
                      {fmtLocalDateTime(t.opened_at)}
                      {t.closed_at ? ` → ${fmtLocalDateTime(t.closed_at)}` : ""}
                    </td>
                    <td><b>{t.symbol}</b></td>
                    <td><span className={t.side === "LONG" ? "green" : "red"}>{t.side}</span></td>
                    <td>
                      {fmtNum(t.entry)} → {t.exit != null ? fmtNum(t.exit) : "open"}
                    </td>
                    <td>
                      {t.tp != null ? fmtNum(t.tp) : "—"} / {t.sl != null ? fmtNum(t.sl) : "—"}
                    </td>
                    <td>{t.liquidation_price != null ? fmtNum(t.liquidation_price) : "—"}</td>
                    <td>{fmtUsd(t.margin_used)}</td>
                    <td>{t.leverage != null ? `${t.leverage}x` : "—"}</td>
                    <td>{ENGINE_LABEL[t.decision_mode] || t.strategy_used || t.decision_mode || "—"}</td>
                    <td>{t.confidence != null ? `${fmtNum(t.confidence, 1)}%` : "—"}</td>
                    <td className="bot-trade-reason">
                      {Array.isArray(t.decision_reasons) ? t.decision_reasons[0] : t.risk_reason || "—"}
                    </td>
                    <td><span className={toneClass(toneOf(t.realized_pnl))}>{fmtUsd(t.realized_pnl)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : (
        <Card title="Binance Real Bot Trades" full className="live-danger-card">
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Label</th>
                  <th>Action</th>
                  <th>Order ID</th>
                  <th>Client Order ID</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Type</th>
                  <th>Qty</th>
                  <th>Avg Fill</th>
                  <th>Status</th>
                  <th>Reduce Only</th>
                  <th>TP/SL Orders</th>
                  <th>Engine</th>
                  <th>Conf.</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {binance.length === 0 && (
                  <tr>
                    <td colSpan={16}>No real bot trades yet — the bot journals every accepted Binance order here</td>
                  </tr>
                )}
                {binance.slice(0, 50).map((t: any) => (
                  <tr key={t.id}>
                    <td>{fmtLocalDateTime(t.created_at)}</td>
                    <td>
                      <span className="badge badge-green" style={{ fontSize: 10 }}>BOT</span>
                    </td>
                    <td>{t.action?.toUpperCase()}</td>
                    <td>{t.order_id}</td>
                    <td style={{ fontSize: 11 }}>{t.client_order_id}</td>
                    <td><b>{t.symbol}</b></td>
                    <td><span className={t.side === "LONG" ? "green" : "red"}>{t.side}</span></td>
                    <td>{t.order_type}</td>
                    <td>{fmtNum(t.quantity, 6)}</td>
                    <td>{t.avg_fill_price != null ? fmtNum(t.avg_fill_price) : "—"}</td>
                    <td>{t.status}</td>
                    <td>{t.reduce_only ? "Yes" : "No"}</td>
                    <td style={{ fontSize: 11 }}>
                      {t.tp_order_id ?? "—"} / {t.sl_order_id ?? "—"}
                    </td>
                    <td>{t.model || t.strategy || "—"}</td>
                    <td>{t.confidence != null ? `${fmtNum(t.confidence, 1)}%` : "—"}</td>
                    <td className="bot-trade-reason">{t.decision_reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
