import { useState } from "react";
import Card from "../components/Layout/Card";
import EditRiskModal from "../components/Dashboard/EditRiskModal";
import type { Position } from "../lib/portfolioStats";
import { fmtLocalDateTime, fmtNum, fmtPct, fmtUsd, toneClass, toneOf } from "../lib/format";
import type { AppData } from "../hooks/useAppData";

const RESET_CONFIRM_TEXT =
  "This will delete all paper trades, close paper positions, and reset balance to $10,000. This does NOT affect live funds.";

/** Paper Trading Terminal (Phase 23): the simulated account only - nothing
 *  here reads or writes the Binance Real account. */
export default function PaperTradingPage(props: AppData) {
  const { portfolio, positions, history, symbol } = props;
  const [leverage, setLeverage] = useState(1);
  const [editingPosition, setEditingPosition] = useState<Position | null>(null);
  const [showResetModal, setShowResetModal] = useState(false);
  const [resetting, setResetting] = useState(false);

  const handleConfirmReset = async () => {
    setResetting(true);
    try {
      await props.resetPaperTrading();
    } finally {
      setResetting(false);
      setShowResetModal(false);
    }
  };

  return (
    <div className="page-grid">
      <Card
        full
        title="Paper Trading Terminal"
        right={<span className="badge">PAPER — SIMULATED FUNDS</span>}
      >
        <div className="portfolio-header-row">
          <p className="regime-desc">
            Fully simulated account. Orders, fills, TP/SL and liquidations are modeled against real market data —
            no real funds are ever touched. Paper TP/SL levels are enforced internally on every mark-price read.
          </p>
          <div className="controls">
            <label className="filter-select-wrap">
              <span className="tile-label">Leverage</span>
              <select className="filter-select" value={leverage} onChange={(e) => setLeverage(Number(e.target.value))}>
                {[1, 2, 3, 5, 10, 20].map((l) => (
                  <option key={l} value={l}>
                    {l}x
                  </option>
                ))}
              </select>
            </label>
            <button className="btn-long" onClick={() => props.openPaperTrade("LONG", leverage)}>
              Open Long ({symbol})
            </button>
            <button className="btn-short" onClick={() => props.openPaperTrade("SHORT", leverage)}>
              Open Short
            </button>
            <button className="btn-danger" onClick={() => setShowResetModal(true)}>
              Reset Paper Account
            </button>
          </div>
        </div>
      </Card>

      <Card title="Paper Account Overview">
        <div className="kv-grid">
          <div>
            <span className="tile-label">Balance</span>
            <b className="tile-value">{fmtUsd(portfolio?.balance)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Equity</span>
            <b className="tile-value">{fmtUsd(portfolio?.equity)}</b>
          </div>
          <div>
            <span className="tile-label">Available Margin</span>
            <b className="tile-value">{fmtUsd(portfolio?.available_margin)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Margin Used</span>
            <b className="tile-value">{fmtUsd(portfolio?.total_margin_used)}</b>
          </div>
          <div>
            <span className="tile-label">Unrealized PnL</span>
            <b className={`tile-value ${toneClass(toneOf(portfolio?.unrealized_pnl))}`}>
              {fmtUsd(portfolio?.unrealized_pnl)}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">Daily PnL</span>
            <b className={`tile-value ${toneClass(toneOf(portfolio?.daily_pnl))}`}>{fmtUsd(portfolio?.daily_pnl)}</b>
          </div>
          <div>
            <span className="tile-label">Total PnL</span>
            <b className={`tile-value ${toneClass(toneOf(portfolio?.total_pnl))}`}>{fmtUsd(portfolio?.total_pnl)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Win Rate</span>
            <b className="tile-value">{portfolio?.win_rate != null ? fmtPct(portfolio.win_rate) : "—"}</b>
          </div>
        </div>
      </Card>

      <Card title="Paper Risk" >
        <div className="kv-grid">
          <div>
            <span className="tile-label">Open Positions</span>
            <b className="tile-value">
              {positions.length}
              {portfolio?.max_open_positions != null ? ` of ${portfolio.max_open_positions}` : ""}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">Notional Exposure</span>
            <b className="tile-value">{fmtUsd(portfolio?.total_notional_exposure)}</b>
          </div>
          <div>
            <span className="tile-label">Margin Usage</span>
            <b className="tile-value">
              {portfolio?.margin_usage_pct != null ? fmtPct(portfolio.margin_usage_pct) : "—"}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">Nearest Liq. Distance</span>
            <b className="tile-value orange">
              {portfolio?.nearest_liquidation_distance_pct != null
                ? fmtPct(portfolio.nearest_liquidation_distance_pct)
                : "—"}
            </b>
          </div>
          <div>
            <span className="tile-label">Wins</span>
            <b className="tile-value green">{portfolio?.wins ?? "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Losses</span>
            <b className="tile-value red">{portfolio?.losses ?? "—"}</b>
          </div>
        </div>
      </Card>

      <Card title="Paper Open Orders">
        <p className="regime-desc">Paper orders fill instantly against the live mark — nothing rests on a book.</p>
      </Card>

      <Card title={`Paper Open Positions (${positions.length})`} full>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Size</th>
                <th>Entry</th>
                <th>Mark</th>
                <th>Est. Liq.</th>
                <th>Margin</th>
                <th>Lev.</th>
                <th>uPnL</th>
                <th>TP</th>
                <th>SL</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {positions.length === 0 && (
                <tr>
                  <td colSpan={12}>No open paper positions</td>
                </tr>
              )}
              {positions.map((p: any) => (
                <tr key={p.id}>
                  <td><b>{p.symbol}</b></td>
                  <td><span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span></td>
                  <td>{fmtNum(p.qty, 6)}</td>
                  <td>{fmtNum(p.entry)}</td>
                  <td>{fmtNum(p.mark)}</td>
                  <td>{p.liquidation_price != null ? fmtNum(p.liquidation_price) : "—"}</td>
                  <td>{fmtUsd(p.margin_used)}</td>
                  <td>{p.leverage != null ? `${p.leverage}x` : "—"}</td>
                  <td><span className={toneClass(toneOf(p.pnl))}>{fmtUsd(p.pnl)}</span></td>
                  <td>{p.tp != null ? fmtNum(p.tp) : "—"}</td>
                  <td>{p.sl != null ? fmtNum(p.sl) : "—"}</td>
                  <td>
                    <div className="controls" style={{ gap: 6 }}>
                      <button className="mini-btn" title="Edit Paper TP/SL" onClick={() => setEditingPosition(p)}>
                        TP/SL
                      </button>
                      <button className="btn-danger mini" onClick={() => props.closePaperTrade(p.id)}>
                        Close
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Paper Trade History" full>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Entry → Exit</th>
                <th>Qty</th>
                <th>Strategy / Model</th>
                <th>Close Reason</th>
                <th>Realized PnL</th>
              </tr>
            </thead>
            <tbody>
              {history.length === 0 && (
                <tr>
                  <td colSpan={8}>No paper trades yet</td>
                </tr>
              )}
              {history.slice(0, 30).map((t: any) => (
                <tr key={t.id}>
                  <td>{fmtLocalDateTime(t.closed_at ?? t.opened_at)}</td>
                  <td><b>{t.symbol}</b></td>
                  <td><span className={t.side === "LONG" ? "green" : "red"}>{t.side}</span></td>
                  <td>
                    {fmtNum(t.entry)} → {t.exit != null ? fmtNum(t.exit) : "open"}
                  </td>
                  <td>{fmtNum(t.qty, 6)}</td>
                  <td>{t.strategy_used || t.champion_model_type || t.decision_mode || "—"}</td>
                  <td>{t.close_reason || "—"}</td>
                  <td><span className={toneClass(toneOf(t.pnl))}>{fmtUsd(t.pnl)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {editingPosition && (
        <EditRiskModal
          position={editingPosition}
          onSave={props.updatePositionRisk}
          onClose={() => setEditingPosition(null)}
        />
      )}

      {showResetModal && (
        <div className="modal-overlay" onClick={() => !resetting && setShowResetModal(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>Reset paper trading?</h3>
            <p className="regime-desc">{RESET_CONFIRM_TEXT}</p>
            <div className="modal-actions">
              <button className="mini-btn" disabled={resetting} onClick={() => setShowResetModal(false)}>
                Cancel
              </button>
              <button className="btn-danger" disabled={resetting} onClick={handleConfirmReset}>
                {resetting ? "Resetting…" : "Reset Paper Account"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
