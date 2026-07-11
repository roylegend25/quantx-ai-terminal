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
import AutoCardTable from "../components/Responsive/AutoCardTable";
import { fmtDuration, fmtNum, fmtPct } from "../lib/format";
import LocalTime from "../components/LocalTime";
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
          <div style={{ marginTop: 14 }}>
            <AutoCardTable
              columns={[
                { key: "symbol", label: "Symbol", render: (c: any) => c.symbol },
                { key: "timeframe", label: "Timeframe", render: (c: any) => c.timeframe },
                { key: "rows", label: "Rows", render: (c: any) => c.rows },
                { key: "interpolated", label: "Interpolated", render: (c: any) => c.interpolated_rows },
                {
                  key: "quality",
                  label: "Quality",
                  render: (c: any) => (
                    <span className={c.quality_score == null ? "" : c.quality_score >= 80 ? "green" : c.quality_score >= 40 ? "" : "red"}>
                      {c.quality_score != null ? `${fmtNum(c.quality_score, 1)}/100` : "—"}
                    </span>
                  ),
                },
                { key: "lastCandle", label: "Last Candle", render: (c: any) => (c.last_timestamp ? <LocalTime value={c.last_timestamp} label="Last candle" /> : "—") },
                { key: "lastChecked", label: "Last Checked", render: (c: any) => (c.last_checked ? <LocalTime value={c.last_checked} label="Last checked" /> : "—") },
              ]}
              rows={coverage}
              keyField={(c: any) => `${c.symbol}-${c.timeframe}`}
              titleColumn="symbol"
            />
          </div>
        )}

        {(dataJobs || []).length > 0 && (
          <div style={{ marginTop: 14 }}>
            <AutoCardTable
              columns={[
                { key: "job", label: "Job", render: (j: any) => j.job_id },
                { key: "type", label: "Type", render: (j: any) => j.data_type },
                { key: "symbol", label: "Symbol", render: (j: any) => j.symbol || "—" },
                { key: "tf", label: "TF", render: (j: any) => j.timeframe || "—" },
                {
                  key: "status",
                  label: "Status",
                  render: (j: any) => <span className={j.status === "succeeded" ? "green" : j.status === "failed" ? "red" : ""}>{j.status}</span>,
                },
                { key: "fetched", label: "Fetched", render: (j: any) => j.rows_fetched },
                { key: "stored", label: "Stored", render: (j: any) => j.rows_stored },
                { key: "quality", label: "Quality", render: (j: any) => (j.quality_score != null ? fmtNum(j.quality_score, 1) : "—") },
                { key: "error", label: "Error", render: (j: any) => <span className="tile-label">{j.error || "—"}</span> },
              ]}
              rows={(dataJobs || []).slice(0, 8)}
              keyField={(j: any) => j.job_id}
              titleColumn="job"
              statusColumn="status"
            />
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
            <div style={{ marginTop: 16 }}>
              <AutoCardTable
                columns={[
                  { key: "symbol", label: "Symbol", render: (c: any) => c.symbol },
                  { key: "tf", label: "TF", render: (c: any) => c.timeframe },
                  {
                    key: "data",
                    label: "Data",
                    render: (c: any) =>
                      c.ok ? (
                        <span className="tile-label">
                          {c.data?.source === "market_candles" ? "DB" : "CSV"}
                          {c.data?.quality_score != null ? ` · Q${fmtNum(c.data.quality_score, 0)}` : ""}
                        </span>
                      ) : (
                        <span className="tile-label">{c.error}</span>
                      ),
                  },
                  { key: "trades", label: "Trades", render: (c: any) => (c.ok ? c.metrics?.total_trades ?? 0 : null) },
                  {
                    key: "return",
                    label: "Return",
                    render: (c: any) => (c.ok ? <span className={(c.metrics?.total_return_pct ?? 0) >= 0 ? "green" : "red"}>{fmtPct(c.metrics?.total_return_pct, 2)}</span> : null),
                  },
                  { key: "oosReturn", label: "OOS Return", render: (c: any) => (c.ok ? fmtPct(c.out_of_sample_metrics?.total_return_pct, 2) : null) },
                  { key: "sharpe", label: "Sharpe", render: (c: any) => (c.ok ? fmtNum(c.metrics?.sharpe_ratio, 2) : null) },
                  { key: "winRate", label: "Win Rate", render: (c: any) => (c.ok ? fmtPct(c.metrics?.win_rate, 1) : null) },
                  { key: "maxDd", label: "Max DD", render: (c: any) => (c.ok ? fmtPct(c.metrics?.max_drawdown_pct, 2) : null) },
                  { key: "consecLosses", label: "Consec. Losses", render: (c: any) => (c.ok ? c.metrics?.max_consecutive_losses ?? "—" : null) },
                  {
                    key: "longShortAcc",
                    label: "Long / Short Acc.",
                    render: (c: any) => (c.ok ? `${fmtPct(c.metrics?.long_accuracy_pct, 0)} / ${fmtPct(c.metrics?.short_accuracy_pct, 0)}` : null),
                  },
                ]}
                rows={advCombos}
                keyField={(c: any) => `${c.symbol}-${c.timeframe}`}
                titleColumn="symbol"
              />
            </div>

            {advFirstOk?.metrics?.regime_performance && (
              <div style={{ marginTop: 14 }}>
                <AutoCardTable
                  columns={[
                    { key: "regime", label: `Regime (${advFirstOk.symbol} ${advFirstOk.timeframe})`, render: ([regime]: [string, any]) => regime },
                    { key: "trades", label: "Trades", render: ([, s]: [string, any]) => s.trades },
                    { key: "winRate", label: "Win Rate", render: ([, s]: [string, any]) => fmtPct(s.win_rate, 1) },
                    { key: "totalPnl", label: "Total PnL", render: ([, s]: [string, any]) => <span className={s.total_pnl >= 0 ? "green" : "red"}>{fmtNum(s.total_pnl, 2)}</span> },
                  ]}
                  rows={Object.entries(advFirstOk.metrics.regime_performance)}
                  keyField={([regime]) => regime}
                  titleColumn="regime"
                />
              </div>
            )}

            {advRun.summary?.timeframe_performance && Object.keys(advRun.summary.timeframe_performance).length > 1 && (
              <div style={{ marginTop: 14 }}>
                <AutoCardTable
                  columns={[
                    { key: "tf", label: "Timeframe", render: ([tf]: [string, any]) => tf },
                    { key: "avgReturn", label: "Avg Return", render: ([, m]: [string, any]) => fmtPct(m?.total_return_pct, 2) },
                    { key: "avgSharpe", label: "Avg Sharpe", render: ([, m]: [string, any]) => fmtNum(m?.sharpe_ratio, 2) },
                    { key: "avgWinRate", label: "Avg Win Rate", render: ([, m]: [string, any]) => fmtPct(m?.win_rate, 1) },
                    { key: "avgMaxDd", label: "Avg Max DD", render: ([, m]: [string, any]) => fmtPct(m?.max_drawdown_pct, 2) },
                  ]}
                  rows={Object.entries(advRun.summary.timeframe_performance)}
                  keyField={([tf]) => tf}
                  titleColumn="tf"
                />
              </div>
            )}

            {advFirstOk?.strategy_contribution && (
              <div style={{ marginTop: 14 }}>
                <AutoCardTable
                  columns={[
                    { key: "name", label: "Strategy Contribution", render: ([name]: [string, any]) => name },
                    { key: "trades", label: "Trades", render: ([, m]: [string, any]) => (m?.error ? <span className="tile-label">{m.error}</span> : m?.total_trades ?? 0) },
                    { key: "return", label: "Return", render: ([, m]: [string, any]) => (m?.error ? null : fmtPct(m?.total_return_pct, 2)) },
                    { key: "winRate", label: "Win Rate", render: ([, m]: [string, any]) => (m?.error ? null : fmtPct(m?.win_rate, 1)) },
                    { key: "profitFactor", label: "Profit Factor", render: ([, m]: [string, any]) => (m?.error ? null : fmtNum(m?.profit_factor, 2)) },
                    { key: "sharpe", label: "Sharpe", render: ([, m]: [string, any]) => (m?.error ? null : fmtNum(m?.sharpe_ratio, 2)) },
                  ]}
                  rows={Object.entries(advFirstOk.strategy_contribution)}
                  keyField={([name]) => name}
                  titleColumn="name"
                />
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
          <AutoCardTable
            columns={[
              { key: "symbol", label: "Symbol", render: (r: any) => r.symbol },
              { key: "tf", label: "TF", render: (r: any) => r.timeframe },
              { key: "riskOfRuin", label: "Risk of Ruin", render: (r: any) => (r.ok !== false ? fmtPct(r.risk_of_ruin_pct, 2) : <span className="tile-label">{r.error}</span>) },
              { key: "worstDd", label: "Worst DD", render: (r: any) => (r.ok !== false ? fmtPct(r.worst_drawdown_pct, 2) : null) },
              { key: "medianFinal", label: "Median Final", render: (r: any) => (r.ok !== false ? fmtNum(r.median_final_balance, 0) : null) },
              {
                key: "ci90",
                label: "90% CI",
                render: (r: any) =>
                  r.ok === false
                    ? null
                    : r.final_balance_confidence_interval_90pct
                    ? `${fmtNum(r.final_balance_confidence_interval_90pct[0], 0)} – ${fmtNum(r.final_balance_confidence_interval_90pct[1], 0)}`
                    : "—",
              },
            ]}
            rows={advMonteCarlo.results || []}
            keyField={(r: any) => `${r.symbol}-${r.timeframe}`}
            titleColumn="symbol"
          />
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
            <AutoCardTable
              columns={[
                { key: "side", label: "Side", render: (t: any) => t.side },
                { key: "entry", label: "Entry", render: (t: any) => fmtNum(t.entry_price, 4) },
                { key: "exit", label: "Exit", render: (t: any) => fmtNum(t.exit_price, 4) },
                { key: "entryTime", label: "Entry Time", render: (t: any) => <LocalTime value={t.entry_time} label="Entry" /> },
                { key: "exitTime", label: "Exit Time", render: (t: any) => <LocalTime value={t.exit_time} label="Exit" /> },
                { key: "pnl", label: "PnL", render: (t: any) => <span className={t.pnl >= 0 ? "green" : "red"}>{fmtNum(t.pnl, 2)}</span> },
                { key: "r", label: "R", render: (t: any) => fmtNum(t.r_multiple, 2) },
                { key: "reason", label: "Reason", render: (t: any) => t.exit_reason },
              ]}
              rows={run.trades.slice(0, 200).map((t: any, i: number) => ({ ...t, _idx: i }))}
              keyField={(t: any) => t._idx}
              titleColumn="side"
              emptyMessage="No trades were generated for this configuration."
            />
          </Card>
        </>
      )}

      {labCompare?.ok && (
        <Card title="Strategy Comparison" full right={<GitCompare size={16} />}>
          <AutoCardTable
            columns={[
              { key: "strategy", label: "Strategy", render: (r: any) => STRATEGIES.find((s) => s.value === r.strategy)?.label || r.strategy },
              { key: "totalReturn", label: "Total Return", render: (r: any) => (r.ok ? fmtPct(r.metrics?.total_return_pct, 2) : <span className="tile-label">{r.error || "Not available"}</span>) },
              { key: "sharpe", label: "Sharpe", render: (r: any) => (r.ok ? fmtNum(r.metrics?.sharpe_ratio, 2) : null) },
              { key: "winRate", label: "Win Rate", render: (r: any) => (r.ok ? fmtPct(r.metrics?.win_rate, 1) : null) },
              { key: "profitFactor", label: "Profit Factor", render: (r: any) => (r.ok ? fmtNum(r.metrics?.profit_factor, 2) : null) },
              { key: "maxDrawdown", label: "Max Drawdown", render: (r: any) => (r.ok ? fmtPct(r.metrics?.max_drawdown_pct, 2) : null) },
              { key: "trades", label: "Trades", render: (r: any) => (r.ok ? r.metrics?.total_trades ?? 0 : null) },
            ]}
            rows={labCompare.results}
            keyField={(r: any) => r.strategy}
            titleColumn="strategy"
          />
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
          <div style={{ marginTop: 12 }}>
            <AutoCardTable
              columns={[
                { key: "window", label: "Window", render: (w: any) => w.window },
                { key: "totalReturn", label: "Total Return", render: (w: any) => fmtPct(w.metrics?.total_return_pct, 2) },
                { key: "sharpe", label: "Sharpe", render: (w: any) => fmtNum(w.metrics?.sharpe_ratio, 2) },
                { key: "winRate", label: "Win Rate", render: (w: any) => fmtPct(w.metrics?.win_rate, 1) },
                { key: "trades", label: "Trades", render: (w: any) => w.metrics?.total_trades ?? 0 },
              ]}
              rows={labWalkForward.windows}
              keyField={(w: any) => w.window}
              titleColumn="window"
            />
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
          <div style={{ marginTop: 14 }}>
            <AutoCardTable
              columns={[
                { key: "slMult", label: "SL Mult", render: (r: any) => fmtNum(r.atr_sl_mult, 1) },
                { key: "tpMult", label: "TP Mult", render: (r: any) => fmtNum(r.atr_tp_mult, 1) },
                { key: "entryThreshold", label: "Entry Threshold", render: (r: any) => fmtNum(r.entry_confidence_threshold, 0) },
                { key: "score", label: "Score", render: (r: any) => fmtNum(r.score, 3) },
                {
                  key: "totalReturn",
                  label: "Total Return",
                  render: (r: any) => <span className={(r.metrics?.total_return_pct ?? 0) >= 0 ? "green" : "red"}>{fmtPct(r.metrics?.total_return_pct, 2)}</span>,
                },
                { key: "sharpe", label: "Sharpe", render: (r: any) => fmtNum(r.metrics?.sharpe_ratio, 2) },
                { key: "winRate", label: "Win Rate", render: (r: any) => fmtPct(r.metrics?.win_rate, 1) },
                { key: "maxDrawdown", label: "Max Drawdown", render: (r: any) => fmtPct(r.metrics?.max_drawdown_pct, 2) },
                { key: "trades", label: "Trades", render: (r: any) => r.metrics?.total_trades ?? 0 },
              ]}
              rows={(optResult.results || []).slice(0, 20).map((r: any, i: number) => ({ ...r, _idx: i }))}
              keyField={(r: any) => r._idx}
              titleColumn="score"
            />
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
                <b className="tile-value"><LocalTime value={learningPerformance.evaluated_at} label="Evaluated" /></b>
              </div>
            </div>

            {learningPerformance.by_timeframe && Object.keys(learningPerformance.by_timeframe).length > 0 && (
              <div style={{ marginTop: 14 }}>
                <AutoCardTable
                  columns={[
                    { key: "tf", label: "Timeframe", render: ([tf]: [string, any]) => tf },
                    { key: "predictions", label: "Predictions", render: ([, s]: [string, any]) => s.predictions },
                    { key: "hitRate", label: "Hit Rate", render: ([, s]: [string, any]) => <span className={s.hit_rate_pct >= 50 ? "green" : "red"}>{fmtPct(s.hit_rate_pct, 1)}</span> },
                    { key: "avgError", label: "Avg Error", render: ([, s]: [string, any]) => fmtPct(s.avg_error_pct, 2) },
                    { key: "avgConfidence", label: "Avg Confidence", render: ([, s]: [string, any]) => fmtNum(s.avg_confidence, 1) },
                  ]}
                  rows={Object.entries(learningPerformance.by_timeframe)}
                  keyField={([tf]) => tf}
                  titleColumn="tf"
                />
              </div>
            )}

            {(learningPerformance.confidence_reliability || []).some((b: any) => b.predictions > 0) && (
              <div style={{ marginTop: 14 }}>
                <AutoCardTable
                  columns={[
                    { key: "bucket", label: "Confidence Bucket", render: (b: any) => b.bucket },
                    { key: "predictions", label: "Predictions", render: (b: any) => b.predictions },
                    { key: "hitRate", label: "Actual Hit Rate", render: (b: any) => fmtPct(b.hit_rate_pct, 1) },
                    { key: "statedConfidence", label: "Stated Confidence", render: (b: any) => fmtNum(b.avg_confidence, 1) },
                  ]}
                  rows={(learningPerformance.confidence_reliability || []).filter((b: any) => b.predictions > 0)}
                  keyField={(b: any) => b.bucket}
                  titleColumn="bucket"
                />
              </div>
            )}
          </>
        ) : (
          <p className="analytics-empty">
            No evaluation yet — hit "Evaluate Prediction History" to score stored predictions against recorded outcomes.
          </p>
        )}

        {learningWeights?.live_weights && (
          <div style={{ marginTop: 14 }}>
            <AutoCardTable
              columns={[
                { key: "strategy", label: "Strategy", render: (name: string) => name },
                { key: "liveWeight", label: "Live Weight", render: (name: string) => fmtNum(learningWeights.live_weights[name], 3) },
                { key: "candidateWeight", label: "Candidate Weight (shadow)", render: (name: string) => fmtNum(learningWeights.candidate_weights?.[name], 3) },
              ]}
              rows={Object.keys(learningWeights.live_weights)}
              keyField={(name) => name}
              titleColumn="strategy"
            />
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
        <AutoCardTable
          columns={[
            { key: "strategy", label: "Strategy", render: (e: any) => STRATEGIES.find((s) => s.value === e.strategy)?.label || e.strategy },
            { key: "symbol", label: "Symbol", render: (e: any) => e.symbol },
            { key: "timeframe", label: "Timeframe", render: (e: any) => e.timeframe },
            { key: "totalReturn", label: "Total Return", render: (e: any) => fmtPct(e.results?.metrics?.total_return_pct, 2) },
            { key: "sharpe", label: "Sharpe", render: (e: any) => fmtNum(e.results?.metrics?.sharpe_ratio, 2) },
            { key: "trades", label: "Trades", render: (e: any) => e.results?.metrics?.total_trades ?? 0 },
            { key: "created", label: "Created", render: (e: any) => <LocalTime value={e.created_at} label="Created" /> },
          ]}
          rows={labExperiments || []}
          keyField={(e: any) => e.experiment_id}
          titleColumn="symbol"
          emptyMessage="No backtests run yet - configure one above and hit Run."
        />
      </Card>
    </div>
  );
}
