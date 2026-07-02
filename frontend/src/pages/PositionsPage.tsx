import Card from "../components/Layout/Card";
import OpenPositionsCard from "../components/Dashboard/OpenPositionsCard";
import { fmtUsd, toneClass, toneOf } from "../lib/format";
import type { AppData } from "../hooks/useAppData";
import type { NavKey } from "../lib/nav";

type Props = AppData & { navigate: (key: NavKey) => void };

export default function PositionsPage(props: Props) {
  const { positions, history, symbol } = props;

  return (
    <div className="page-grid">
      <Card title="Open a Paper Trade" wide>
        <p className="regime-desc">
          Opens a simulated {symbol} position using the paper trading engine ($1,000 notional).
        </p>
        <div className="controls" style={{ marginTop: 14 }}>
          <button className="btn-long" onClick={() => props.openPaperTrade("LONG")}>
            Open Long
          </button>
          <button className="btn-short" onClick={() => props.openPaperTrade("SHORT")}>
            Open Short
          </button>
        </div>
      </Card>

      <Card title="Positions Summary">
        <div className="kv-grid">
          <div>
            <span className="tile-label">Open Positions</span>
            <b className="tile-value">{positions.length}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Closed Trades</span>
            <b className="tile-value">{history.filter((t) => t.status === "CLOSED").length}</b>
          </div>
        </div>
      </Card>

      <Card title="Open Positions" full>
        <OpenPositionsCard
          positions={positions}
          onClose={props.closePaperTrade}
          onViewAll={props.navigate}
          compact={false}
        />
      </Card>

      <Card title="Trade Journal" full>
        {history.length === 0 && <p className="analytics-empty">No trade history yet.</p>}
        {history.length > 0 && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Status</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>PnL</th>
                </tr>
              </thead>
              <tbody>
                {history.slice(0, 30).map((t) => (
                  <tr key={t.id}>
                    <td>
                      <b>{t.symbol}</b>
                    </td>
                    <td className={t.side === "LONG" ? "green" : "red"}>{t.side}</td>
                    <td>{t.status}</td>
                    <td>{t.entry ?? "—"}</td>
                    <td>{t.exit ?? "—"}</td>
                    <td className={toneClass(toneOf(t.pnl))}>{fmtUsd(t.pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
