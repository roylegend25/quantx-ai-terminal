import { useMemo, useState } from "react";
import { FileDown, ListOrdered, Search } from "lucide-react";
import Card from "../Layout/Card";
import AutoCardTable, { type AutoCardColumn } from "../Responsive/AutoCardTable";
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
          <AutoCardTable
            columns={
              [
                {
                  key: "date",
                  label: "Date",
                  render: (t) => {
                    const d = t.entry_time ? new Date(t.entry_time) : null;
                    return d ? d.toLocaleDateString([], { month: "short", day: "numeric", year: "2-digit" }) : "—";
                  },
                },
                {
                  key: "time",
                  label: "Time",
                  render: (t) => {
                    const d = t.entry_time ? new Date(t.entry_time) : null;
                    return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
                  },
                },
                { key: "symbol", label: "Symbol", render: () => <b>{symbol}</b> },
                { key: "direction", label: "Direction", render: (t) => <span className={t.side === "LONG" ? "green" : "red"}>{t.side}</span> },
                { key: "entry", label: "Entry", render: (t) => fmtNum(t.entry_price, 2) },
                { key: "exit", label: "Exit", render: (t) => fmtNum(t.exit_price, 2) },
                { key: "pnl", label: "PnL", render: (t) => <span className={(Number(t.pnl) || 0) >= 0 ? "green" : "red"}>{fmtNum(t.pnl, 2)}</span> },
                {
                  key: "return",
                  label: "Return %",
                  render: (t) => {
                    const ret = tradeReturnPct(t);
                    return <span className={ret != null && ret >= 0 ? "green" : "red"}>{fmtPct(ret, 2)}</span>;
                  },
                },
                { key: "r", label: "R", render: (t) => fmtNum(t.r_multiple, 2) },
                { key: "holding", label: "Holding", render: (t) => fmtHolding(t.entry_time, t.exit_time) },
                { key: "confidence", label: "Confidence", render: (t) => (t.confidence != null ? fmtNum(t.confidence, 0) : "—") },
                { key: "regime", label: "Regime", render: (t) => (t.regime || "—").replace(/_/g, " ") },
                { key: "reason", label: "Reason", render: (t) => t.exit_reason.replace(/_/g, " ") },
                { key: "strategy", label: "Strategy", render: () => strategy },
              ] as AutoCardColumn<BtTrade>[]
            }
            rows={pageRows}
            keyField={(t) => `${t.entry_time}-${t.exit_time}-${t.entry_price}`}
            titleColumn="symbol"
            statusColumn="direction"
          />
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
