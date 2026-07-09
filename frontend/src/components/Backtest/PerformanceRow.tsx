import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import Card from "../Layout/Card";
import { fmtAxisNumber, fmtNum, fmtPct } from "../../lib/format";
import {
  drawdownFromEquity,
  monthlyReturns,
  pnlHistogram,
  rollingSeries,
  type BtTrade,
} from "../../lib/backtestStats";

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const GRID = "rgba(255,255,255,0.06)";
const TICK = { fontSize: 10, fill: "#8b90a8" };

const tooltipStyle = {
  background: "rgba(12,13,24,0.95)",
  border: "1px solid rgba(124,92,255,0.35)",
  borderRadius: 10,
  fontSize: 12,
};

function ChartEmpty({ text }: { text: string }) {
  return <p className="analytics-empty">{text}</p>;
}

type Props = {
  metrics: any | null;
  trades: BtTrade[];
  startingBalance: number;
};

export default function PerformanceRow({ metrics, trades, startingBalance }: Props) {
  const equity = useMemo(
    () => (metrics?.equity_curve || []).map((v: number, i: number) => ({ i, equity: v })),
    [metrics]
  );
  const drawdown = useMemo(
    () => drawdownFromEquity(metrics?.equity_curve).map((v, i) => ({ i, drawdown: -v })),
    [metrics]
  );
  const histogram = useMemo(() => pnlHistogram(trades), [trades]);
  const monthly = useMemo(() => monthlyReturns(trades, startingBalance), [trades, startingBalance]);
  const rolling = useMemo(() => rollingSeries(trades, startingBalance), [trades, startingBalance]);

  const monthlyByYear = useMemo(() => {
    const map = new Map<number, (number | null)[]>();
    for (const cell of monthly) {
      if (!map.has(cell.year)) map.set(cell.year, Array.from({ length: 12 }, () => null));
      map.get(cell.year)![cell.month] = cell.pct;
    }
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [monthly]);

  const monthlyMax = useMemo(
    () => Math.max(0.01, ...monthly.map((m) => Math.abs(m.pct))),
    [monthly]
  );

  return (
    <div className="bt-row bt-row-charts">
      <Card title="Equity Curve" className="bt-chart-card">
        {equity.length > 1 ? (
          <div className="bt-recharts">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equity} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="btEquityFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--c-cyan)" stopOpacity={0.32} />
                    <stop offset="100%" stopColor="var(--c-cyan)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
                <XAxis dataKey="i" tick={TICK} tickLine={false} axisLine={false} label={undefined} />
                <YAxis
                  tick={TICK}
                  tickLine={false}
                  axisLine={false}
                  width={58}
                  domain={["auto", "auto"]}
                  tickFormatter={fmtAxisNumber}
                />
                <Tooltip
                  contentStyle={tooltipStyle}
                  labelFormatter={(i) => `Trade #${i}`}
                  formatter={(v: any) => [fmtNum(v, 2), "Equity"]}
                />
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke="var(--c-cyan)"
                  strokeWidth={2}
                  fill="url(#btEquityFill)"
                  animationDuration={600}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <ChartEmpty text="Equity curve appears after the first run with trades." />
        )}
      </Card>

      <Card title="Drawdown" className="bt-chart-card">
        {drawdown.length > 1 ? (
          <div className="bt-recharts">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={drawdown} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
                <XAxis dataKey="i" tick={TICK} tickLine={false} axisLine={false} />
                <YAxis
                  tick={TICK}
                  tickLine={false}
                  axisLine={false}
                  width={48}
                  tickFormatter={(v) => `${v}%`}
                />
                <Tooltip
                  contentStyle={tooltipStyle}
                  labelFormatter={(i) => `Trade #${i}`}
                  formatter={(v: any) => [fmtPct(Math.abs(v), 2), "Drawdown"]}
                />
                <Area
                  type="monotone"
                  dataKey="drawdown"
                  stroke="var(--c-red)"
                  strokeWidth={2}
                  fill="rgba(255,93,115,0.18)"
                  animationDuration={600}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <ChartEmpty text="Drawdown chart appears after the first run with trades." />
        )}
      </Card>

      <Card title="Trade PnL Distribution" className="bt-chart-card">
        {histogram.length ? (
          <div className="bt-recharts">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={histogram} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
                <XAxis dataKey="label" tick={TICK} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                <YAxis tick={TICK} tickLine={false} axisLine={false} width={32} allowDecimals={false} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  labelFormatter={(l) => `PnL ≈ ${l}`}
                  formatter={(v: any) => [v, "Trades"]}
                  cursor={{ fill: "rgba(255,255,255,0.05)" }}
                />
                <Bar dataKey="count" radius={[4, 4, 0, 0]} animationDuration={600}>
                  {histogram.map((bin, i) => (
                    <Cell key={i} fill={bin.mid >= 0 ? "var(--c-green)" : "var(--c-red)"} fillOpacity={0.75} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <ChartEmpty text="PnL histogram appears once the run produces trades." />
        )}
      </Card>

      <Card title="Monthly Returns" className="bt-chart-card">
        {monthlyByYear.length ? (
          <div className="bt-heatmap-wrap">
            <div className="bt-heatmap" style={{ gridTemplateColumns: `44px repeat(12, 1fr)` }}>
              <span />
              {MONTH_LABELS.map((m) => (
                <span className="bt-heatmap-label" key={m}>{m}</span>
              ))}
              {monthlyByYear.map(([year, months]) => (
                <YearRow key={year} year={year} months={months} maxAbs={monthlyMax} />
              ))}
            </div>
          </div>
        ) : (
          <ChartEmpty text="Monthly heatmap needs trades spanning at least one month." />
        )}
      </Card>

      <Card title="Rolling Sharpe (20 trades)" className="bt-chart-card">
        {rolling.length ? (
          <div className="bt-recharts">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rolling} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
                <XAxis dataKey="i" tick={TICK} tickLine={false} axisLine={false} />
                <YAxis tick={TICK} tickLine={false} axisLine={false} width={40} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  labelFormatter={(i) => `Through trade #${i}`}
                  formatter={(v: any) => [fmtNum(v, 3), "Rolling Sharpe"]}
                />
                <Line
                  type="monotone"
                  dataKey="sharpe"
                  stroke="var(--c-purple)"
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                  animationDuration={600}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <ChartEmpty text="Needs at least ~15 closed trades to compute a rolling window." />
        )}
      </Card>

      <Card title="Rolling Win Rate (20 trades)" className="bt-chart-card">
        {rolling.length ? (
          <div className="bt-recharts">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rolling} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
                <XAxis dataKey="i" tick={TICK} tickLine={false} axisLine={false} />
                <YAxis tick={TICK} tickLine={false} axisLine={false} width={40} domain={[0, 100]} tickFormatter={(v) => `${v}%`} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  labelFormatter={(i) => `Through trade #${i}`}
                  formatter={(v: any) => [fmtPct(v, 1), "Win rate"]}
                />
                <Line
                  type="monotone"
                  dataKey="winRate"
                  stroke="var(--c-blue)"
                  strokeWidth={2}
                  dot={false}
                  animationDuration={600}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <ChartEmpty text="Needs at least ~15 closed trades to compute a rolling window." />
        )}
      </Card>
    </div>
  );
}

function YearRow({ year, months, maxAbs }: { year: number; months: (number | null)[]; maxAbs: number }) {
  return (
    <>
      <span className="bt-heatmap-label">{year}</span>
      {months.map((pct, i) => {
        if (pct == null) {
          return <span className="bt-heatmap-cell empty" key={i} />;
        }
        const alpha = 0.12 + Math.min(1, Math.abs(pct) / maxAbs) * 0.55;
        const bg = pct >= 0 ? `rgba(0,245,160,${alpha})` : `rgba(255,93,115,${alpha})`;
        return (
          <span
            className="bt-heatmap-cell"
            key={i}
            style={{ background: bg }}
            title={`${MONTH_LABELS[i]} ${year}: ${fmtPct(pct, 2)}`}
          >
            {Math.abs(pct) >= 0.05 ? `${pct > 0 ? "+" : ""}${pct.toFixed(1)}` : "0"}
          </span>
        );
      })}
    </>
  );
}
