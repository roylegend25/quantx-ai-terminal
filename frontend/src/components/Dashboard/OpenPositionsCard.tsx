import { memo } from "react";
import { fmtUsd, toneClass, toneOf } from "../../lib/format";
import type { Position } from "../../lib/portfolioStats";
import type { NavKey } from "../../lib/nav";

type Props = {
  positions: Position[];
  onClose: (id: number) => void;
  onViewAll: (key: NavKey) => void;
  compact?: boolean;
};

function OpenPositionsCard({ positions, onClose, onViewAll, compact = true }: Props) {
  const rows = compact ? positions.slice(0, 5) : positions;

  return (
    <div>
      {rows.length === 0 && <p className="analytics-empty">No open positions.</p>}

      {rows.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Size</th>
                <th>Entry Price</th>
                <th>Mark Price</th>
                <th>PnL</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td>
                    <b>{p.symbol}</b>
                  </td>
                  <td className={p.side === "LONG" ? "green" : "red"}>{p.side}</td>
                  <td>{p.qty}</td>
                  <td>{p.entry}</td>
                  <td>{p.mark}</td>
                  <td className={toneClass(toneOf(p.pnl))}>{fmtUsd(p.pnl)}</td>
                  <td>
                    <button className="mini-btn" onClick={() => onClose(p.id)}>
                      Close
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {compact && positions.length > 0 && (
        <button className="link-btn" onClick={() => onViewAll("positions")}>
          View All Positions →
        </button>
      )}
    </div>
  );
}

export default memo(OpenPositionsCard);
