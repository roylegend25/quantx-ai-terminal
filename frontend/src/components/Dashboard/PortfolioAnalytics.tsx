import { useMemo } from "react";
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

type Portfolio = {
  balance?: number;
  equity?: number;
  unrealized_pnl?: number;
  daily_pnl?: number;
  total_pnl?: number;
  win_rate?: number;
  sharpe_ratio?: number;
};

type Position = {
  id: number;
  symbol: string;
};

type Trade = {
  id: number;
  status: string;
  pnl?: number;
  closed_at?: string | null;
};

type Props = {
  portfolio: Portfolio | null;
  positions: Position[];
  history: Trade[];
};

type Tone = "pos" | "neg" | "neutral";

type Tile = {
  label: string;
  value: string;
  tone: Tone;
};

function fmtUsd(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function fmtPct(n: number): string {
  return `${n.toFixed(2)}%`;
}

function tone(n: number): Tone {
  if (n > 0) return "pos";
  if (n < 0) return "neg";
  return "neutral";
}

export default function PortfolioAnalytics({ portfolio, positions, history }: Props) {
  const closedTrades = useMemo(() => {
    return (history || [])
      .filter((t): t is Trade & { closed_at: string; pnl: number } =>
        t.status === "CLOSED" && !!t.closed_at && typeof t.pnl === "number"
      )
      .slice()
      .sort((a, b) => new Date(a.closed_at).getTime() - new Date(b.closed_at).getTime());
  }, [history]);

  const initialBalance = useMemo(() => {
    if (portfolio && typeof portfolio.balance === "number" && typeof portfolio.total_pnl === "number") {
      const base = portfolio.balance - portfolio.total_pnl;
      return base > 0 ? base : portfolio.balance;
    }
    return 10000;
  }, [portfolio]);

  const equityCurve = useMemo(() => {
    let running = initialBalance;
    const points = [{ label: "Start", equity: running }];

    closedTrades.forEach((t) => {
      running += t.pnl;
      points.push({
        label: new Date(t.closed_at).toLocaleDateString([], { month: "short", day: "numeric" }),
        equity: Math.round(running * 100) / 100,
      });
    });

    const current = typeof portfolio?.equity === "number" ? portfolio.equity : running;
    points.push({ label: "Now", equity: Math.round(current * 100) / 100 });

    return points;
  }, [closedTrades, initialBalance, portfolio]);

  const drawdownCurve = useMemo(() => {
    let peak = -Infinity;
    return equityCurve.map((p) => {
      peak = Math.max(peak, p.equity);
      const dd = peak > 0 ? ((p.equity - peak) / peak) * 100 : 0;
      return { label: p.label, drawdown: Math.round(dd * 100) / 100 };
    });
  }, [equityCurve]);

  const maxDrawdown = useMemo(
    () => drawdownCurve.reduce((min, p) => Math.min(min, p.drawdown), 0),
    [drawdownCurve]
  );

  const dailyPnl = useMemo(() => {
    const byDay = new Map<string, number>();
    closedTrades.forEach((t) => {
      const day = t.closed_at.slice(0, 10);
      byDay.set(day, (byDay.get(day) || 0) + t.pnl);
    });
    return Array.from(byDay.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([day, pnl]) => ({ day: day.slice(5), pnl: Math.round(pnl * 100) / 100 }));
  }, [closedTrades]);

  const { avgWin, avgLoss, profitFactor, expectancy, winRatePct } = useMemo(() => {
    const wins = closedTrades.filter((t) => t.pnl > 0).map((t) => t.pnl);
    const losses = closedTrades.filter((t) => t.pnl < 0).map((t) => Math.abs(t.pnl));
    const grossProfit = wins.reduce((a, b) => a + b, 0);
    const grossLoss = losses.reduce((a, b) => a + b, 0);

    const aw = wins.length ? grossProfit / wins.length : 0;
    const al = losses.length ? grossLoss / losses.length : 0;
    const pf = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

    const wr =
      typeof portfolio?.win_rate === "number"
        ? portfolio.win_rate
        : closedTrades.length
        ? (wins.length / closedTrades.length) * 100
        : 0;

    const wrFraction = wr / 100;
    const exp = wrFraction * aw - (1 - wrFraction) * al;

    return { avgWin: aw, avgLoss: al, profitFactor: pf, expectancy: exp, winRatePct: wr };
  }, [closedTrades, portfolio]);

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
    { label: "Unrealized PnL", value: fmtUsd(portfolio?.unrealized_pnl ?? 0), tone: tone(portfolio?.unrealized_pnl ?? 0) },
    { label: "Realized PnL", value: fmtUsd(realizedPnl), tone: tone(realizedPnl) },
    { label: "Daily PnL", value: fmtUsd(portfolio?.daily_pnl ?? 0), tone: tone(portfolio?.daily_pnl ?? 0) },
    { label: "Total Return", value: fmtPct(totalReturnPct), tone: tone(totalReturnPct) },
    { label: "Win Rate", value: fmtPct(winRatePct), tone: "neutral" },
    { label: "Average Win", value: fmtUsd(avgWin), tone: "pos" },
    { label: "Average Loss", value: fmtUsd(-avgLoss), tone: "neg" },
    {
      label: "Profit Factor",
      value: Number.isFinite(profitFactor) ? profitFactor.toFixed(2) : "∞",
      tone: "neutral",
    },
    { label: "Expectancy", value: fmtUsd(expectancy), tone: tone(expectancy) },
    { label: "Max Drawdown", value: `-${Math.abs(maxDrawdown).toFixed(2)}%`, tone: "neg" },
    ...(sharpeRatio !== null
      ? [{ label: "Sharpe Ratio", value: sharpeRatio.toFixed(2), tone: tone(sharpeRatio) }]
      : []),
    { label: "Open Positions", value: String(positions?.length ?? 0), tone: "neutral" },
    { label: "Closed Trades", value: String(closedTrades.length), tone: "neutral" },
  ];

  return (
    <>
      <div className="analytics-grid">
        {tiles.map((t) => (
          <div className="analytics-tile" key={t.label}>
            <span className="tile-label">{t.label}</span>
            <b className={`tile-value ${t.tone === "pos" ? "green" : t.tone === "neg" ? "red" : ""}`}>
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
                  <stop offset="5%" stopColor="#00f5d4" stopOpacity={0.5} />
                  <stop offset="95%" stopColor="#00f5d4" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="label" stroke="#8b90a8" fontSize={12} />
              <YAxis stroke="#8b90a8" fontSize={12} domain={["dataMin", "dataMax"]} />
              <Tooltip />
              <Area type="monotone" dataKey="equity" stroke="#00f5d4" fill="url(#equityFill)" strokeWidth={2} />
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
              <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
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
              <Area type="monotone" dataKey="drawdown" stroke="#ff5d73" fill="url(#ddFill)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <p className="analytics-empty">Not enough data yet to plot drawdown.</p>
        )}
      </div>
    </>
  );
}
