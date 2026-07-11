import { useState } from "react";
import Card from "../components/Layout/Card";
import EditRiskModal from "../components/Dashboard/EditRiskModal";
import AutoCardTable from "../components/Responsive/AutoCardTable";
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
        <AutoCardTable
          columns={[
            { key: "symbol", label: "Symbol", render: (p: any) => <b>{p.symbol}</b> },
            { key: "side", label: "Side", render: (p: any) => <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span> },
            { key: "size", label: "Size", render: (p: any) => fmtNum(p.qty, 6) },
            { key: "entry", label: "Entry", render: (p: any) => fmtNum(p.entry) },
            { key: "mark", label: "Mark", render: (p: any) => fmtNum(p.mark) },
            { key: "liq", label: "Est. Liq.", render: (p: any) => (p.liquidation_price != null ? fmtNum(p.liquidation_price) : "—") },
            { key: "margin", label: "Margin", render: (p: any) => fmtUsd(p.margin_used) },
            { key: "lev", label: "Lev.", render: (p: any) => (p.leverage != null ? `${p.leverage}x` : "—") },
            { key: "pnl", label: "uPnL", render: (p: any) => <span className={toneClass(toneOf(p.pnl))}>{fmtUsd(p.pnl)}</span> },
            { key: "tp", label: "TP", render: (p: any) => (p.tp != null ? fmtNum(p.tp) : "—") },
            { key: "sl", label: "SL", render: (p: any) => (p.sl != null ? fmtNum(p.sl) : "—") },
          ]}
          rows={positions}
          keyField={(p: any) => p.id}
          titleColumn="symbol"
          statusColumn="side"
          renderActions={(p: any) => (
            <>
              <button className="mini-btn" title="Edit Paper TP/SL" onClick={() => setEditingPosition(p)}>
                TP/SL
              </button>
              <button className="btn-danger mini" onClick={() => props.closePaperTrade(p.id)}>
                Close
              </button>
            </>
          )}
          emptyMessage="No open paper positions"
        />
      </Card>

      <Card title="Paper Trade History" full>
        <AutoCardTable
          columns={[
            { key: "time", label: "Time", render: (t: any) => fmtLocalDateTime(t.closed_at ?? t.opened_at) },
            { key: "symbol", label: "Symbol", render: (t: any) => <b>{t.symbol}</b> },
            { key: "side", label: "Side", render: (t: any) => <span className={t.side === "LONG" ? "green" : "red"}>{t.side}</span> },
            { key: "entryExit", label: "Entry → Exit", render: (t: any) => `${fmtNum(t.entry)} → ${t.exit != null ? fmtNum(t.exit) : "open"}` },
            { key: "qty", label: "Qty", render: (t: any) => fmtNum(t.qty, 6) },
            { key: "strategy", label: "Strategy / Model", render: (t: any) => t.strategy_used || t.champion_model_type || t.decision_mode || "—" },
            { key: "closeReason", label: "Close Reason", render: (t: any) => t.close_reason || "—" },
            { key: "pnl", label: "Realized PnL", render: (t: any) => <span className={toneClass(toneOf(t.pnl))}>{fmtUsd(t.pnl)}</span> },
          ]}
          rows={history.slice(0, 30)}
          keyField={(t: any) => t.id}
          titleColumn="symbol"
          statusColumn="side"
          emptyMessage="No paper trades yet"
        />
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
