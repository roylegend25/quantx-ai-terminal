import { useMemo, useState } from "react";
import { FileDown, ListOrdered, Search } from "lucide-react";
import Card from "../Layout/Card";
import { fmtNum, fmtPct } from "../../lib/format";
import {
  downloadBlob,
  fmtHolding,
  tradeReturnPct,
  tradesToCsv,
  type BtTrade,
} from "../../lib/backtestStats";

const PAGE_SIZE = 25;

const RESULT_FILTERS = [
  { key: "all", label: "All" },
  { key: "wins", label: "Wins" },
  { key: "losses", label: "Losses" },
  { key: "stop_loss", label: "Stopped" },
  { key: "take_profit", label: "Targets" },
];

type Props = {
  trades: BtTrade[];
  symbol: string;
  strategy: string;
  runId: string | null;
  directionNote: string | null;
};

export default function TradeHistoryTable({ trades, symbol, strategy, runId, directionNote }: Props) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    let rows = trades;
    if (filter === "wins") rows = rows.filter((t) => (Number(t.pnl) || 0) >= 0);
    else if (filter === "losses") rows = rows.filter((t) => (Number(t.pnl) || 0) < 0);
    else if (filter === "stop_loss" || filter === "take_profit") rows = rows.filter((t) => t.exit_reason === filter);
    const q = query.trim().toLowerCase();
    if (q) {
      rows = rows.filter((t) =>
        [t.side, t.exit_reason, t.regime, t.entry_time, t.exit_time]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(q))
      );
    }
    return rows;
  }, [trades, filter, query]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageRows = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const setFilterAndReset = (key: string) => {
    setFilter(key);
    setPage(0);
  };

  return (
    <Card title="Trade History" full right={<ListOrdered size={16} />}>
      <div className="bt-table-toolbar">
        <div className="bt-search">
          <Search size={14} />
          <input
            placeholder="Search side, reason, regime…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
          />
        </div>
        <div className="bt-filter-row">
          {RESULT_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`bt-chip ${filter === f.key ? "active" : ""}`}
              onClick={() => setFilterAndReset(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="bt-chip bt-export"
          disabled={!filtered.length}
          onClick={() =>
            downloadBlob(
              `trades_${runId || "run"}_${symbol}.csv`,
              tradesToCsv(filtered, symbol, strategy),
              "text/csv"
            )
          }
        >
          <FileDown size={13} /> Export CSV
        </button>
      </div>

      {directionNote && <p className="bt-note">{directionNote}</p>}

      {!trades.length ? (
        <p className="analytics-empty">No trades yet — run a backtest to populate the trade log.</p>
      ) : !filtered.length ? (
        <p className="analytics-empty">No trades match the current search/filter.</p>
      ) : (
        <>
          <div className="table-wrap">
            <table className="data-table bt-trades-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Direction</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>PnL</th>
                  <th>Return %</th>
                  <th>R</th>
                  <th>Holding</th>
                  <th>Confidence</th>
                  <th>Regime</th>
                  <th>Reason</th>
                  <th>Strategy</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((t, i) => {
                  const d = t.entry_time ? new Date(t.entry_time) : null;
                  const ret = tradeReturnPct(t);
                  const win = (Number(t.pnl) || 0) >= 0;
                  return (
                    <tr key={`${t.entry_time}-${i}`}>
                      <td>{d ? d.toLocaleDateString([], { month: "short", day: "numeric", year: "2-digit" }) : "—"}</td>
                      <td>{d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}</td>
                      <td>{symbol}</td>
                      <td><span className={t.side === "LONG" ? "green" : "red"}>{t.side}</span></td>
                      <td>{fmtNum(t.entry_price, 2)}</td>
                      <td>{fmtNum(t.exit_price, 2)}</td>
                      <td><span className={win ? "green" : "red"}>{fmtNum(t.pnl, 2)}</span></td>
                      <td><span className={ret != null && ret >= 0 ? "green" : "red"}>{fmtPct(ret, 2)}</span></td>
                      <td>{fmtNum(t.r_multiple, 2)}</td>
                      <td>{fmtHolding(t.entry_time, t.exit_time)}</td>
                      <td>{t.confidence != null ? fmtNum(t.confidence, 0) : "—"}</td>
                      <td className="bt-regime-cell">{(t.regime || "—").replace(/_/g, " ")}</td>
                      <td className="bt-regime-cell">{t.exit_reason.replace(/_/g, " ")}</td>
                      <td className="bt-regime-cell">{strategy}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <div className="bt-pager">
              <button type="button" className="bt-chip" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                ‹ Prev
              </button>
              <span className="tile-label" style={{ marginBottom: 0 }}>
                Page {page + 1} / {pages} · {filtered.length} trades
              </span>
              <button
                type="button"
                className="bt-chip"
                disabled={page >= pages - 1}
                onClick={() => setPage((p) => p + 1)}
              >
                Next ›
              </button>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
