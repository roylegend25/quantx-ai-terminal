import { useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  Beaker,
  Dices,
  GitCompare,
  History,
  LineChart as LineChartIcon,
  ListOrdered,
  PlayCircle,
  Waves,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import Card from "../components/Layout/Card";
import { fmtDuration, fmtNum, fmtPct, fmtRelativeTime } from "../lib/format";
import type { AppData } from "../hooks/useAppData";

type Props = AppData;

const STRATEGIES: { value: string; label: string }[] = [
  { value: "trend", label: "Trend Following" },
  { value: "momentum", label: "Momentum" },
  { value: "mean_reversion", label: "Mean Reversion" },
  { value: "breakout", label: "Breakout" },
  { value: "ensemble", label: "Adaptive Ensemble" },
  { value: "champion_ml", label: "Champion ML" },
  { value: "challenger_ml", label: "Challenger ML" },
];

const TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"];

const METRIC_ROWS: { key: string; label: string; fmt: (v: any) => string }[] = [
  { key: "total_return_pct", label: "Total Return", fmt: (v) => fmtPct(v, 2) },
  { key: "cagr_pct", label: "CAGR", fmt: (v) => fmtPct(v, 2) },
  { key: "sharpe_ratio", label: "Sharpe", fmt: (v) => fmtNum(v, 2) },
  { key: "sortino_ratio", label: "Sortino", fmt: (v) => fmtNum(v, 2) },
  { key: "calmar_ratio", label: "Calmar", fmt: (v) => fmtNum(v, 2) },
  { key: "profit_factor", label: "Profit Factor", fmt: (v) => fmtNum(v, 2) },
  { key: "win_rate", label: "Win Rate", fmt: (v) => fmtPct(v, 1) },
  { key: "expectancy", label: "Expectancy", fmt: (v) => fmtNum(v, 4) },
  { key: "max_drawdown_pct", label: "Max Drawdown", fmt: (v) => fmtPct(v, 2) },
  { key: "average_r", label: "Average R", fmt: (v) => fmtNum(v, 2) },
  { key: "best_trade_pnl", label: "Best Trade", fmt: (v) => fmtNum(v, 2) },
  { key: "worst_trade_pnl", label: "Worst Trade", fmt: (v) => fmtNum(v, 2) },
  { key: "average_holding_time_seconds", label: "Avg Holding Time", fmt: (v) => fmtDuration(v) },
  { key: "exposure_time_pct", label: "Exposure Time", fmt: (v) => fmtPct(v, 1) },
];

const DEFAULT_FORM = {
  strategy: "ensemble",
  symbol: "BTCUSDT",
  timeframe: "5m",
  start_date: "",
  end_date: "",
  starting_balance: 10000,
  position_size_usd: 1000,
  commission_pct: 0.04,
  slippage_bps: 2,
  spread_bps: 2,
  funding_rate_pct: 0.01,
  latency_ms: 250,
  partial_fill_ratio: 1,
  atr_sl_mult: 1.5,
  atr_tp_mult: 3,
  entry_confidence_threshold: 50,
};

export default function ResearchLabPage({
  labExperiments,
  labRun,
  labCompare,
  labMonteCarlo,
  labWalkForward,
  loadLabExperiments,
  runLabBacktest,
  runLabCompare,
  runLabMonteCarlo,
  runLabWalkForward,
  showToast,
}: Props) {
  const [form, setForm] = useState(DEFAULT_FORM);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    loadLabExperiments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function setField<K extends keyof typeof DEFAULT_FORM>(key: K, value: (typeof DEFAULT_FORM)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function withBusy(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    try {
      await fn();
    } catch (e: any) {
      showToast(e?.message || "Action failed");
    } finally {
      setBusy(null);
    }
  }

  const run = labRun?.run;
  const metrics = run?.metrics;
  const equityChart = (run?.equity_curve || []).map((v: number, i: number) => ({ i, equity: v }));
  const drawdownChart = (run?.drawdown_curve || []).map((v: number, i: number) => ({ i, drawdown: -v }));
  const experimentId = labRun?.experiment?.experiment_id;

  return (
    <div className="page-grid">
      <Card title="Backtest Runner" full right={<Beaker size={16} />}>
        <div className="analytics-grid">
          <div className="lab-field">
            <label className="tile-label">Strategy / Model</label>
            <select value={form.strategy} onChange={(e) => setField("strategy", e.target.value)}>
              {STRATEGIES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <div className="lab-field">
            <label className="tile-label">Symbol</label>
            <input value={form.symbol} onChange={(e) => setField("symbol", e.target.value.toUpperCase())} />
          </div>
          <div className="lab-field">
            <label className="tile-label">Timeframe</label>
            <select value={form.timeframe} onChange={(e) => setField("timeframe", e.target.value)}>
              {TIMEFRAMES.map((tf) => (
                <option key={tf} value={tf}>
                  {tf}
                </option>
              ))}
            </select>
          </div>
          <div className="lab-field">
            <label className="tile-label">Start Date</label>
            <input type="date" value={form.start_date} onChange={(e) => setField("start_date", e.target.value)} />
          </div>
          <div className="lab-field">
            <label className="tile-label">End Date</label>
            <input type="date" value={form.end_date} onChange={(e) => setField("end_date", e.target.value)} />
          </div>
          <div className="lab-field">
            <label className="tile-label">Starting Balance</label>
            <input
              type="number"
              value={form.starting_balance}
              onChange={(e) => setField("starting_balance", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Position Size (USD)</label>
            <input
              type="number"
              value={form.position_size_usd}
              onChange={(e) => setField("position_size_usd", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Commission %</label>
            <input
              type="number"
              step="0.01"
              value={form.commission_pct}
              onChange={(e) => setField("commission_pct", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Slippage (bps)</label>
            <input
              type="number"
              step="0.5"
              value={form.slippage_bps}
              onChange={(e) => setField("slippage_bps", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Spread (bps)</label>
            <input
              type="number"
              step="0.5"
              value={form.spread_bps}
              onChange={(e) => setField("spread_bps", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Funding Rate % / 8h</label>
            <input
              type="number"
              step="0.01"
              value={form.funding_rate_pct}
              onChange={(e) => setField("funding_rate_pct", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Latency (ms)</label>
            <input
              type="number"
              value={form.latency_ms}
              onChange={(e) => setField("latency_ms", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Partial Fill Ratio</label>
            <input
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={form.partial_fill_ratio}
              onChange={(e) => setField("partial_fill_ratio", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">ATR Stop-Loss Mult</label>
            <input
              type="number"
              step="0.1"
              value={form.atr_sl_mult}
              onChange={(e) => setField("atr_sl_mult", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">ATR Take-Profit Mult</label>
            <input
              type="number"
              step="0.1"
              value={form.atr_tp_mult}
              onChange={(e) => setField("atr_tp_mult", Number(e.target.value))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Entry Confidence Threshold</label>
            <input
              type="number"
              value={form.entry_confidence_threshold}
              onChange={(e) => setField("entry_confidence_threshold", Number(e.target.value))}
            />
          </div>
        </div>

        <div className="controls" style={{ marginTop: 18 }}>
          <button
            disabled={busy !== null}
            onClick={() =>
              withBusy("run", async () => {
                const res = await runLabBacktest({ ...form, start_date: form.start_date || null, end_date: form.end_date || null });
                if (res?.ok) {
                  showToast(`${form.strategy}: ${res.run?.metrics?.total_trades ?? 0} trades simulated`);
                } else {
                  showToast(res?.detail ? String(res.detail) : "Backtest failed");
                }
              })
            }
          >
            <PlayCircle size={16} />
            {busy === "run" ? "Running…" : "Run Backtest"}
          </button>
          <button
            disabled={busy !== null}
            onClick={() =>
              withBusy("compare", async () => {
                await runLabCompare(form.symbol, form.timeframe);
                showToast("Strategy comparison complete");
              })
            }
          >
            <GitCompare size={16} />
            {busy === "compare" ? "Comparing…" : "Compare All Strategies"}
          </button>
          <button
            disabled={busy !== null || !experimentId}
            onClick={() =>
              withBusy("montecarlo", async () => {
                await runLabMonteCarlo(experimentId, 1000);
                showToast("Monte Carlo simulation complete");
              })
            }
          >
            <Dices size={16} />
            {busy === "montecarlo" ? "Simulating…" : "Run Monte Carlo"}
          </button>
          <button
            disabled={busy !== null || !experimentId}
            onClick={() =>
              withBusy("walkforward", async () => {
                await runLabWalkForward(experimentId, 500, 150);
                showToast("Walk-forward validation complete");
              })
            }
          >
            <Waves size={16} />
            {busy === "walkforward" ? "Validating…" : "Walk-Forward Validate"}
          </button>
        </div>
      </Card>

      {run && (
        <>
          <Card title="Equity Curve" right={<LineChartIcon size={16} />}>
            <div style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={equityChart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                  <XAxis dataKey="i" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={64} />
                  <Tooltip />
                  <Line type="monotone" dataKey="equity" stroke="#00f5d4" strokeWidth={2.5} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card title="Drawdown" right={<BarChart3 size={16} />}>
            <div style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={drawdownChart}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                  <XAxis dataKey="i" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={64} />
                  <Tooltip />
                  <Area type="monotone" dataKey="drawdown" stroke="#ff5c7a" fill="rgba(255,92,122,0.25)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card title="Metrics" full right={<Activity size={16} />}>
            <div className="analytics-grid">
              {METRIC_ROWS.map((row) => (
                <div className="analytics-tile" key={row.key}>
                  <span className="tile-label">{row.label}</span>
                  <b className="tile-value">{row.fmt(metrics?.[row.key])}</b>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Trade List" full right={<ListOrdered size={16} />}>
            {!run.trades?.length ? (
              <p className="analytics-empty">No trades were generated for this configuration.</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Side</th>
                      <th>Entry</th>
                      <th>Exit</th>
                      <th>Entry Time</th>
                      <th>Exit Time</th>
                      <th>PnL</th>
                      <th>R</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.trades.slice(0, 200).map((t: any, i: number) => (
                      <tr key={i}>
                        <td>{t.side}</td>
                        <td>{fmtNum(t.entry_price, 4)}</td>
                        <td>{fmtNum(t.exit_price, 4)}</td>
                        <td>{fmtRelativeTime(t.entry_time)}</td>
                        <td>{fmtRelativeTime(t.exit_time)}</td>
                        <td className={t.pnl >= 0 ? "green" : "red"}>{fmtNum(t.pnl, 2)}</td>
                        <td>{fmtNum(t.r_multiple, 2)}</td>
                        <td>{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      {labCompare?.ok && (
        <Card title="Strategy Comparison" full right={<GitCompare size={16} />}>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Total Return</th>
                  <th>Sharpe</th>
                  <th>Win Rate</th>
                  <th>Profit Factor</th>
                  <th>Max Drawdown</th>
                  <th>Trades</th>
                </tr>
              </thead>
              <tbody>
                {labCompare.results.map((r: any) => (
                  <tr key={r.strategy}>
                    <td>{STRATEGIES.find((s) => s.value === r.strategy)?.label || r.strategy}</td>
                    {r.ok ? (
                      <>
                        <td>{fmtPct(r.metrics?.total_return_pct, 2)}</td>
                        <td>{fmtNum(r.metrics?.sharpe_ratio, 2)}</td>
                        <td>{fmtPct(r.metrics?.win_rate, 1)}</td>
                        <td>{fmtNum(r.metrics?.profit_factor, 2)}</td>
                        <td>{fmtPct(r.metrics?.max_drawdown_pct, 2)}</td>
                        <td>{r.metrics?.total_trades ?? 0}</td>
                      </>
                    ) : (
                      <td colSpan={6} className="tile-label">
                        {r.error || "Not available"}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {labMonteCarlo?.ok && (
        <Card title="Monte Carlo Simulation" right={<Dices size={16} />}>
          <div className="analytics-grid">
            <div className="analytics-tile">
              <span className="tile-label">Simulations</span>
              <b className="tile-value">{labMonteCarlo.simulations}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Risk of Ruin</span>
              <b className="tile-value">{fmtPct(labMonteCarlo.risk_of_ruin_pct, 2)}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Worst Drawdown</span>
              <b className="tile-value">{fmtPct(labMonteCarlo.worst_drawdown_pct, 2)}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Median CAGR</span>
              <b className="tile-value">{fmtPct(labMonteCarlo.median_cagr_pct, 2)}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Median Final Balance</span>
              <b className="tile-value">{fmtNum(labMonteCarlo.median_final_balance, 2)}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">90% Confidence Interval</span>
              <b className="tile-value">
                {labMonteCarlo.final_balance_confidence_interval_90pct
                  ? `${fmtNum(labMonteCarlo.final_balance_confidence_interval_90pct[0], 0)} – ${fmtNum(labMonteCarlo.final_balance_confidence_interval_90pct[1], 0)}`
                  : "—"}
              </b>
            </div>
          </div>
        </Card>
      )}

      {labWalkForward?.ok && (
        <Card title="Walk-Forward Validation" right={<Waves size={16} />}>
          <p className="regime-desc">
            {labWalkForward.windows_run} windows of {labWalkForward.validate_bars} bars each, stepping forward from{" "}
            {labWalkForward.train_bars}-bar training windows.
          </p>
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Window</th>
                  <th>Total Return</th>
                  <th>Sharpe</th>
                  <th>Win Rate</th>
                  <th>Trades</th>
                </tr>
              </thead>
              <tbody>
                {labWalkForward.windows.map((w: any) => (
                  <tr key={w.window}>
                    <td>{w.window}</td>
                    <td>{fmtPct(w.metrics?.total_return_pct, 2)}</td>
                    <td>{fmtNum(w.metrics?.sharpe_ratio, 2)}</td>
                    <td>{fmtPct(w.metrics?.win_rate, 1)}</td>
                    <td>{w.metrics?.total_trades ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card title="Experiment History" full right={<History size={16} />}>
        {!labExperiments?.length ? (
          <p className="analytics-empty">No backtests run yet - configure one above and hit Run.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Symbol</th>
                  <th>Timeframe</th>
                  <th>Total Return</th>
                  <th>Sharpe</th>
                  <th>Trades</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {labExperiments.map((e: any) => (
                  <tr key={e.experiment_id}>
                    <td>{STRATEGIES.find((s) => s.value === e.strategy)?.label || e.strategy}</td>
                    <td>{e.symbol}</td>
                    <td>{e.timeframe}</td>
                    <td>{fmtPct(e.results?.metrics?.total_return_pct, 2)}</td>
                    <td>{fmtNum(e.results?.metrics?.sharpe_ratio, 2)}</td>
                    <td>{e.results?.metrics?.total_trades ?? 0}</td>
                    <td>{fmtRelativeTime(e.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
