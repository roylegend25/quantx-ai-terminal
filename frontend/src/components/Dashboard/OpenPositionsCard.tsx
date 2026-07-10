import { memo, useState } from "react";
import { ChevronDown, ChevronRight, Pencil } from "lucide-react";
import { fmtNum, fmtPct, fmtUsd, toneClass, toneOf } from "../../lib/format";
import type { Position } from "../../lib/portfolioStats";
import type { NavKey } from "../../lib/nav";
import { useMediaQuery } from "../../hooks/useMediaQuery";

type Props = {
  positions: Position[];
  onClose: (id: number) => void;
  onEditRisk: (position: Position) => void;
  onViewAll: (key: NavKey) => void;
  compact?: boolean;
};

const dash = "—";

function leverageOf(p: Position): string {
  return p.leverage != null ? `${p.leverage}x` : dash;
}

function liqOf(p: Position): string {
  if (p.liquidation_price != null) return fmtNum(p.liquidation_price);
  if (p.leverage === 1) return "None at 1x";
  return dash;
}

function pnlText(p: Position): string {
  const pct = typeof p.unrealized_pnl_pct === "number" ? ` (${fmtPct(p.unrealized_pnl_pct)})` : "";
  return `${fmtUsd(p.pnl)}${pct}`;
}

function DesktopTable({ positions, onClose, onEditRisk }: { positions: Position[]; onClose: (id: number) => void; onEditRisk: (p: Position) => void }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Size</th>
            <th>Entry</th>
            <th>Mark</th>
            <th>Lev</th>
            <th>Margin Used</th>
            <th>TP</th>
            <th>SL</th>
            <th>Est. Liq.</th>
            <th>Unrealized PnL</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.id}>
              <td>
                <b>{p.symbol}</b>
              </td>
              <td>
                <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span>
              </td>
              <td>{p.qty}</td>
              <td>{fmtNum(p.entry)}</td>
              <td>{fmtNum(p.mark)}</td>
              <td>{leverageOf(p)}</td>
              <td>{p.margin_used != null ? fmtUsd(p.margin_used) : dash}</td>
              <td>
                <span className={p.tp != null ? "green" : ""}>{p.tp != null ? fmtNum(p.tp) : dash}</span>
              </td>
              <td>
                <span className={p.sl != null ? "red" : ""}>{p.sl != null ? fmtNum(p.sl) : dash}</span>
              </td>
              <td>
                <span className={p.liquidation_price != null ? "orange" : ""} title="Estimated liquidation price (paper model)">
                  {liqOf(p)}
                </span>
              </td>
              <td>
                <span className={toneClass(toneOf(p.pnl))}>{pnlText(p)}</span>
              </td>
              <td>
                <div className="pos-actions">
                  <button className="mini-btn mini-btn-edit" onClick={() => onEditRisk(p)}>
                    <Pencil size={11} /> Edit Risk
                  </button>
                  <button className="mini-btn" onClick={() => onClose(p.id)}>
                    Close
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MobileCards({ positions, onClose, onEditRisk }: { positions: Position[]; onClose: (id: number) => void; onEditRisk: (p: Position) => void }) {
  const [expanded, setExpanded] = useState<number | null>(null);

  return (
    <div className="pos-cards">
      {positions.map((p) => {
        const open = expanded === p.id;
        return (
          <div key={p.id} className={`pos-card${open ? " pos-card-open" : ""}`}>
            <button className="pos-card-head" onClick={() => setExpanded(open ? null : p.id)}>
              <span className="pos-card-chevron">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
              <b>{p.symbol}</b>
              <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span>
              {p.leverage != null && <span className="lev-badge">{p.leverage}x</span>}
              <span className={`pos-card-pnl ${toneClass(toneOf(p.pnl))}`}>{pnlText(p)}</span>
            </button>

            <div className="pos-card-grid">
              <div>
                <span className="tile-label">Size</span>
                <b className="tile-value">{p.qty}</b>
              </div>
              <div>
                <span className="tile-label">Entry</span>
                <b className="tile-value">{fmtNum(p.entry)}</b>
              </div>
              <div>
                <span className="tile-label">Mark</span>
                <b className="tile-value">{fmtNum(p.mark)}</b>
              </div>
            </div>

            {open && (
              <div className="pos-card-grid">
                <div>
                  <span className="tile-label">Margin Used</span>
                  <b className="tile-value">{p.margin_used != null ? fmtUsd(p.margin_used) : dash}</b>
                </div>
                <div>
                  <span className="tile-label">Notional</span>
                  <b className="tile-value">{p.notional_value != null ? fmtUsd(p.notional_value) : dash}</b>
                </div>
                <div>
                  <span className="tile-label">Leverage</span>
                  <b className="tile-value">{leverageOf(p)}</b>
                </div>
                <div>
                  <span className="tile-label">Take Profit</span>
                  <b className="tile-value green">{p.tp != null ? fmtNum(p.tp) : dash}</b>
                </div>
                <div>
                  <span className="tile-label">Stop Loss</span>
                  <b className="tile-value red">{p.sl != null ? fmtNum(p.sl) : dash}</b>
                </div>
                <div>
                  <span className="tile-label">Est. Liquidation</span>
                  <b className="tile-value orange">{liqOf(p)}</b>
                </div>
                <div>
                  <span className="tile-label">Liq. Distance</span>
                  <b className="tile-value">
                    {p.distance_to_liquidation_pct != null ? fmtPct(p.distance_to_liquidation_pct) : dash}
                  </b>
                </div>
                <div>
                  <span className="tile-label">Risk / Reward</span>
                  <b className="tile-value">{p.risk_reward_ratio != null ? `1 : ${p.risk_reward_ratio.toFixed(2)}` : dash}</b>
                </div>
                <div>
                  <span className="tile-label">Trailing Stop</span>
                  <b className="tile-value">{p.trailing_stop != null ? fmtNum(p.trailing_stop) : dash}</b>
                </div>
              </div>
            )}

            <div className="pos-actions">
              <button className="mini-btn mini-btn-edit" onClick={() => onEditRisk(p)}>
                <Pencil size={11} /> Edit Risk
              </button>
              <button className="mini-btn" onClick={() => onClose(p.id)}>
                Close
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function OpenPositionsCard({ positions, onClose, onEditRisk, onViewAll, compact = true }: Props) {
  const isMobile = useMediaQuery("(max-width: 767px)");
  const rows = compact ? positions.slice(0, 5) : positions;

  return (
    <div className="pos-list">
      {rows.length === 0 && <p className="analytics-empty">No open positions.</p>}

      {rows.length > 0 &&
        (isMobile ? (
          <MobileCards positions={rows} onClose={onClose} onEditRisk={onEditRisk} />
        ) : (
          <DesktopTable positions={rows} onClose={onClose} onEditRisk={onEditRisk} />
        ))}

      {compact && positions.length > 0 && (
        <button className="link-btn" onClick={() => onViewAll("positions")}>
          View All Positions →
        </button>
      )}
    </div>
  );
}

export default memo(OpenPositionsCard);
