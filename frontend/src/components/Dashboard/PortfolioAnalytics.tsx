import { memo, useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtAxisNumber, fmtPct, fmtUsd, toneClass, toneOf } from "../../lib/format";
import {
  closedTradesOf,
  dailyPnlOf,
  drawdownCurveOf,
  equityCurveOf,
  initialBalanceOf,
  maxDrawdownOf,
  winLossStats,
  type Portfolio,
  type Position,
  type Trade,
} from "../../lib/portfolioStats";

type Props = {
  portfolio: Portfolio | null;
  positions: Position[];
  history: Trade[];
};

type Tile = {
  label: string;
  value: string;
  tone: ReturnType<typeof toneOf>;
};

function PortfolioAnalytics({ portfolio, positions, history }: Props) {
  const closedTrades = useMemo(() => closedTradesOf(history), [history]);
  const initialBalance = useMemo(() => initialBalanceOf(portfolio), [portfolio]);
  const equityCurve = useMemo(() => equityCurveOf(portfolio, history), [portfolio, history]);
  const drawdownCurve = useMemo(() => drawdownCurveOf(equityCurve), [equityCurve]);
  const maxDrawdown = useMemo(() => maxDrawdownOf(drawdownCurve), [drawdownCurve]);
  const dailyPnl = useMemo(() => dailyPnlOf(history), [history]);

  const { avgWin, avgLoss, profitFactor, expectancy, winRate: winRatePct } = useMemo(
    () => winLossStats(history, portfolio),
    [history, portfolio]
  );

  const realizedPnl = typeof portfolio?.total_pnl === "number" ? portfolio.total_pnl : 0;
  const totalReturnPct =
    initialBalance > 0
      ? (((typeof portfolio?.equity === "number" ? portfolio.equity : initialBalance) - initialBalance) /
          initialBalance) *
        100
      : 0;

  const sharpeRatio = typeof portfolio?.sharpe_ratio === "number" ? portfolio.sharpe_ratio : null;

  const tiles: Tile[] = [
    { label: "Current Equity", value: fmtUsd(portfolio?.equity ?? 0), tone: "neutral" },
    { label: "Cash Balance", value: fmtUsd(portfolio?.balance ?? 0), tone: "neutral" },
    { label: "Unrealized PnL", value: fmtUsd(portfolio?.unrealized_pnl ?? 0), tone: toneOf(portfolio?.unrealized_pnl ?? 0) },
    { label: "Realized PnL", value: fmtUsd(realizedPnl), tone: toneOf(realizedPnl) },
    { label: "Daily PnL", value: fmtUsd(portfolio?.daily_pnl ?? 0), tone: toneOf(portfolio?.daily_pnl ?? 0) },
    { label: "Total Return", value: fmtPct(totalReturnPct), tone: toneOf(totalReturnPct) },
    { label: "Win Rate", value: fmtPct(winRatePct), tone: "neutral" },
    { label: "Average Win", value: fmtUsd(avgWin), tone: "pos" },
    { label: "Average Loss", value: fmtUsd(-avgLoss), tone: "neg" },
    {
      label: "Profit Factor",
      value: Number.isFinite(profitFactor) ? profitFactor.toFixed(2) : "∞",
      tone: "neutral",
    },
    { label: "Expectancy", value: fmtUsd(expectancy), tone: toneOf(expectancy) },
    { label: "Max Drawdown", value: `-${Math.abs(maxDrawdown).toFixed(2)}%`, tone: "neg" },
    ...(sharpeRatio !== null
      ? [{ label: "Sharpe Ratio", value: sharpeRatio.toFixed(2), tone: toneOf(sharpeRatio) }]
      : []),
    { label: "Open Positions", value: String(positions?.length ?? 0), tone: "neutral" as const },
    { label: "Closed Trades", value: String(closedTrades.length), tone: "neutral" as const },
  ];

  return (
    <>
      <div className="analytics-grid">
        {tiles.map((t) => (
          <div className="analytics-tile" key={t.label}>
            <span className="tile-label">{t.label}</span>
            <b className={`tile-value ${toneClass(t.tone)}`}>
              {t.value}
            </b>
          </div>
        ))}
      </div>

      <div className="analytics-section">
        <div className="card-title">Equity Curve</div>
        {equityCurve.length > 1 ? (
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={equityCurve}>
              <defs>
                <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--c-cyan)" stopOpacity={0.5} />
                  <stop offset="95%" stopColor="var(--c-cyan)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="label" stroke="#8b90a8" fontSize={12} />
              <YAxis
                stroke="#8b90a8"
                fontSize={12}
                domain={["dataMin", "dataMax"]}
                tickFormatter={fmtAxisNumber}
                width={64}
              />
              <Tooltip />
              <Area
                type="monotone"
                dataKey="equity"
                stroke="var(--c-cyan)"
                fill="url(#equityFill)"
                strokeWidth={2}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <p className="analytics-empty">Not enough closed trades yet to plot an equity curve.</p>
        )}
      </div>

      <div className="analytics-section">
        <div className="card-title">Daily PnL</div>
        {dailyPnl.length ? (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={dailyPnl}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="day" stroke="#8b90a8" fontSize={12} />
              <YAxis stroke="#8b90a8" fontSize={12} />
              <Tooltip />
              <Bar dataKey="pnl" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                {dailyPnl.map((d) => (
                  <Cell key={d.day} fill={d.pnl >= 0 ? "#00f5a0" : "#ff5d73"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="analytics-empty">No closed trades yet.</p>
        )}
      </div>

      <div className="analytics-section">
        <div className="card-title">Drawdown</div>
        {drawdownCurve.length > 1 ? (
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={drawdownCurve}>
              <defs>
                <linearGradient id="ddFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ff5d73" stopOpacity={0} />
                  <stop offset="95%" stopColor="#ff5d73" stopOpacity={0.5} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="label" stroke="#8b90a8" fontSize={12} />
              <YAxis stroke="#8b90a8" fontSize={12} domain={["dataMin", 0]} />
              <Tooltip />
              <Area
                type="monotone"
                dataKey="drawdown"
                stroke="#ff5d73"
                fill="url(#ddFill)"
                strokeWidth={2}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <p className="analytics-empty">Not enough data yet to plot drawdown.</p>
        )}
      </div>
    </>
  );
}

export default memo(PortfolioAnalytics);
