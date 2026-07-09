import { useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  Beaker,
  Brain,
  Database,
  Dices,
  Download,
  FileDown,
  GitCompare,
  History,
  Layers,
  LineChart as LineChartIcon,
  ListOrdered,
  PlayCircle,
  ShieldAlert,
  SlidersHorizontal,
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
import { api } from "../services/api";
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

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"];
const ALL_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"];
const DATA_TYPES = ["candles", "funding", "open_interest", "orderbook", "trades", "sentiment", "liquidations"];
const PRESETS = ["", "scalping", "intraday", "swing", "trend_following", "mean_reversion", "high_volatility", "low_volatility"];

const DATA_FORM_DEFAULT = {
  data_type: "candles",
  symbol: "BTCUSDT",
  timeframe: "5m",
  provider: "binance_futures",
  limit: 1000,
};

const ADV_FORM_DEFAULT = {
  strategy: "ensemble",
  preset: "",
  symbols: ["BTCUSDT"],
  timeframes: ["5m"],
  train_split: 0.7,
  monte_carlo_sims: 500,
  trailing_stop_atr_mult: 0,
  max_holding_bars: 0,
  daily_loss_limit_pct: 0,
  max_drawdown_stop_pct: 0,
};

function downloadBlob(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function tradesToCsv(trades: any[]): string {
  if (!trades?.length) return "";
  const cols = ["side", "entry_time", "exit_time", "entry_price", "exit_price", "pnl", "r_multiple", "exit_reason", "regime", "commission", "funding"];
  const lines = [cols.join(",")];
  for (const t of trades) {
    lines.push(cols.map((c) => (t[c] != null ? String(t[c]).replace(/,/g, ";") : "")).join(","));
  }
  return lines.join("\n");
}

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
  dataSources,
  dataJobs,
  dataQuality,
  dataGaps,
  advRun,
  advWalkForward,
  advMonteCarlo,
  learningPerformance,
  learningWeights,
  learningRecommendations,
  loadDataEngine,
  startDataDownload,
  loadDataGaps,
  runAdvancedBacktest,
  runAdvancedWalkForward,
  runAdvancedMonteCarlo,
  loadLearning,
  runLearningEvaluate,
  showToast,
}: Props) {
  const [form, setForm] = useState(DEFAULT_FORM);
  const [busy, setBusy] = useState<string | null>(null);
  const [dataForm, setDataForm] = useState(DATA_FORM_DEFAULT);
  const [advForm, setAdvForm] = useState(ADV_FORM_DEFAULT);
  const [optResult, setOptResult] = useState<any>(null);

  useEffect(() => {
    loadLabExperiments();
    loadDataEngine();
    loadLearning();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // keep the jobs table live while a download is running
  useEffect(() => {
    const anyActive = (dataJobs || []).some((j: any) => j.status === "queued" || j.status === "running");
    if (!anyActive) return;
    const id = window.setInterval(() => loadDataEngine(), 3000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataJobs]);

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

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((x) => x !== value) : [...list, value];

  const advCombos: any[] = advRun?.combinations || [];
  const advFirstOk = advCombos.find((c: any) => c.ok);
  const advEquity = (advFirstOk?.equity_curve || []).map((v: number, i: number) => ({ i, equity: v }));
  const advDrawdown = (advFirstOk?.drawdown_curve || []).map((v: number, i: number) => ({ i, drawdown: -v }));
  const coverage: any[] = dataQuality?.coverage || [];
  const qualityReports: any[] = dataQuality?.reports || [];

  return (
    <div className="page-grid">
      <Card title="Data Engine" full right={<Database size={16} />}>
        <div className="controls" style={{ marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
          {(dataSources?.providers || []).map((p: any) => (
            <span
              key={p.name}
              className="tile-label"
              style={{
                padding: "4px 10px",
                borderRadius: 12,
                border: "1px solid rgba(128,128,128,0.35)",
                opacity: p.available ? 1 : 0.5,
              }}
              title={p.reason || ""}
            >
              {p.available ? "●" : "○"} {p.name} {p.requires_key && !p.available ? "(no key)" : ""}
            </span>
          ))}
        </div>

        <div className="analytics-grid">
          <div className="lab-field">
            <label className="tile-label">Data Type</label>
            <select value={dataForm.data_type} onChange={(e) => setDataForm((f) => ({ ...f, data_type: e.target.value }))}>
              {DATA_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <div className="lab-field">
            <label className="tile-label">Symbol</label>
            <select value={dataForm.symbol} onChange={(e) => setDataForm((f) => ({ ...f, symbol: e.target.value }))}>
              {SYMBOLS.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="lab-field">
            <label className="tile-label">Timeframe</label>
            <select value={dataForm.timeframe} onChange={(e) => setDataForm((f) => ({ ...f, timeframe: e.target.value }))}>
              {ALL_TIMEFRAMES.map((tf) => (
                <option key={tf} value={tf}>{tf}</option>
              ))}
            </select>
          </div>
          <div className="lab-field">
            <label className="tile-label">Provider</label>
            <select value={dataForm.provider} onChange={(e) => setDataForm((f) => ({ ...f, provider: e.target.value }))}>
              <option value="binance_futures">binance_futures</option>
              <option value="binance_spot">binance_spot</option>
              <option value="coinglass">coinglass</option>
            </select>
          </div>
          <div className="lab-field">
            <label className="tile-label">Candles / Rows</label>
            <input
              type="number"
              value={dataForm.limit}
              onChange={(e) => setDataForm((f) => ({ ...f, limit: Number(e.target.value) }))}
            />
          </div>
        </div>

        <div className="controls" style={{ marginTop: 14 }}>
          <button
            disabled={busy !== null}
            onClick={() =>
              withBusy("download", async () => {
                const res = await startDataDownload(dataForm);
                showToast(res?.ok ? `Download job ${res.job?.job_id} started` : "Download failed");
              })
            }
          >
            <Download size={16} />
            {busy === "download" ? "Starting…" : "Download Data"}
          </button>
          <button disabled={busy !== null} onClick={() => withBusy("refresh-data", () => loadDataEngine())}>
            <Activity size={16} /> Refresh Status
          </button>
          <button
            disabled={busy !== null}
            onClick={() =>
              withBusy("gaps", async () => {
                const res = await loadDataGaps(dataForm.symbol, dataForm.timeframe);
                if (res?.error) showToast(res.error);
              })
            }
          >
            <ShieldAlert size={16} /> Gap Report
          </button>
        </div>

        {dataGaps && !dataGaps.error && (
          <p className="regime-desc" style={{ marginTop: 10 }}>
            {dataGaps.symbol} {dataGaps.timeframe}: quality {fmtNum(dataGaps.quality_score, 1)}/100,{" "}
            {dataGaps.missing_candles} missing candles, {dataGaps.interpolated} interpolated,{" "}
            {dataGaps.rejected_gaps} rejected gap(s), largest gap {dataGaps.largest_gap_bars} bars.
            {(dataGaps.gaps || []).length
              ? ` First gaps: ${(dataGaps.gaps || [])
                  .slice(0, 3)
                  .map((g: any) => `${g.bars} bars (${g.action || "open"})`)
                  .join(", ")}`
              : " No gaps recorded."}
          </p>
        )}

        {coverage.length > 0 && (
          <div className="table-wrap" style={{ marginTop: 14 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Timeframe</th>
                  <th>Rows</th>
                  <th>Interpolated</th>
                  <th>Quality</th>
                  <th>Last Candle</th>
                  <th>Last Checked</th>
                </tr>
              </thead>
              <tbody>
                {coverage.map((c: any) => (
                  <tr key={`${c.symbol}-${c.timeframe}`}>
                    <td>{c.symbol}</td>
                    <td>{c.timeframe}</td>
                    <td>{c.rows}</td>
                    <td>{c.interpolated_rows}</td>
                    <td className={c.quality_score == null ? "" : c.quality_score >= 80 ? "green" : c.quality_score >= 40 ? "" : "red"}>
                      {c.quality_score != null ? `${fmtNum(c.quality_score, 1)}/100` : "—"}
                    </td>
                    <td>{c.last_timestamp ? fmtRelativeTime(c.last_timestamp) : "—"}</td>
                    <td>{c.last_checked ? fmtRelativeTime(c.last_checked) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {(dataJobs || []).length > 0 && (
          <div className="table-wrap" style={{ marginTop: 14 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Type</th>
                  <th>Symbol</th>
                  <th>TF</th>
                  <th>Status</th>
                  <th>Fetched</th>
                  <th>Stored</th>
                  <th>Quality</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {(dataJobs || []).slice(0, 8).map((j: any) => (
                  <tr key={j.job_id}>
                    <td>{j.job_id}</td>
                    <td>{j.data_type}</td>
                    <td>{j.symbol || "—"}</td>
                    <td>{j.timeframe || "—"}</td>
                    <td className={j.status === "succeeded" ? "green" : j.status === "failed" ? "red" : ""}>{j.status}</td>
                    <td>{j.rows_fetched}</td>
                    <td>{j.rows_stored}</td>
                    <td>{j.quality_score != null ? fmtNum(j.quality_score, 1) : "—"}</td>
                    <td className="tile-label" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {j.error || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {qualityReports.length === 0 && coverage.length === 0 && (
          <p className="analytics-empty" style={{ marginTop: 10 }}>
            No datasets downloaded yet — pick a symbol/timeframe above and hit Download Data.
          </p>
        )}
      </Card>

      <Card title="Advanced Backtest" full right={<Layers size={16} />}>
        <div className="analytics-grid">
          <div className="lab-field">
            <label className="tile-label">Strategy / Model</label>
            <select value={advForm.strategy} onChange={(e) => setAdvForm((f) => ({ ...f, strategy: e.target.value }))}>
              {STRATEGIES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>
          <div className="lab-field">
            <label className="tile-label">Preset</label>
            <select value={advForm.preset} onChange={(e) => setAdvForm((f) => ({ ...f, preset: e.target.value }))}>
              {PRESETS.map((p) => (
                <option key={p} value={p}>{p || "custom (no preset)"}</option>
              ))}
            </select>
          </div>
          <div className="lab-field">
            <label className="tile-label">Train Split (in-sample)</label>
            <input
              type="number" step="0.05" min="0.1" max="0.9"
              value={advForm.train_split}
              onChange={(e) => setAdvForm((f) => ({ ...f, train_split: Number(e.target.value) }))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Monte Carlo Sims (0 = off)</label>
            <input
              type="number"
              value={advForm.monte_carlo_sims}
              onChange={(e) => setAdvForm((f) => ({ ...f, monte_carlo_sims: Number(e.target.value) }))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Trailing Stop (ATR ×, 0 = off)</label>
            <input
              type="number" step="0.1"
              value={advForm.trailing_stop_atr_mult}
              onChange={(e) => setAdvForm((f) => ({ ...f, trailing_stop_atr_mult: Number(e.target.value) }))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Max Holding (bars, 0 = off)</label>
            <input
              type="number"
              value={advForm.max_holding_bars}
              onChange={(e) => setAdvForm((f) => ({ ...f, max_holding_bars: Number(e.target.value) }))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Daily Loss Limit % (0 = off)</label>
            <input
              type="number" step="0.5"
              value={advForm.daily_loss_limit_pct}
              onChange={(e) => setAdvForm((f) => ({ ...f, daily_loss_limit_pct: Number(e.target.value) }))}
            />
          </div>
          <div className="lab-field">
            <label className="tile-label">Drawdown Breaker % (0 = off)</label>
            <input
              type="number" step="0.5"
              value={advForm.max_drawdown_stop_pct}
              onChange={(e) => setAdvForm((f) => ({ ...f, max_drawdown_stop_pct: Number(e.target.value) }))}
            />
          </div>
        </div>

        <div className="controls" style={{ marginTop: 12, flexWrap: "wrap" }}>
          <span className="tile-label">Symbols:</span>
          {SYMBOLS.map((s) => (
            <button
              key={s}
              className={advForm.symbols.includes(s) ? "" : "ghost"}
              style={{ opacity: advForm.symbols.includes(s) ? 1 : 0.45 }}
              onClick={() => setAdvForm((f) => ({ ...f, symbols: toggle(f.symbols, s) }))}
            >
              {s.replace("USDT", "")}
            </button>
          ))}
        </div>
        <div className="controls" style={{ marginTop: 8, flexWrap: "wrap" }}>
          <span className="tile-label">Timeframes:</span>
          {ALL_TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              className={advForm.timeframes.includes(tf) ? "" : "ghost"}
              style={{ opacity: advForm.timeframes.includes(tf) ? 1 : 0.45 }}
              onClick={() => setAdvForm((f) => ({ ...f, timeframes: toggle(f.timeframes, tf) }))}
            >
              {tf}
            </button>
          ))}
        </div>

        <div className="controls" style={{ marginTop: 14 }}>
          <button
            disabled={busy !== null || !advForm.symbols.length || !advForm.timeframes.length}
            onClick={() =>
              withBusy("adv-run", async () => {
                const payload: Record<string, unknown> = {
                  ...advForm,
                  preset: advForm.preset || null,
                  trailing_stop_atr_mult: advForm.trailing_stop_atr_mult || null,
                  max_holding_bars: advForm.max_holding_bars || null,
                  daily_loss_limit_pct: advForm.daily_loss_limit_pct || null,
                  max_drawdown_stop_pct: advForm.max_drawdown_stop_pct || null,
                };
                const res = await runAdvancedBacktest(payload);
                const ok = res?.run?.summary?.combinations_ok ?? 0;
                showToast(`Advanced run complete: ${ok}/${res?.run?.summary?.combinations_run ?? 0} combinations OK`);
              })
            }
          >
            <PlayCircle size={16} />
            {busy === "adv-run" ? "Running…" : "Run Advanced Backtest"}
          </button>
          <button
            disabled={busy !== null || !advRun?.run_id}
            onClick={() =>
              withBusy("adv-wf", async () => {
                await runAdvancedWalkForward(advRun.run_id);
                showToast("Walk-forward complete");
              })
            }
          >
            <Waves size={16} /> Walk-Forward
          </button>
          <button
            disabled={busy !== null || !advRun?.run_id}
            onClick={() =>
              withBusy("adv-mc", async () => {
                await runAdvancedMonteCarlo(advRun.run_id, 1000);
                showToast("Monte Carlo complete");
              })
            }
          >
            <Dices size={16} /> Monte Carlo
          </button>
          <button
            disabled={!advRun}
            onClick={() => downloadBlob(`backtest_${advRun.run_id}.json`, JSON.stringify(advRun, null, 2), "application/json")}
          >
            <FileDown size={16} /> Export JSON
          </button>
          <button
            disabled={!advFirstOk?.trades?.length}
            onClick={() =>
              downloadBlob(
                `trades_${advRun.run_id}_${advFirstOk.symbol}_${advFirstOk.timeframe}.csv`,
                tradesToCsv(advFirstOk.trades),
                "text/csv"
              )
            }
          >
            <FileDown size={16} /> Export Trades CSV
          </button>
        </div>

        {advRun && (
          <>
            <div className="table-wrap" style={{ marginTop: 16 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>TF</th>
                    <th>Data</th>
                    <th>Trades</th>
                    <th>Return</th>
                    <th>OOS Return</th>
                    <th>Sharpe</th>
                    <th>Win Rate</th>
                    <th>Max DD</th>
                    <th>Consec. Losses</th>
                    <th>Long / Short Acc.</th>
                  </tr>
                </thead>
                <tbody>
                  {advCombos.map((c: any) => (
                    <tr key={`${c.symbol}-${c.timeframe}`}>
                      <td>{c.symbol}</td>
                      <td>{c.timeframe}</td>
                      {c.ok ? (
                        <>
                          <td className="tile-label">
                            {c.data?.source === "market_candles" ? "DB" : "CSV"}
                            {c.data?.quality_score != null ? ` · Q${fmtNum(c.data.quality_score, 0)}` : ""}
                          </td>
                          <td>{c.metrics?.total_trades ?? 0}</td>
                          <td className={(c.metrics?.total_return_pct ?? 0) >= 0 ? "green" : "red"}>
                            {fmtPct(c.metrics?.total_return_pct, 2)}
                          </td>
                          <td>{fmtPct(c.out_of_sample_metrics?.total_return_pct, 2)}</td>
                          <td>{fmtNum(c.metrics?.sharpe_ratio, 2)}</td>
                          <td>{fmtPct(c.metrics?.win_rate, 1)}</td>
                          <td>{fmtPct(c.metrics?.max_drawdown_pct, 2)}</td>
                          <td>{c.metrics?.max_consecutive_losses ?? "—"}</td>
                          <td>
                            {fmtPct(c.metrics?.long_accuracy_pct, 0)} / {fmtPct(c.metrics?.short_accuracy_pct, 0)}
                          </td>
                        </>
                      ) : (
                        <td colSpan={9} className="tile-label">{c.error}</td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {advFirstOk?.metrics?.regime_performance && (
              <div className="table-wrap" style={{ marginTop: 14 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Regime ({advFirstOk.symbol} {advFirstOk.timeframe})</th>
                      <th>Trades</th>
                      <th>Win Rate</th>
                      <th>Total PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(advFirstOk.metrics.regime_performance).map(([regime, s]: [string, any]) => (
                      <tr key={regime}>
                        <td>{regime}</td>
                        <td>{s.trades}</td>
                        <td>{fmtPct(s.win_rate, 1)}</td>
                        <td className={s.total_pnl >= 0 ? "green" : "red"}>{fmtNum(s.total_pnl, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {advRun.summary?.timeframe_performance && Object.keys(advRun.summary.timeframe_performance).length > 1 && (
              <div className="table-wrap" style={{ marginTop: 14 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Timeframe</th>
                      <th>Avg Return</th>
                      <th>Avg Sharpe</th>
                      <th>Avg Win Rate</th>
                      <th>Avg Max DD</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(advRun.summary.timeframe_performance).map(([tf, m]: [string, any]) => (
                      <tr key={tf}>
                        <td>{tf}</td>
                        <td>{fmtPct(m?.total_return_pct, 2)}</td>
                        <td>{fmtNum(m?.sharpe_ratio, 2)}</td>
                        <td>{fmtPct(m?.win_rate, 1)}</td>
                        <td>{fmtPct(m?.max_drawdown_pct, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {advFirstOk?.strategy_contribution && (
              <div className="table-wrap" style={{ marginTop: 14 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Strategy Contribution</th>
                      <th>Trades</th>
                      <th>Return</th>
                      <th>Win Rate</th>
                      <th>Profit Factor</th>
                      <th>Sharpe</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(advFirstOk.strategy_contribution).map(([name, m]: [string, any]) => (
                      <tr key={name}>
                        <td>{name}</td>
                        {m?.error ? (
                          <td colSpan={5} className="tile-label">{m.error}</td>
                        ) : (
                          <>
                            <td>{m?.total_trades ?? 0}</td>
                            <td>{fmtPct(m?.total_return_pct, 2)}</td>
                            <td>{fmtPct(m?.win_rate, 1)}</td>
                            <td>{fmtNum(m?.profit_factor, 2)}</td>
                            <td>{fmtNum(m?.sharpe_ratio, 2)}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {(advFirstOk?.risk_events || []).length > 0 && (
              <p className="regime-desc" style={{ marginTop: 10 }}>
                Risk events: {(advFirstOk.risk_events || []).map((e: any) => e.detail).join(" · ")}
              </p>
            )}
          </>
        )}
      </Card>

      {advFirstOk && (
        <>
          <Card title={`Advanced Equity (${advFirstOk.symbol} ${advFirstOk.timeframe})`} right={<LineChartIcon size={16} />}>
            <div style={{ height: 240 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={advEquity}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                  <XAxis dataKey="i" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={64} />
                  <Tooltip />
                  <Line type="monotone" dataKey="equity" stroke="var(--c-cyan)" strokeWidth={2.5} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <Card title="Advanced Drawdown" right={<BarChart3 size={16} />}>
            <div style={{ height: 240 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={advDrawdown}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                  <XAxis dataKey="i" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={64} />
                  <Tooltip />
                  <Area type="monotone" dataKey="drawdown" stroke="#ff5c7a" fill="rgba(255,92,122,0.25)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </>
      )}

      {advWalkForward?.ok && (
        <Card title="Advanced Walk-Forward" full right={<Waves size={16} />}>
          {(advWalkForward.results || []).map((r: any) => (
            <div key={`${r.symbol}-${r.timeframe}`} style={{ marginBottom: 12 }}>
              <p className="regime-desc">
                {r.symbol} {r.timeframe}: {r.windows_run} windows ({r.train_bars} train / {r.validate_bars} validate bars). Avg
                return {fmtPct(r.average_metrics?.total_return_pct, 2)}, avg win rate {fmtPct(r.average_metrics?.win_rate, 1)}.
              </p>
            </div>
          ))}
        </Card>
      )}

      {advMonteCarlo?.ok && (
        <Card title="Advanced Monte Carlo" full right={<Dices size={16} />}>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>TF</th>
                  <th>Risk of Ruin</th>
                  <th>Worst DD</th>
                  <th>Median Final</th>
                  <th>90% CI</th>
                </tr>
              </thead>
              <tbody>
                {(advMonteCarlo.results || []).map((r: any) => (
                  <tr key={`${r.symbol}-${r.timeframe}`}>
                    <td>{r.symbol}</td>
                    <td>{r.timeframe}</td>
                    {r.ok !== false ? (
                      <>
                        <td>{fmtPct(r.risk_of_ruin_pct, 2)}</td>
                        <td>{fmtPct(r.worst_drawdown_pct, 2)}</td>
                        <td>{fmtNum(r.median_final_balance, 0)}</td>
                        <td>
                          {r.final_balance_confidence_interval_90pct
                            ? `${fmtNum(r.final_balance_confidence_interval_90pct[0], 0)} – ${fmtNum(r.final_balance_confidence_interval_90pct[1], 0)}`
                            : "—"}
                        </td>
                      </>
                    ) : (
                      <td colSpan={4} className="tile-label">{r.error}</td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
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
          <button
            disabled={busy !== null}
            onClick={() =>
              withBusy("optimize", async () => {
                const res = await api.backtestOptimize({
                  strategy: form.strategy,
                  symbol: form.symbol,
                  timeframe: form.timeframe,
                  start_date: form.start_date || null,
                  end_date: form.end_date || null,
                  starting_balance: form.starting_balance,
                  position_size_usd: form.position_size_usd,
                });
                if (res?.ok) {
                  setOptResult(res.optimization);
                  showToast(`Optimization complete: ${res.optimization?.combinations_tested ?? 0} combinations tested`);
                } else {
                  showToast(res?.detail ? String(res.detail) : "Optimization failed");
                }
              })
            }
          >
            <SlidersHorizontal size={16} />
            {busy === "optimize" ? "Optimizing…" : "Optimize Parameters"}
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
                  <Line type="monotone" dataKey="equity" stroke="var(--c-cyan)" strokeWidth={2.5} dot={false} />
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

      {optResult && (
        <Card title="Parameter Optimization" full right={<SlidersHorizontal size={16} />}>
          <p className="regime-desc">
            {optResult.combinations_tested} combinations of ATR stop-loss / take-profit multiples and entry-confidence
            thresholds, ranked by Sharpe ratio — {optResult.strategy} on {optResult.symbol} {optResult.timeframe}
            {optResult.data_info?.source ? ` (data: ${optResult.data_info.source}${optResult.data_info.quality_score != null ? `, quality ${fmtNum(optResult.data_info.quality_score, 1)}` : ""})` : ""}.
          </p>
          {optResult.best && (
            <div className="analytics-grid" style={{ marginTop: 12 }}>
              <div className="analytics-tile">
                <span className="tile-label">Best SL Mult</span>
                <b className="tile-value">{fmtNum(optResult.best.atr_sl_mult, 1)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Best TP Mult</span>
                <b className="tile-value">{fmtNum(optResult.best.atr_tp_mult, 1)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Best Entry Threshold</span>
                <b className="tile-value">{fmtNum(optResult.best.entry_confidence_threshold, 0)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Best Score</span>
                <b className="tile-value">{fmtNum(optResult.best.score, 3)}</b>
              </div>
            </div>
          )}
          <div className="table-wrap" style={{ marginTop: 14 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>SL Mult</th>
                  <th>TP Mult</th>
                  <th>Entry Threshold</th>
                  <th>Score</th>
                  <th>Total Return</th>
                  <th>Sharpe</th>
                  <th>Win Rate</th>
                  <th>Max Drawdown</th>
                  <th>Trades</th>
                </tr>
              </thead>
              <tbody>
                {(optResult.results || []).slice(0, 20).map((r: any, i: number) => (
                  <tr key={i}>
                    <td>{fmtNum(r.atr_sl_mult, 1)}</td>
                    <td>{fmtNum(r.atr_tp_mult, 1)}</td>
                    <td>{fmtNum(r.entry_confidence_threshold, 0)}</td>
                    <td>{fmtNum(r.score, 3)}</td>
                    <td className={(r.metrics?.total_return_pct ?? 0) >= 0 ? "green" : "red"}>
                      {fmtPct(r.metrics?.total_return_pct, 2)}
                    </td>
                    <td>{fmtNum(r.metrics?.sharpe_ratio, 2)}</td>
                    <td>{fmtPct(r.metrics?.win_rate, 1)}</td>
                    <td>{fmtPct(r.metrics?.max_drawdown_pct, 2)}</td>
                    <td>{r.metrics?.total_trades ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card title="AI Learning Loop" full right={<Brain size={16} />}>
        <div className="controls" style={{ marginBottom: 12 }}>
          <button
            disabled={busy !== null}
            onClick={() =>
              withBusy("evaluate", async () => {
                const res = await runLearningEvaluate();
                const resolved = res?.performance?.predictions_resolved ?? 0;
                showToast(`Evaluated: ${resolved} predictions resolved against recorded outcomes`);
              })
            }
          >
            <PlayCircle size={16} />
            {busy === "evaluate" ? "Evaluating…" : "Evaluate Prediction History"}
          </button>
          <button disabled={busy !== null} onClick={() => withBusy("learning-refresh", () => loadLearning())}>
            <Activity size={16} /> Refresh
          </button>
        </div>

        {learningPerformance?.evaluated_at ? (
          <>
            <div className="analytics-grid">
              <div className="analytics-tile">
                <span className="tile-label">Direction Hit Rate</span>
                <b className="tile-value">{fmtPct(learningPerformance.direction_hit_rate_pct, 1)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Resolved / Considered</span>
                <b className="tile-value">
                  {learningPerformance.predictions_resolved} / {learningPerformance.predictions_considered}
                </b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Avg Target Error</span>
                <b className="tile-value">{fmtPct(learningPerformance.avg_error_pct, 2)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Avg Confidence</span>
                <b className="tile-value">{fmtNum(learningPerformance.avg_confidence, 1)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Last Evaluated</span>
                <b className="tile-value">{fmtRelativeTime(learningPerformance.evaluated_at)}</b>
              </div>
            </div>

            {learningPerformance.by_timeframe && Object.keys(learningPerformance.by_timeframe).length > 0 && (
              <div className="table-wrap" style={{ marginTop: 14 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Timeframe</th>
                      <th>Predictions</th>
                      <th>Hit Rate</th>
                      <th>Avg Error</th>
                      <th>Avg Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(learningPerformance.by_timeframe).map(([tf, s]: [string, any]) => (
                      <tr key={tf}>
                        <td>{tf}</td>
                        <td>{s.predictions}</td>
                        <td className={s.hit_rate_pct >= 50 ? "green" : "red"}>{fmtPct(s.hit_rate_pct, 1)}</td>
                        <td>{fmtPct(s.avg_error_pct, 2)}</td>
                        <td>{fmtNum(s.avg_confidence, 1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {(learningPerformance.confidence_reliability || []).some((b: any) => b.predictions > 0) && (
              <div className="table-wrap" style={{ marginTop: 14 }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Confidence Bucket</th>
                      <th>Predictions</th>
                      <th>Actual Hit Rate</th>
                      <th>Stated Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(learningPerformance.confidence_reliability || [])
                      .filter((b: any) => b.predictions > 0)
                      .map((b: any) => (
                        <tr key={b.bucket}>
                          <td>{b.bucket}</td>
                          <td>{b.predictions}</td>
                          <td>{fmtPct(b.hit_rate_pct, 1)}</td>
                          <td>{fmtNum(b.avg_confidence, 1)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <p className="analytics-empty">
            No evaluation yet — hit "Evaluate Prediction History" to score stored predictions against recorded outcomes.
          </p>
        )}

        {learningWeights?.live_weights && (
          <div className="table-wrap" style={{ marginTop: 14 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Live Weight</th>
                  <th>Candidate Weight (shadow)</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(learningWeights.live_weights).map((name) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td>{fmtNum(learningWeights.live_weights[name], 3)}</td>
                    <td>{fmtNum(learningWeights.candidate_weights?.[name], 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {(learningRecommendations?.recommendations || []).length > 0 && (
          <div style={{ marginTop: 14 }}>
            {(learningRecommendations.recommendations || []).map((r: any, i: number) => (
              <p key={i} className="regime-desc" style={{ marginBottom: 6 }}>
                <b style={{ textTransform: "uppercase", marginRight: 6 }}>
                  [{r.severity}] {r.type}:
                </b>
                {r.reason} — {r.action}
              </p>
            ))}
          </div>
        )}
      </Card>

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
