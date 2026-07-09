import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import Card from "../Layout/Card";
import { fmtNum, fmtPct } from "../../lib/format";
import { confusionMatrix, type BtTrade } from "../../lib/backtestStats";

// Fixed categorical hue per sub-strategy — assigned by entity, never cycled.
const STRATEGY_HEX: Record<string, string> = {
  trend: "#7c5cff",
  momentum: "#00f5d4",
  mean_reversion: "#00a8ff",
  breakout: "#ffd166",
};

const REGIME_LABELS: { key: string; label: string }[] = [
  { key: "TRENDING_UP", label: "Bull Market" },
  { key: "TRENDING_DOWN", label: "Bear Market" },
  { key: "RANGING", label: "Sideways" },
  { key: "HIGH_VOLATILITY", label: "High Volatility" },
  { key: "LOW_VOLATILITY", label: "Low Volatility" },
];

const GRID = "rgba(255,255,255,0.06)";
const TICK = { fontSize: 10, fill: "#8b90a8" };
const tooltipStyle = {
  background: "rgba(12,13,24,0.95)",
  border: "1px solid rgba(124,92,255,0.35)",
  borderRadius: 10,
  fontSize: 12,
};

type Props = {
  combo: any | null; // selected combination from the advanced run
  run: any | null; // full run (summary.timeframe_performance)
  trades: BtTrade[];
};

