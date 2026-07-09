import { Gauge } from "lucide-react";
import { fmtNum, fmtPct, fmtUsd } from "../../lib/format";
import {
  recoveryFactor,
  strategyScore,
  tradeAverages,
  type BtTrade,
} from "../../lib/backtestStats";

type Props = {
  metrics: any | null; // combo.metrics from /api/backtest/run-advanced
  trades: BtTrade[];
  loading: boolean;
};

type Kpi = {
  label: string;
  value: string;
  tone?: "green" | "red" | "";
  hero?: boolean;
};

function toneOfNum(v: number | null | undefined): "green" | "red" | "" {
  if (typeof v !== "number" || Number.isNaN(v) || v === 0) return "";
  return v > 0 ? "green" : "red";
}

export default function KpiPanel({ metrics, trades, loading }: Props) {
  if (loading) {
    return (
      <div className="bt-kpi-panel">
        <div className="bt-panel-head">
          <Gauge size={15} />
          <span>Performance</span>
        </div>
        <div className="bt-skeleton-stack">
          {Array.from({ length: 8 }).map((_, i) => (
            <div className="bt-skeleton" key={i} />
          ))}
        </div>
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="bt-kpi-panel">
        <div className="bt-panel-head">
          <Gauge size={15} />
          <span>Performance</span>
        </div>
        <p className="bt-note">Run a backtest to populate the performance dashboard.</p>
      </div>
    );
  }

  const avgs = tradeAverages(trades);
  const recovery = recoveryFactor(metrics.equity_curve);
  const score = strategyScore(metrics);

  const kpis: Kpi[] = [
    { label: "Total Return", value: fmtPct(metrics.total_return_pct), tone: toneOfNum(metrics.total_return_pct), hero: true },
    { label: "Net Profit", value: fmtUsd(avgs.netProfit), tone: toneOfNum(avgs.netProfit), hero: true },
    { label: "Profit Factor", value: fmtNum(metrics.profit_factor) },
    { label: "Win Rate", value: fmtPct(metrics.win_rate, 1) },
    { label: "Sharpe Ratio", value: fmtNum(metrics.sharpe_ratio) },
    { label: "Sortino Ratio", value: fmtNum(metrics.sortino_ratio) },
    { label: "Calmar Ratio", value: fmtNum(metrics.calmar_ratio) },
    { label: "Expectancy", value: fmtUsd(metrics.expectancy) },
    { label: "Average Trade", value: fmtUsd(avgs.avgTrade), tone: toneOfNum(avgs.avgTrade) },
    { label: "Average Winner", value: fmtUsd(avgs.avgWinner), tone: "green" },
    { label: "Average Loser", value: fmtUsd(avgs.avgLoser), tone: avgs.avgLoser != null ? "red" : "" },
    { label: "Max Drawdown", value: fmtPct(metrics.max_drawdown_pct), tone: metrics.max_drawdown_pct > 0 ? "red" : "" },
    { label: "Recovery Factor", value: fmtNum(recovery) },
    { label: "Total Trades", value: metrics.total_trades != null ? String(metrics.total_trades) : "—" },
    { label: "Long Win %", value: fmtPct(metrics.long_accuracy_pct, 1) },
    { label: "Short Win %", value: fmtPct(metrics.short_accuracy_pct, 1) },
    { label: "Strategy Score", value: fmtNum(score), hero: true },
  ];

  return (
    <div className="bt-kpi-panel">
      <div className="bt-panel-head">
        <Gauge size={15} />
        <span>Performance</span>
      </div>
      <div className="bt-kpi-grid">
        {kpis.map((k, i) => (
          <div
            className={`bt-kpi-card ${k.hero ? "hero" : ""}`}
            key={k.label}
            style={{ animationDelay: `${i * 35}ms` }}
          >
            <span className="tile-label">{k.label}</span>
            <b className={`bt-kpi-value ${k.tone || ""}`}>{k.value}</b>
          </div>
        ))}
      </div>
    </div>
  );
}
