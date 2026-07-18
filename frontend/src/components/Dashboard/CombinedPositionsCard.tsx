import { memo } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { fmtNum, fmtUsd, toneClass, toneOf } from "../../lib/format";
import type { Position } from "../../lib/portfolioStats";

type BinanceRow = {
  symbol: string;
  side: "LONG" | "SHORT" | string;
  quantity: number;
  entry_price: number | null;
  mark_price: number | null;
  unrealized_pnl: number | null;
};

type Row = {
  key: string;
  source: "PAPER" | "BINANCE REAL";
  symbol: string;
  side: string;
  qty: number;
  entry: number | null;
  mark: number | null;
  pnl: number | null;
};

type Props = {
  paperPositions: Position[];
  binancePositionRows: BinanceRow[];
  /** True when the Binance real read failed outright (not merely stale) -
   *  must never be silently rendered as "zero Binance positions", since
   *  that would misrepresent a fetch failure as an honest empty account. */
  binanceUnavailable?: boolean;
  binanceUnavailableReason?: string | null;
  binanceStale?: boolean;
};

/** Phase 34: read-only combined monitoring view of paper + Binance Real
 *  open positions. Every row keeps an explicit source badge and the two
 *  totals below are always shown separately - paper and real balances are
 *  never summed into one figure anywhere in this component. Detailed
 *  per-source management (close/edit/protect) stays on OpenPositionsCard
 *  (paper) and BinancePositionsTable (Binance Real); this card is
 *  monitoring-only. */
function CombinedPositionsCard({ paperPositions, binancePositionRows, binanceUnavailable, binanceUnavailableReason, binanceStale }: Props) {
  const paperRows: Row[] = paperPositions.map((p) => ({
    key: `paper-${p.id}`, source: "PAPER", symbol: p.symbol, side: p.side, qty: p.qty, entry: p.entry, mark: p.mark, pnl: p.pnl,
  }));
  const binanceRows: Row[] = binanceUnavailable ? [] : binancePositionRows.map((p, i) => ({
    key: `binance-${p.symbol}-${i}`, source: "BINANCE REAL", symbol: p.symbol, side: p.side, qty: p.quantity,
    entry: p.entry_price, mark: p.mark_price, pnl: p.unrealized_pnl,
  }));
  const rows = [...paperRows, ...binanceRows];
  const paperTotal = paperRows.reduce((sum, r) => sum + (r.pnl ?? 0), 0);
  const binanceTotal = binanceRows.reduce((sum, r) => sum + (r.pnl ?? 0), 0);

  return (
    <div className="combined-positions">
      {binanceUnavailable && (
        <div className="regime-focus blocked" style={{ marginBottom: 12 }}>
          <span className="tile-label"><AlertTriangle size={13} /> Binance Real positions unavailable</span>
          <p className="regime-desc">{binanceUnavailableReason || "Could not reach the Binance Real account - this does not mean there are no open real positions."}</p>
        </div>
      )}
      {binanceStale && !binanceUnavailable && (
        <div className="regime-focus" style={{ marginBottom: 12 }}>
          <span className="tile-label"><RefreshCw size={13} /> Binance Real positions are a cached snapshot (API cooling down)</span>
        </div>
      )}

      {rows.length === 0 ? (
        <p className="analytics-empty">No open positions - paper or Binance Real.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table combined-positions-table">
            <thead>
              <tr><th>Source</th><th>Symbol</th><th>Side</th><th>Size</th><th>Entry</th><th>Mark</th><th>Unrealized PnL</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td><span className={`chip source-badge ${r.source === "PAPER" ? "cyan" : "purple"}`}>{r.source}</span></td>
                  <td><b>{r.symbol}</b></td>
                  <td><span className={r.side === "LONG" ? "green" : "red"}>{r.side}</span></td>
                  <td>{r.qty}</td>
                  <td>{fmtNum(r.entry)}</td>
                  <td>{fmtNum(r.mark)}</td>
                  <td><span className={toneClass(toneOf(r.pnl))}>{fmtUsd(r.pnl)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="combined-positions-totals">
        <span><small>PAPER unrealized PnL</small><b className={toneClass(toneOf(paperTotal))}>{fmtUsd(paperTotal)}</b></span>
        <span>
          <small>BINANCE REAL unrealized PnL</small>
          <b className={binanceUnavailable ? "yellow" : toneClass(toneOf(binanceTotal))}>
            {binanceUnavailable ? "Unavailable" : fmtUsd(binanceTotal)}
          </b>
        </span>
      </div>
    </div>
  );
}

export default memo(CombinedPositionsCard);
