import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { fmtPct } from "../../lib/format";
import {
  equityCurveOf,
  initialBalanceOf,
  winLossStats,
  type Portfolio,
  type Trade,
} from "../../lib/portfolioStats";

type Props = {
  portfolio: Portfolio | null;
  history: Trade[];
};

export default function PerformanceMiniCard({ portfolio, history }: Props) {
  const equityCurve = equityCurveOf(portfolio, history);
  const initial = initialBalanceOf(portfolio);
  const current = portfolio?.equity ?? initial;
  const returnPct = initial > 0 ? ((current - initial) / initial) * 100 : 0;
  const stats = winLossStats(history, portfolio);

  return (
    <div>
      <div className="perf-badge-row">
        <span className={`badge ${returnPct >= 0 ? "badge-green" : "badge-red"}`}>
          {returnPct >= 0 ? "+" : ""}
          {returnPct.toFixed(1)}%
        </span>
      </div>

      <ResponsiveContainer width="100%" height={130}>
        <AreaChart data={equityCurve}>
          <defs>
            <linearGradient id="perfMini" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#7c5cff" stopOpacity={0.55} />
              <stop offset="95%" stopColor="#7c5cff" stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area type="monotone" dataKey="equity" stroke="#7c5cff" fill="url(#perfMini)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>

      <div className="kv-grid" style={{ marginTop: 14 }}>
        <div>
          <span className="tile-label">Win Rate</span>
          <b className="tile-value">{fmtPct(stats.winRate, 1)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Total Trades</span>
          <b className="tile-value">{stats.totalTrades}</b>
        </div>
        <div>
          <span className="tile-label">Profit Factor</span>
          <b className="tile-value">{Number.isFinite(stats.profitFactor) ? stats.profitFactor.toFixed(2) : "∞"}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Sharpe Ratio</span>
          <b className="tile-value">—</b>
        </div>
      </div>
    </div>
  );
}
