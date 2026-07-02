import { fmtPct, fmtUsd, toneClass, toneOf } from "../../lib/format";
import { marginUsagePct, type Portfolio, type Position } from "../../lib/portfolioStats";

type Props = {
  portfolio: Portfolio | null;
  positions: Position[];
};

export default function AccountOverviewCard({ portfolio, positions }: Props) {
  const margin = marginUsagePct(positions, portfolio);
  const available = (portfolio?.equity ?? 0) - ((portfolio?.equity ?? 0) * margin) / 100;
  const dailyPnl = portfolio?.daily_pnl ?? 0;
  const dailyPct = portfolio?.balance ? (dailyPnl / portfolio.balance) * 100 : 0;

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
      </div>

      <div className="strength-row">
        <span className="tile-label">Margin Usage</span>
        <b>{fmtPct(margin, 1)}</b>
      </div>
      <progress value={margin} max={100} />
    </div>
  );
}