export default function StrategyAnalytics({ combo, run, trades }: Props) {
  const contribution = combo?.strategy_contribution || null;

  const pieData = useMemo(() => {
    if (!contribution) return [];
    return Object.entries(contribution)
      .filter(([, m]: [string, any]) => m && !m.error && (m.total_trades ?? 0) > 0)
      .map(([name, m]: [string, any]) => ({
        name: name.replace(/_/g, " "),
        key: name,
        value: m.total_trades ?? 0,
        returnPct: m.total_return_pct,
      }));
  }, [contribution]);

  const radarData = useMemo(() => {
    if (!contribution) return { axes: [] as any[], strategies: [] as string[] };
    const entries = Object.entries(contribution).filter(([, m]: [string, any]) => m && !m.error);
    if (!entries.length) return { axes: [] as any[], strategies: [] as string[] };
    const metricsDef: { key: string; label: string }[] = [
      { key: "win_rate", label: "Win Rate" },
      { key: "total_return_pct", label: "Return" },
      { key: "profit_factor", label: "Profit Factor" },
      { key: "sharpe_ratio", label: "Sharpe" },
      { key: "total_trades", label: "Activity" },
    ];
    const axes = metricsDef.map(({ key, label }) => {
      const values = entries.map(([, m]: [string, any]) => Number(m[key]) || 0);
      const max = Math.max(0.001, ...values.map((v) => Math.abs(v)));
      const row: Record<string, number | string> = { metric: label };
      entries.forEach(([name, m]: [string, any], i) => {
        // normalized 0-100 per axis so different units share one radial scale
        row[name] = Math.max(0, ((Number(m[key]) || 0) / max) * 100);
        void i;
      });
      return row;
    });
    return { axes, strategies: entries.map(([name]) => name) };
  }, [contribution]);

  const regimeRows = useMemo(() => {
    const perf = combo?.metrics?.regime_performance || {};
    return REGIME_LABELS.map(({ key, label }) => ({
      key,
      label,
      stats: perf[key] || null,
    }));
  }, [combo]);

  const tfRows = useMemo(() => {
    const perf = run?.summary?.timeframe_performance || {};
    return Object.entries(perf).map(([tf, m]: [string, any]) => ({
      tf: tf.toUpperCase(),
      returnPct: m?.total_return_pct ?? null,
      sharpe: m?.sharpe_ratio ?? null,
      winRate: m?.win_rate ?? null,
      maxDd: m?.max_drawdown_pct ?? null,
    }));
  }, [run]);

  const confusion = useMemo(() => confusionMatrix(trades), [trades]);
  const totalConf = confusion.longWin + confusion.longLoss + confusion.shortWin + confusion.shortLoss;
  const accuracy = combo?.metrics?.prediction_accuracy_pct;

  return (
    <div className="bt-row bt-row-analytics">
      <Card title="Strategy Contribution" className="bt-chart-card">
        {pieData.length ? (
          <div className="bt-recharts">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={pieData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius="52%"
                  outerRadius="82%"
                  paddingAngle={3}
                  stroke="rgba(5,6,10,0.9)"
                  strokeWidth={2}
                  animationDuration={600}
                >
                  {pieData.map((d) => (
                    <Cell key={d.key} fill={STRATEGY_HEX[d.key] || "#8b90a8"} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v: any, _n: any, item: any) => [
                    `${v} trades · ${fmtPct(item?.payload?.returnPct, 2)} return`,
                    item?.payload?.name,
                  ]}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} iconSize={9} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="analytics-empty">
            Contribution is computed when the run uses the Ensemble strategy — each sub-strategy is
            replayed on the same data.
          </p>
        )}
      </Card>

      <Card title="Market Regime Performance" className="bt-chart-card">
        {regimeRows.some((r) => r.stats) ? (
          <div className="bt-regime-list">
            {regimeRows.map((r) => (
              <div className="bt-regime-row" key={r.key}>
                <span className="bt-regime-name">{r.label}</span>
                {r.stats ? (
                  <>
                    <div className="bt-regime-bar-track">
                      <div
                        className="bt-regime-bar-fill"
                        style={{
                          width: `${Math.min(100, Math.max(2, r.stats.win_rate || 0))}%`,
                          background:
                            (r.stats.total_pnl ?? 0) >= 0
                              ? "linear-gradient(90deg, rgba(0,245,160,0.5), var(--c-green))"
                              : "linear-gradient(90deg, rgba(255,93,115,0.5), var(--c-red))",
                        }}
                      />
                    </div>
                    <span className="bt-regime-stats">
                      {r.stats.trades} trades · {fmtPct(r.stats.win_rate, 0)} win ·{" "}
                      <b className={(r.stats.total_pnl ?? 0) >= 0 ? "green" : "red"}>
                        {fmtNum(r.stats.total_pnl, 0)}
                      </b>
                    </span>
                  </>
                ) : (
                  <span className="bt-regime-stats tile-label" style={{ marginBottom: 0 }}>
                    no trades in this regime
                  </span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="analytics-empty">Regime attribution appears once a run produces trades.</p>
        )}
      </Card>

      <Card title="Timeframe Comparison" className="bt-chart-card">
        {tfRows.length > 1 ? (
          <div className="bt-recharts">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={tfRows} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
                <XAxis dataKey="tf" tick={TICK} tickLine={false} axisLine={false} />
                <YAxis tick={TICK} tickLine={false} axisLine={false} width={44} tickFormatter={(v) => `${v}%`} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v: any, name: any) => [
                    name === "returnPct" ? fmtPct(v, 2) : fmtNum(v, 2),
                    name === "returnPct" ? "Avg return" : name,
                  ]}
                  cursor={{ fill: "rgba(255,255,255,0.05)" }}
                />
                <Bar dataKey="returnPct" radius={[4, 4, 0, 0]} animationDuration={600}>
                  {tfRows.map((r, i) => (
                    <Cell key={i} fill={(r.returnPct ?? 0) >= 0 ? "#00f5a0" : "#ff5d73"} fillOpacity={0.8} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : tfRows.length === 1 ? (
          <div className="bt-single-tf">
            <p className="bt-note">
              Single timeframe run ({tfRows[0].tf}): {fmtPct(tfRows[0].returnPct, 2)} return, Sharpe{" "}
              {fmtNum(tfRows[0].sharpe, 2)}, {fmtPct(tfRows[0].winRate, 1)} win rate.
            </p>
            <p className="analytics-empty">
              Run the same strategy from the Research Lab across multiple timeframes to compare them here.
            </p>
          </div>
        ) : (
          <p className="analytics-empty">Timeframe comparison appears after a run completes.</p>
        )}
      </Card>

      <Card title="Strategy Radar" className="bt-chart-card">
        {radarData.axes.length ? (
          <div className="bt-recharts">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radarData.axes} outerRadius="72%">
                <PolarGrid stroke="rgba(255,255,255,0.1)" />
                <PolarAngleAxis dataKey="metric" tick={{ fontSize: 10, fill: "#8b90a8" }} />
                <PolarRadiusAxis tick={false} axisLine={false} domain={[0, 100]} />
                {radarData.strategies.map((name) => (
                  <Radar
                    key={name}
                    name={name.replace(/_/g, " ")}
                    dataKey={name}
                    stroke={STRATEGY_HEX[name] || "#8b90a8"}
                    fill={STRATEGY_HEX[name] || "#8b90a8"}
                    fillOpacity={0.08}
                    strokeWidth={2}
                    animationDuration={600}
                  />
                ))}
                <Legend wrapperStyle={{ fontSize: 11 }} iconSize={9} />
                <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => [`${fmtNum(v, 0)} / 100`, ""]} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="analytics-empty">
            The radar compares sub-strategies (normalized per axis) — available for Ensemble runs.
          </p>
        )}
      </Card>

      <Card title="Direction × Outcome" className="bt-chart-card">
        {totalConf > 0 ? (
          <div className="bt-confusion">
            <div className="bt-confusion-grid">
              <span />
              <span className="bt-heatmap-label">Win</span>
              <span className="bt-heatmap-label">Loss</span>
              <span className="bt-heatmap-label">Long</span>
              <ConfCell count={confusion.longWin} total={totalConf} good />
              <ConfCell count={confusion.longLoss} total={totalConf} />
              <span className="bt-heatmap-label">Short</span>
              <ConfCell count={confusion.shortWin} total={totalConf} good />
              <ConfCell count={confusion.shortLoss} total={totalConf} />
            </div>
            <p className="bt-note" style={{ textAlign: "center" }}>
              Prediction accuracy:{" "}
              <b className={typeof accuracy === "number" && accuracy >= 50 ? "green" : "red"}>
                {fmtPct(accuracy, 1)}
              </b>{" "}
              over {totalConf} directional calls
            </p>
          </div>
        ) : (
          <p className="analytics-empty">
            The confusion matrix (predicted direction vs realized outcome) appears once trades exist.
          </p>
        )}
      </Card>
    </div>
  );
}

function ConfCell({ count, total, good = false }: { count: number; total: number; good?: boolean }) {
  const frac = total > 0 ? count / total : 0;
  const alpha = 0.1 + frac * 0.6;
  return (
    <span
      className="bt-confusion-cell"
      style={{ background: good ? `rgba(0,245,160,${alpha})` : `rgba(255,93,115,${alpha})` }}
    >
      <b>{count}</b>
      <small>{total ? `${Math.round(frac * 100)}%` : ""}</small>
    </span>
  );
}
