import { memo } from "react";
import { fmtPct, fmtUsd, toneClass, toneOf } from "../../lib/format";
import { marginUsagePct, type Portfolio, type Position } from "../../lib/portfolioStats";

type Props = {
  portfolio: Portfolio | null;
  positions: Position[];
};

function AccountOverviewCard({ portfolio, positions }: Props) {
  const margin = marginUsagePct(positions, portfolio);
  const available =
    typeof portfolio?.available_margin === "number"
      ? portfolio.available_margin
      : (portfolio?.equity ?? 0) - ((portfolio?.equity ?? 0) * margin) / 100;
  const dailyPnl = portfolio?.daily_pnl ?? 0;
  const dailyPct = portfolio?.balance ? (dailyPnl / portfolio.balance) * 100 : 0;
  const maxOpen = portfolio?.max_open_positions;
  const openCount = positions.length;
  const atLimit = typeof maxOpen === "number" && openCount >= maxOpen;

  return (
    <div className="stack-card">
      <div className="card-title">Account Overview</div>
      <div className="kv-grid">
        <div>
          <span className="tile-label">Balance</span>
          <b className="tile-value">{fmtUsd(portfolio?.balance)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Daily PnL</span>
          <b className={`tile-value ${toneClass(toneOf(dailyPnl))}`}>
            {fmtUsd(dailyPnl)} ({fmtPct(dailyPct)})
          </b>
        </div>
        <div>
          <span className="tile-label">Equity</span>
          <b className="tile-value">{fmtUsd(portfolio?.equity)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Available Margin</span>
          <b className="tile-value">{fmtUsd(available)}</b>
        </div>
        <div>
          <span className="tile-label">Margin Used</span>
          <b className="tile-value">{fmtUsd(portfolio?.total_margin_used)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Notional Exposure</span>
          <b className="tile-value">{fmtUsd(portfolio?.total_notional_exposure)}</b>
        </div>
        <div>
          <span className="tile-label">Open Positions</span>
          <b className={`tile-value ${atLimit ? "yellow" : ""}`}>
            {typeof maxOpen === "number" ? `${openCount} of ${maxOpen}` : openCount}
            {atLimit ? " (limit reached)" : ""}
          </b>
        </div>
        <div className="align-right">
          <span className="tile-label" title="Distance to the closest estimated liquidation among open positions">
            Nearest Est. Liq.
          </span>
          <b className="tile-value orange">
            {typeof portfolio?.nearest_liquidation_distance_pct === "number"
              ? fmtPct(portfolio.nearest_liquidation_distance_pct)
              : "—"}
          </b>
        </div>
      </div>

      <div className="strength-row">
        <span className="tile-label">Margin Usage</span>
        <b>{fmtPct(margin, 1)}</b>
      </div>
      <progress value={margin} max={100} />
    </div>
  );
}

export default memo(AccountOverviewCard);
