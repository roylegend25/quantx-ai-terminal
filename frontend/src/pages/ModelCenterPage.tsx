import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  Award,
  BrainCircuit,
  Crown,
  Download,
  FlaskConical,
  Gauge,
  LineChart as LineChartIcon,
  PlayCircle,
  RadioTower,
  RotateCw,
  Settings2,
  Square,
  Trash2,
} from "lucide-react";
import Card from "../components/Layout/Card";
import { fmtNum, fmtPct } from "../lib/format";
import type { AppData } from "../hooks/useAppData";
import { useMLLab, type Algorithm, type MLJob } from "../components/MLLab/useMLLab";
import {
  EmptySlate,
  KpiTile,
  ProgressBar,
  SkeletonTiles,
  StatusPill,
  fmtAgo,
  fmtBytes,
  fmtDurationS,
} from "../components/MLLab/widgets";
import {
  ConfusionMatrix,
  CurvePanel,
  DriftHistoryChart,
  HpoHistoryChart,
  ImportancePanel,
  MetricHistoryChart,
  TrainingCurveChart,
  C,
} from "../components/MLLab/charts";

const TABS = [
  { id: "overview", label: "Overview", icon: Gauge },
  { id: "train", label: "Training", icon: PlayCircle },
  { id: "models", label: "Models", icon: Crown },
  { id: "analytics", label: "Analytics", icon: LineChartIcon },
  { id: "monitoring", label: "Monitoring", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings2 },
] as const;

type TabId = (typeof TABS)[number]["id"];

const pct01 = (v?: number | null) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—");

export default function ModelCenterPage(props: AppData) {
  const { showToast } = props;
  const lab = useMLLab(true);
  const [tab, setTab] = useState<TabId>("overview");
  const [busy, setBusy] = useState<string | null>(null);

  async function act(key: string, fn: () => Promise<unknown>, okMsg?: string) {
    setBusy(key);
    try {
      await fn();
      if (okMsg) showToast(okMsg, "success");
    } catch (e: any) {
      showToast(e?.message || "Action failed", "error");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="page-grid mll-page">
      <Card title="AI Model Laboratory" full right={<BrainCircuit size={16} className="cyan" />}>
        <div className="mll-tabs">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
              <Icon size={14} /> {label}
              {id === "train" && lab.hasActiveJob ? <span className="mll-live-dot" /> : null}
            </button>
          ))}
        </div>

        {lab.error ? (
          <EmptySlate title="AI Lab unavailable" reason={lab.error} />
        ) : (
          <AnimatePresence mode="wait">
            <motion.div
              key={tab}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.18 }}
            >
              {tab === "overview" && <OverviewTab lab={lab} />}
              {tab === "train" && <TrainTab lab={lab} busy={busy} act={act} />}
              {tab === "models" && <ModelsTab lab={lab} busy={busy} act={act} setTab={setTab} />}
              {tab === "analytics" && <AnalyticsTab lab={lab} />}
              {tab === "monitoring" && <MonitoringTab lab={lab} busy={busy} act={act} />}
              {tab === "settings" && <SettingsTab lab={lab} busy={busy} act={act} />}
            </motion.div>
          </AnimatePresence>
        )}
      </Card>
    </div>
  );
}

// ================================================================ overview

function OverviewTab({ lab }: { lab: ReturnType<typeof useMLLab> }) {
  const champion = lab.overview?.champion;
  const datasets = lab.overview?.datasets;
  const explanation = lab.explanation;

  if (lab.loading) return <SkeletonTiles count={12} />;

  return (
    <div className="mll-stack">
      <div className="mll-section-title"><Crown size={14} className="yellow" /> Champion Model</div>
      {champion ? (
        <div className="mll-kpi-grid">
          <KpiTile label="Champion" value={champion.model_name} sub={champion.algorithm} tone="cyan" />
          <KpiTile label="Version" value={champion.version} sub={`promoted ${fmtAgo(champion.promoted_at)}`} />
          <KpiTile label="Trained" value={fmtAgo(champion.trained_at)} sub={champion.trained_at?.slice(0, 10)} />
          <KpiTile label="Dataset Size" value={champion.training_samples ?? "—"} sub={champion.dataset_source || "pre-lab pipeline"} />
          <KpiTile label="Holdout Trades" value={champion.total_trades ?? "—"} sub="threshold-crossing signals" />
          <KpiTile label="Win Rate" value={champion.win_rate != null ? fmtPct(champion.win_rate, 1) : "—"} tone={champion.win_rate >= 50 ? "green" : "red"} />
          <KpiTile label="Accuracy" value={pct01(champion.val_accuracy ?? champion.train_accuracy)} />
          <KpiTile label="Precision" value={pct01(champion.precision)} />
          <KpiTile label="Recall" value={pct01(champion.recall)} />
          <KpiTile label="F1 Score" value={pct01(champion.f1)} />
          <KpiTile label="ROC AUC" value={champion.roc_auc != null ? fmtNum(champion.roc_auc, 3) : "—"} />
          <KpiTile label="Profit Factor" value={champion.profit_factor != null ? fmtNum(champion.profit_factor, 2) : "—"} tone={champion.profit_factor >= 1 ? "green" : "red"} />
          <KpiTile label="Sharpe" value={champion.sharpe_ratio != null ? fmtNum(champion.sharpe_ratio, 2) : "—"} />
          <KpiTile label="Avg Confidence" value={pct01(champion.avg_confidence)} />
          <KpiTile label="Avg Pred. Error" value={champion.avg_prediction_error != null ? fmtNum(champion.avg_prediction_error, 4) : "—"} sub="mean |P(up) − outcome|" />
          <KpiTile label="Model Size" value={fmtBytes(champion.model_size_bytes)} />
          <KpiTile label="Training Time" value={fmtDurationS(champion.training_duration_seconds)} sub={`peak RAM ${champion.peak_memory_mb != null ? `${champion.peak_memory_mb} MB` : "—"}`} />
          <KpiTile label="Hardware" value={champion.gpu_info || "CPU"} sub={champion.cpu_info || "—"} />
        </div>
      ) : (
        <EmptySlate
          title="No Champion promoted yet"
          reason="Train a challenger in the Training tab — it auto-promotes when it clears every configured promotion rule, or promote manually from the Models tab."
        />
      )}

      <div className="mll-section-title"><FlaskConical size={14} className="cyan" /> Training Datasets</div>
      <div className="mll-two-col">
        <div className="mll-panel">
          <div className="mll-panel-title">Market History <StatusPill status={datasets?.market_history?.available ? "healthy" : "critical"} /></div>
          {datasets?.market_history?.available ? (
            <div className="mll-mini-grid">
              <span>Rows <b>{datasets.market_history.rows}</b></span>
              <span>Features <b>{datasets.market_history.features}</b></span>
              <span>Symbols <b>{datasets.market_history.symbols?.join(", ")}</b></span>
              <span>Label balance <b>{fmtPct((datasets.market_history.label_balance ?? 0) * 100, 1)} up</b></span>
            </div>
          ) : (
            <p className="mll-dim">{datasets?.market_history?.reason || "Loading dataset info…"}</p>
          )}
          <p className="mll-dim" style={{ marginTop: 6 }}>
            Real Binance klines, features from the live indicator pipeline, labeled by realized forward returns.
          </p>
        </div>
        <div className="mll-panel">
          <div className="mll-panel-title">
            Trade Outcomes{" "}
            <StatusPill status={(datasets?.trade_outcomes?.labeled ?? 0) >= (datasets?.trade_outcomes?.min_required ?? 30) ? "healthy" : "warning"} />
          </div>
          <div className="mll-mini-grid">
            <span>Predictions <b>{datasets?.trade_outcomes?.samples ?? "—"}</b></span>
            <span>Labeled <b>{datasets?.trade_outcomes?.labeled ?? 0} / {datasets?.trade_outcomes?.min_required ?? 30} needed</b></span>
            <span>Wins <b className="green">{datasets?.trade_outcomes?.wins ?? 0}</b></span>
            <span>Losses <b className="red">{datasets?.trade_outcomes?.losses ?? 0}</b></span>
          </div>
          <p className="mll-dim" style={{ marginTop: 6 }}>
            Grows as paper trades resolve real predictions — the long-term ground truth set.
          </p>
        </div>
      </div>

      <div className="mll-section-title"><BrainCircuit size={14} className="purple" /> Latest Prediction Explained</div>
      {explanation?.available ? (
        <div className="mll-two-col">
          <div className="mll-panel">
            <div className="mll-panel-title">
              {explanation.symbol} {explanation.timeframe} · <StatusPill status={explanation.direction} />
              <span className="mll-dim" style={{ marginLeft: 8 }}>{fmtAgo(explanation.timestamp)}</span>
            </div>
            <div className="mll-mini-grid">
              <span>Confidence <b>{fmtPct(explanation.confidence, 1)}</b></span>
              <span>Regime <b>{explanation.regime || "—"}</b></span>
              <span>P(up) <b>{fmtPct(explanation.probability_distribution?.up, 1)}</b></span>
              <span>P(down) <b>{fmtPct(explanation.probability_distribution?.down, 1)}</b></span>
            </div>
            <p className="mll-dim" style={{ marginTop: 8 }}>{explanation.decision_reason || "No strategy gave a qualifying signal."}</p>
          </div>
          <div className="mll-panel">
            <div className="mll-panel-title">ML Champion Attribution</div>
            {explanation.ml_attribution?.available ? (
              <ul className="mll-reasons">
                {explanation.ml_attribution.top_reasons.slice(0, 6).map((r: string, i: number) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            ) : (
              <p className="mll-dim">{explanation.ml_attribution?.reason}</p>
            )}
          </div>
        </div>
      ) : (
        <EmptySlate title="No prediction to explain" reason={explanation?.reason || "Waiting for the first stored prediction."} />
      )}
    </div>
  );
}

// ================================================================== train

function TrainTab({ lab, busy, act }: { lab: ReturnType<typeof useMLLab>; busy: string | null; act: (k: string, fn: () => Promise<unknown>, ok?: string) => Promise<void> }) {
  const [symbols, setSymbols] = useState<string[]>(["BTCUSDT", "ETHUSDT"]);
  const [timeframe, setTimeframe] = useState("1h");
  const [bars, setBars] = useState(1500);
  const [horizon, setHorizon] = useState(4);
  const [dataset, setDataset] = useState("market_history");

  const activeJobs = lab.jobs.filter((j) => j.status === "running" || j.status === "queued");
  const recentJobs = lab.jobs.filter((j) => j.status !== "running" && j.status !== "queued").slice(0, 8);

  return (
    <div className="mll-stack">
      <div className="mll-panel">
        <div className="mll-panel-title">Dataset for new training runs</div>
        <div className="mll-form-row">
          <label>
            Source
            <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
              <option value="market_history">Market history (klines + forward returns)</option>
              <option value="trade_outcomes">Trade outcomes (resolved paper trades)</option>
            </select>
          </label>
          <label>
            Symbols
            <select
              value={symbols.join(",")}
              onChange={(e) => setSymbols(e.target.value.split(","))}
              disabled={dataset !== "market_history"}
            >
              <option value="BTCUSDT,ETHUSDT">BTC + ETH</option>
              <option value="BTCUSDT">BTCUSDT</option>
              <option value="ETHUSDT">ETHUSDT</option>
            </select>
          </label>
          <label>
            Timeframe
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} disabled={dataset !== "market_history"}>
              {["15m", "30m", "1h", "4h"].map((tf) => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </label>
          <label>
            Bars / symbol
            <select value={bars} onChange={(e) => setBars(Number(e.target.value))} disabled={dataset !== "market_history"}>
              {[700, 1000, 1500].map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
          <label>
            Label horizon
            <select value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} disabled={dataset !== "market_history"}>
              {[1, 2, 4, 8, 12].map((h) => <option key={h} value={h}>{h} bars</option>)}
            </select>
          </label>
        </div>
      </div>

      {activeJobs.length > 0 && (
        <div className="mll-panel mll-panel-live">
          <div className="mll-panel-title"><RadioTower size={14} className="cyan" /> Live Training</div>
          {activeJobs.map((job) => <JobProgress key={job.job_id} job={job} busy={busy} act={act} cancel={lab.cancelJob} />)}
        </div>
      )}

      <div className="mll-section-title"><PlayCircle size={14} className="green" /> One-Click Training</div>
      <div className="mll-algo-grid">
        {lab.algorithms.map((algo) => (
          <AlgoCard
            key={algo.id}
            algo={algo}
            busy={busy}
            disabled={busy !== null}
            onTrain={() =>
              act(
                `train-${algo.id}`,
                () => lab.train({ algorithm: algo.id, dataset, symbols, timeframe, bars, horizon }),
                `${algo.label} training queued`
              )
            }
          />
        ))}
      </div>

      <div className="mll-section-title">Recent Jobs</div>
      {recentJobs.length === 0 ? (
        <EmptySlate title="No completed jobs yet" reason="Launch any algorithm above — every run appears here with its full result or failure reason." />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>Job</th><th>Kind</th><th>Algorithm</th><th>Status</th><th>Result</th><th>Finished</th></tr>
            </thead>
            <tbody>
              {recentJobs.map((j) => (
                <tr key={j.job_id}>
                  <td className="mll-mono">{j.job_id}</td>
                  <td>{j.kind}</td>
                  <td>{j.algorithm}</td>
                  <td><StatusPill status={j.status} /></td>
                  <td className="mll-dim">
                    {j.status === "succeeded"
                      ? j.kind === "hpo"
                        ? `best ${fmtNum(j.result?.best_value, 4)} over ${j.result?.trials_run} trials`
                        : `${j.result?.version} · acc ${pct01(j.result?.metrics?.accuracy)}${j.result?.promotion?.promoted ? " · PROMOTED" : ""}`
                      : j.error || "—"}
                  </td>
                  <td>{fmtAgo(j.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AlgoCard({ algo, busy, disabled, onTrain }: { algo: Algorithm; busy: string | null; disabled: boolean; onTrain: () => void }) {
  return (
    <motion.div
      className={`mll-algo${algo.available ? "" : " unavailable"}`}
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
      title={algo.available ? algo.description || undefined : algo.unavailable_reason || undefined}
    >
      <div className="mll-algo-head">
        <b>{algo.label}</b>
        <span className="mll-dim">{algo.family.replace(/_/g, " ")}</span>
      </div>
      {algo.available ? (
        <>
          <div className="mll-algo-tags">
            {algo.supports_shap && <span className="mll-tag">native SHAP</span>}
            {algo.family === "deep_learning" && <span className="mll-tag">Keras / CPU</span>}
          </div>
          <button className="mll-train-btn" disabled={disabled} onClick={onTrain}>
            {busy === `train-${algo.id}` ? "Queuing…" : `Train ${algo.label}`}
          </button>
        </>
      ) : (
        <p className="mll-unavailable-reason"><AlertTriangle size={12} /> {algo.unavailable_reason}</p>
      )}
    </motion.div>
  );
}

function JobProgress({ job, busy, act, cancel }: { job: MLJob; busy: string | null; act: any; cancel: (id: string) => Promise<unknown> }) {
  const p = job.progress || {};
  const pct = typeof p.pct === "number" ? p.pct : 0;
  return (
    <div className="mll-job">
      <div className="mll-job-head">
        <b>{job.algorithm}</b>
        <span className="mll-dim">{job.kind === "hpo" ? "hyperparameter search" : "training"} · {p.stage || job.status}</span>
        <StatusPill status={job.status} />
        <button
          className="mll-cancel"
          disabled={busy !== null}
          onClick={() => act(`cancel-${job.job_id}`, () => cancel(job.job_id), "Job cancelled")}
        >
          <Square size={11} /> Cancel
        </button>
      </div>
      <ProgressBar pct={pct} tone={job.status === "running" ? "cyan" : "green"} />
      <div className="mll-job-stats">
        {p.epoch != null && <span>{job.kind === "hpo" ? "Trial" : "Epoch"} <b>{p.epoch ?? p.trial}/{p.total_epochs ?? p.total_trials ?? "?"}</b></span>}
        {p.trial != null && p.epoch == null && <span>Trial <b>{p.trial}/{p.total_trials ?? "?"}</b></span>}
        {p.loss != null && <span>Loss <b>{fmtNum(p.loss, 4)}</b></span>}
        {p.val_loss != null && <span>Val Loss <b>{fmtNum(p.val_loss, 4)}</b></span>}
        {p.accuracy != null && <span>Acc <b>{fmtNum(p.accuracy * 100, 1)}%</b></span>}
        {p.best_value != null && <span>Best <b>{fmtNum(p.best_value, 4)}</b></span>}
        {p.learning_rate != null && <span>LR <b>{p.learning_rate}</b></span>}
        {p.eta_seconds != null && <span>ETA <b>{fmtDurationS(p.eta_seconds)}</b></span>}
        {p.samples_per_sec != null && <span>Samples/s <b>{fmtNum(p.samples_per_sec, 0)}</b></span>}
        {p.cpu_pct != null && <span>CPU <b>{fmtNum(p.cpu_pct, 0)}%</b></span>}
        {p.ram_mb != null && <span>RAM <b>{fmtNum(p.ram_mb, 0)} MB</b></span>}
        <span>GPU <b>{p.gpu_pct != null ? `${p.gpu_pct}%` : "n/a"}</b></span>
      </div>
      {p.message && <p className="mll-dim" style={{ margin: "4px 0 0" }}>{p.message}</p>}
    </div>
  );
}

// ================================================================= models

function ModelsTab({ lab, busy, act, setTab }: { lab: ReturnType<typeof useMLLab>; busy: string | null; act: any; setTab: (t: TabId) => void }) {
  const champion = lab.compare.find((r) => r.role === "champion");
  const challengers = lab.compare.filter((r) => r.role === "challenger");

  const METRIC_ROWS: Array<[string, (m: any) => string]> = [
    ["Accuracy", (m) => pct01(m.val_accuracy ?? m.train_accuracy)],
    ["Win Rate", (m) => (m.win_rate != null ? fmtPct(m.win_rate, 1) : "—")],
    ["Sharpe", (m) => (m.sharpe_ratio != null ? fmtNum(m.sharpe_ratio, 2) : "—")],
    ["Profit Factor", (m) => (m.profit_factor != null ? fmtNum(m.profit_factor, 2) : "—")],
    ["Trades", (m) => (m.total_trades != null ? String(m.total_trades) : "—")],
    ["Max Drawdown", (m) => (m.max_drawdown_pct != null ? fmtPct(m.max_drawdown_pct, 1) : "—")],
    ["Precision", (m) => pct01(m.precision)],
    ["Recall", (m) => pct01(m.recall)],
    ["F1", (m) => pct01(m.f1)],
    ["ROC AUC", (m) => (m.roc_auc != null ? fmtNum(m.roc_auc, 3) : "—")],
    ["Avg Confidence", (m) => pct01(m.avg_confidence)],
    ["Pred. Error", (m) => (m.avg_prediction_error != null ? fmtNum(m.avg_prediction_error, 4) : "—")],
    ["Latency", (m) => (m.latency_ms != null ? `${fmtNum(m.latency_ms, 3)} ms` : "—")],
    ["Inference", (m) => (m.inference_rows_per_sec != null ? `${fmtNum(m.inference_rows_per_sec, 0)}/s` : "—")],
    ["Training Time", (m) => fmtDurationS(m.training_duration_seconds)],
    ["Memory", (m) => (m.peak_memory_mb != null ? `${fmtNum(m.peak_memory_mb, 0)} MB` : "—")],
    ["Size", (m) => fmtBytes(m.model_size_bytes)],
  ];

  return (
    <div className="mll-stack">
      <div className="mll-section-title"><Award size={14} className="cyan" /> Challengers</div>
      {challengers.length === 0 ? (
        <EmptySlate title="No challengers pending" reason="Every new training run registers as a Challenger. Launch one from the Training tab." />
      ) : (
        <div className="mll-challenger-grid">
          {challengers.map((m) => (
            <div className="mll-panel" key={m.model_id}>
              <div className="mll-panel-title">
                {m.model_name} <span className="mll-dim">{m.version}</span> <StatusPill status={m.status} />
              </div>
              <div className="mll-mini-grid">
                <span>Accuracy <b>{pct01(m.val_accuracy ?? m.train_accuracy)}</b></span>
                <span>Return <b className={m.profit_factor >= 1 ? "green" : "red"}>{m.profit_factor != null ? `PF ${fmtNum(m.profit_factor, 2)}` : "—"}</b></span>
                <span>Sharpe <b>{m.sharpe_ratio != null ? fmtNum(m.sharpe_ratio, 2) : "—"}</b></span>
                <span>Win Rate <b>{m.win_rate != null ? fmtPct(m.win_rate, 1) : "—"}</b></span>
                <span>Age <b>{fmtAgo(m.trained_at)}</b></span>
                <span>Live est. <b>{pct01(m.val_accuracy)}</b></span>
              </div>
              {!m.promotion_check?.met && (
                <p className="mll-dim mll-promo-fails" title={m.promotion_check?.failures?.join("\n")}>
                  Blocked from auto-promotion: {m.promotion_check?.failures?.[0]}{(m.promotion_check?.failures?.length ?? 0) > 1 ? ` (+${m.promotion_check.failures.length - 1} more)` : ""}
                </p>
              )}
              <div className="mll-btn-row">
                <button disabled={busy !== null} onClick={() => act(`promote-${m.model_id}`, () => lab.promote(m.model_id, !m.promotion_check?.met), m.promotion_check?.met ? "Promoted" : "Force-promoted despite failing rules")}>
                  <Award size={12} /> Promote{m.promotion_check?.met ? "" : " (force)"}
                </button>
                <button disabled={busy !== null} onClick={() => { lab.loadModelDetail(m.model_id); setTab("analytics"); }}>
                  <LineChartIcon size={12} /> Compare / Inspect
                </button>
                <button className="danger" disabled={busy !== null} onClick={() => act(`delete-${m.model_id}`, () => lab.deleteModel(m.model_id), "Model archived + file removed")}>
                  <Trash2 size={12} /> Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="mll-section-title">Champion vs Challengers</div>
      {lab.compare.length === 0 ? (
        <EmptySlate title="Nothing to compare" reason="Train at least one model to populate the comparison table." />
      ) : (
        <div className="table-wrap">
          <table className="data-table mll-compare">
            <thead>
              <tr>
                <th>Metric</th>
                {champion && <th className="mll-champ-col"><Crown size={12} /> {champion.model_name} {champion.version}</th>}
                {challengers.map((m) => <th key={m.model_id}>{m.model_name} {m.version}</th>)}
              </tr>
            </thead>
            <tbody>
              {METRIC_ROWS.map(([label, fn]) => (
                <tr key={label}>
                  <td className="mll-dim">{label}</td>
                  {champion && <td className="mll-champ-col"><b>{fn(champion)}</b></td>}
                  {challengers.map((m) => <td key={m.model_id}>{fn(m)}</td>)}
                </tr>
              ))}
              <tr>
                <td className="mll-dim">Actions</td>
                {champion && (
                  <td className="mll-champ-col">
                    <div className="mll-btn-row">
                      <a className="mll-link" href={api_download(champion.model_id)} download><Download size={12} /> Export</a>
                      <button disabled={busy !== null} onClick={() => act("rollback", () => lab.rollback(champion.model_name), "Rolled back")}>
                        <RotateCw size={12} /> Rollback
                      </button>
                    </div>
                  </td>
                )}
                {challengers.map((m) => (
                  <td key={m.model_id}>
                    <div className="mll-btn-row">
                      <a className="mll-link" href={api_download(m.model_id)} download><Download size={12} /></a>
                      <button disabled={busy !== null} onClick={() => act(`promote2-${m.model_id}`, () => lab.promote(m.model_id, !m.promotion_check?.met), "Promoted")}>
                        <Award size={12} />
                      </button>
                    </div>
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}

      <div className="mll-section-title">Model Registry — every version</div>
      {lab.registry.length === 0 ? (
        <EmptySlate title="Registry is empty" reason="Every training run (including failed and archived versions) is recorded here permanently." />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>Model</th><th>Version</th><th>Status</th><th>Created</th><th>Accuracy</th><th>Dataset</th><th>Features</th><th>Params</th><th>Git</th><th>Size</th><th></th></tr>
            </thead>
            <tbody>
              {lab.registry.map((m) => (
                <tr key={m.model_id}>
                  <td><b>{m.model_name}</b><div className="tile-label">{m.algorithm}</div></td>
                  <td>{m.version}</td>
                  <td><StatusPill status={m.status} /></td>
                  <td>{fmtAgo(m.trained_at)}</td>
                  <td>{pct01(m.val_accuracy ?? m.train_accuracy)}</td>
                  <td className="mll-dim">{m.dataset_source || m.dataset_version || "—"}</td>
                  <td>{m.feature_list?.length ?? "—"}</td>
                  <td className="mll-dim" title={JSON.stringify(m.parameters, null, 2)}>{m.parameters ? `${Object.keys(m.parameters).length} set` : "—"}</td>
                  <td className="mll-mono">{m.git_commit || "n/a"}</td>
                  <td>{fmtBytes(m.model_size_bytes)}</td>
                  <td>
                    <div className="mll-btn-row">
                      {m.model_path && <a className="mll-link" title="Download" href={api_download(m.model_id)} download><Download size={12} /></a>}
                      {m.status === "Champion" ? (
                        <button title="Rollback to previous champion" disabled={busy !== null} onClick={() => act(`rb-${m.model_id}`, () => lab.rollback(m.model_name), "Rolled back")}><RotateCw size={12} /></button>
                      ) : (
                        <button className="danger" title="Archive + remove file" disabled={busy !== null || m.status === "Archived"} onClick={() => act(`del-${m.model_id}`, () => lab.deleteModel(m.model_id), "Archived")}><Trash2 size={12} /></button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function api_download(modelId: string): string {
  return `/api/ml/registry/${encodeURIComponent(modelId)}/download`;
}

// =============================================================== analytics

function AnalyticsTab({ lab }: { lab: ReturnType<typeof useMLLab> }) {
  const detail = lab.modelDetail;
  const withArtifacts = lab.registry.filter((m) => m.status !== "Failed");

  return (
    <div className="mll-stack">
      <div className="mll-form-row">
        <label>
          Model version
          <select
            value={detail?.model?.model_id || ""}
            onChange={(e) => e.target.value && lab.loadModelDetail(e.target.value)}
          >
            <option value="">Select a trained model…</option>
            {withArtifacts.map((m) => (
              <option key={m.model_id} value={m.model_id}>
                {m.model_name} {m.version} ({m.status})
              </option>
            ))}
          </select>
        </label>
      </div>

      {!detail && (
        <EmptySlate title="Pick a model version" reason={withArtifacts.length ? "Its holdout evaluation — SHAP, curves, confusion matrix, simulated trading — renders here." : "No trained versions exist yet. Train one first."} />
      )}
      {detail?.loading && <SkeletonTiles count={6} />}
      {detail?.error && <EmptySlate title="Failed to load" reason={detail.error} />}

      {detail?.model && (
        <>
          {detail.artifacts?.trading_simulation && (
            <div className="mll-kpi-grid">
              <KpiTile label="Sim Trades" value={detail.artifacts.trading_simulation.trades} sub={detail.artifacts.trading_simulation.reason} />
              <KpiTile label="Sim Win Rate" value={detail.artifacts.trading_simulation.win_rate != null ? fmtPct(detail.artifacts.trading_simulation.win_rate, 1) : "—"} tone={detail.artifacts.trading_simulation.win_rate >= 50 ? "green" : "red"} />
              <KpiTile label="Sim Return" value={detail.artifacts.trading_simulation.total_return_pct != null ? fmtPct(detail.artifacts.trading_simulation.total_return_pct, 1) : "—"} tone={detail.artifacts.trading_simulation.total_return_pct > 0 ? "green" : "red"} />
              <KpiTile label="Sim Sharpe" value={detail.artifacts.trading_simulation.sharpe_ratio != null ? fmtNum(detail.artifacts.trading_simulation.sharpe_ratio, 2) : "—"} />
              <KpiTile label="Sim Max DD" value={detail.artifacts.trading_simulation.max_drawdown_pct != null ? fmtPct(detail.artifacts.trading_simulation.max_drawdown_pct, 1) : "—"} />
              <KpiTile label="Expectancy" value={detail.artifacts.trading_simulation.expectancy_pct != null ? `${fmtNum(detail.artifacts.trading_simulation.expectancy_pct, 3)}%` : "—"} sub="per trade" />
            </div>
          )}

          <div className="mll-two-col">
            <div className="mll-panel">
              <div className="mll-panel-title">Feature Attribution</div>
              <ImportancePanel importance={detail.artifacts?.importance || null} sample={detail.artifacts?.shap_sample || null} />
            </div>
            <div className="mll-panel">
              <div className="mll-panel-title">Evaluation Curves</div>
              {detail.artifacts && Object.keys(detail.artifacts).length ? (
                <CurvePanel artifacts={detail.artifacts} />
              ) : (
                <EmptySlate title="No curves stored" reason="This version predates the AI Lab pipeline." />
              )}
            </div>
          </div>

          <div className="mll-two-col">
            <div className="mll-panel">
              <div className="mll-panel-title">Confusion Matrix (holdout)</div>
              {detail.artifacts?.confusion_matrix ? (
                <ConfusionMatrix cm={detail.artifacts.confusion_matrix} />
              ) : (
                <EmptySlate title="No confusion matrix" reason="This version predates the AI Lab pipeline." />
              )}
            </div>
            <div className="mll-panel">
              <div className="mll-panel-title">Training Curve</div>
              <TrainingCurveChart curve={detail.artifacts?.training_curve || []} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ============================================================== monitoring

function MonitoringTab({ lab, busy, act }: { lab: ReturnType<typeof useMLLab>; busy: string | null; act: any }) {
  const [historyMetric, setHistoryMetric] = useState<"accuracy_pct" | "scored" | "avg_confidence">("accuracy_pct");
  const drift = lab.drift || {};
  const liveTf = lab.live?.timeframes || {};
  const inferenceItems = lab.inference?.items || [];

  const DRIFT_LABELS: Record<string, string> = {
    feature: "Feature Drift",
    prediction: "Prediction Drift",
    market: "Market Drift",
    data: "Data Drift",
    concept: "Concept Drift",
  };
  const METHOD_LABELS: Record<string, string> = {
    PSI: "Population Stability Index",
    "PSI-mean": "PSI (all features)",
    KS: "KS Statistic",
    KL: "KL Divergence",
    Wasserstein: "Wasserstein Distance",
    "accuracy-drop": "Accuracy Drop",
  };

  return (
    <div className="mll-stack">
      <div className="mll-section-title">
        <Activity size={14} className="yellow" /> Drift Monitoring
        <button className="mll-inline-btn" disabled={busy !== null} onClick={() => act("scan", () => lab.scanDrift(), "Drift scan complete")}>
          {busy === "scan" ? "Scanning…" : "Run scan now"}
        </button>
      </div>
      <div className="mll-drift-grid">
        {Object.entries(DRIFT_LABELS).map(([type, label]) => {
          const entry = drift[type];
          const methods = entry?.methods || {};
          return (
            <div className="mll-panel" key={type}>
              <div className="mll-panel-title">{label} <StatusPill status={entry?.latest?.status || "no_data"} /></div>
              {Object.keys(methods).length === 0 ? (
                <p className="mll-dim">No scan recorded yet for this drift type.</p>
              ) : (
                <div className="mll-drift-methods">
                  {Object.entries(methods).map(([method, rec]: [string, any]) => (
                    <div key={method} className="mll-drift-method" title={`threshold ${rec.threshold}`}>
                      <span>{METHOD_LABELS[method] || method}</span>
                      <b className={rec.status === "healthy" ? "green" : rec.status === "warning" ? "yellow" : rec.status === "critical" ? "red" : ""}>
                        {rec.score != null ? fmtNum(rec.score, 4) : "n/a"}
                      </b>
                      <i className="mll-dim">thr {rec.threshold != null ? fmtNum(rec.threshold, 2) : "—"}</i>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mll-panel">
        <div className="mll-panel-title">Drift History (30 days)</div>
        <DriftHistoryChart series={lab.driftHistory?.series || []} />
      </div>

      <div className="mll-section-title">Live Accuracy by Timeframe</div>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr><th>Timeframe</th><th>Predictions</th><th>Directional Acc.</th><th>MAE</th><th>RMSE</th><th>MAPE</th><th>Calibration Err.</th><th>Profit if traded</th></tr>
          </thead>
          <tbody>
            {Object.entries(liveTf).map(([tf, m]: [string, any]) => (
              <tr key={tf}>
                <td><b>{tf}</b></td>
                <td>{m.predictions}</td>
                <td className={m.directional_accuracy_pct >= 50 ? "green" : m.predictions ? "red" : ""}>
                  {m.predictions ? fmtPct(m.directional_accuracy_pct, 1) : <span className="mll-dim" title={m.reason}>no data</span>}
                </td>
                <td>{m.mae != null ? fmtNum(m.mae, 2) : "—"}</td>
                <td>{m.rmse != null ? fmtNum(m.rmse, 2) : "—"}</td>
                <td>{m.mape_pct != null ? fmtPct(m.mape_pct, 2) : "—"}</td>
                <td>{m.calibration_error != null ? fmtNum(m.calibration_error, 3) : "—"}</td>
                <td className={(m.profit_if_traded_pct ?? 0) >= 0 ? "green" : "red"}>{m.profit_if_traded_pct != null ? fmtPct(m.profit_if_traded_pct, 2) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mll-section-title">
        Performance History
        <div className="mll-seg" style={{ marginLeft: 12 }}>
          {([["accuracy_pct", "Accuracy"], ["scored", "Volume"], ["avg_confidence", "Confidence"]] as const).map(([k, label]) => (
            <button key={k} className={historyMetric === k ? "active" : ""} onClick={() => setHistoryMetric(k)}>{label}</button>
          ))}
        </div>
      </div>
      {(lab.history?.live_accuracy || []).length ? (
        <MetricHistoryChart
          data={lab.history.live_accuracy}
          dataKey={historyMetric}
          color={historyMetric === "accuracy_pct" ? C.green : historyMetric === "scored" ? C.blue : C.yellow}
        />
      ) : (
        <EmptySlate title="No daily history yet" reason="Realized accuracy accumulates one point per day of stored predictions." />
      )}

      <div className="mll-section-title">
        Live Inference Monitor
        {lab.inference?.running_accuracy_pct != null && (
          <span className="mll-dim" style={{ marginLeft: 10, fontSize: 12 }}>
            running accuracy <b className={lab.inference.running_accuracy_pct >= 50 ? "green" : "red"}>{fmtPct(lab.inference.running_accuracy_pct, 1)}</b> over {lab.inference.scored} scored · {lab.inference.pending} pending
          </span>
        )}
      </div>
      {inferenceItems.length === 0 ? (
        <EmptySlate title="No predictions recorded" reason="Every live prediction lands here with its measured latency and later resolution." />
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>Time</th><th>Symbol</th><th>TF</th><th>Signal</th><th>Confidence</th><th>Latency</th><th>Outcome</th></tr>
            </thead>
            <tbody>
              {inferenceItems.slice(0, 25).map((it: any) => (
                <tr key={it.id}>
                  <td>{fmtAgo(it.timestamp)}</td>
                  <td>{it.symbol}</td>
                  <td>{it.timeframe}</td>
                  <td><StatusPill status={it.signal || "—"} /></td>
                  <td>{it.confidence != null ? fmtPct(it.confidence, 1) : "—"}</td>
                  <td>{it.latency_ms != null ? `${fmtNum(it.latency_ms, 0)} ms` : <span className="mll-dim" title="Recorded before latency tracking was added">n/a</span>}</td>
                  <td><StatusPill status={it.outcome} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ================================================================ settings

function SettingsTab({ lab, busy, act }: { lab: ReturnType<typeof useMLLab>; busy: string | null; act: any }) {
  const [promo, setPromo] = useState<any>(null);
  const [retrain, setRetrain] = useState<any>(null);
  const [hpoAlgo, setHpoAlgo] = useState("xgboost");
  const [hpoMethod, setHpoMethod] = useState("random");
  const [hpoTrials, setHpoTrials] = useState(20);
  const [hpoView, setHpoView] = useState(0);

  const promotion = promo ?? lab.settings?.promotion ?? null;
  const retraining = retrain ?? lab.settings?.retraining ?? null;
  const hpoResult = lab.hpoResults[hpoView];

  if (!lab.settings) return <SkeletonTiles count={6} />;

  const promoFields: Array<[string, string, number]> = [
    ["min_accuracy", "Minimum Accuracy (0-1)", 0.01],
    ["min_sharpe", "Minimum Sharpe", 0.1],
    ["min_profit_factor", "Minimum Profit Factor", 0.05],
    ["min_trades", "Minimum Trades", 1],
    ["max_drawdown_pct", "Maximum Drawdown %", 1],
    ["min_confidence", "Minimum Avg Confidence (0-1)", 0.05],
  ];

  return (
    <div className="mll-stack">
      <div className="mll-two-col">
        <div className="mll-panel">
          <div className="mll-panel-title">Automatic Promotion Rules</div>
          <p className="mll-dim">A challenger auto-promotes to Champion only when EVERY rule passes on its holdout evaluation.</p>
          <label className="mll-check">
            <input
              type="checkbox"
              checked={!!promotion?.auto_promote}
              onChange={(e) => setPromo({ ...promotion, auto_promote: e.target.checked })}
            />
            Auto-promote enabled
          </label>
          <div className="mll-form-grid">
            {promoFields.map(([key, label, step]) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  step={step}
                  value={promotion?.[key] ?? ""}
                  onChange={(e) => setPromo({ ...promotion, [key]: Number(e.target.value) })}
                />
              </label>
            ))}
          </div>
          <button
            className="mll-train-btn"
            disabled={busy !== null || !promo}
            onClick={() => act("save-promo", () => lab.saveSettings({ promotion: promo }).then(() => setPromo(null)), "Promotion rules saved")}
          >
            {busy === "save-promo" ? "Saving…" : "Save promotion rules"}
          </button>
        </div>

        <div className="mll-panel">
          <div className="mll-panel-title">Scheduled Retraining</div>
          <div className="mll-form-grid">
            <label>
              Schedule
              <select
                value={retraining?.schedule || "manual"}
                onChange={(e) => setRetrain({ ...retraining, schedule: e.target.value })}
              >
                <option value="manual">Manual only</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </label>
          </div>
          <label className="mll-check">
            <input
              type="checkbox"
              checked={!!retraining?.drift_trigger_enabled}
              onChange={(e) => setRetrain({ ...retraining, drift_trigger_enabled: e.target.checked })}
            />
            Auto-retrain when drift crosses threshold
          </label>
          <div className="mll-btn-row" style={{ marginTop: 10 }}>
            <button
              className="mll-train-btn"
              disabled={busy !== null || !retrain}
              onClick={() => act("save-retrain", () => lab.saveSettings({ retraining: retrain }).then(() => setRetrain(null)), "Retraining settings saved")}
            >
              {busy === "save-retrain" ? "Saving…" : "Save schedule"}
            </button>
            <button
              disabled={busy !== null}
              onClick={() => act("retrain-now", () => lab.retrainNow(), "Retraining jobs queued")}
            >
              Retrain now
            </button>
          </div>
          {lab.overview?.retrain_triggers?.due && (
            <p className="mll-dim" style={{ marginTop: 8 }}>
              <AlertTriangle size={12} className="yellow" /> Triggers currently firing:{" "}
              {lab.overview.retrain_triggers.reasons.map((r: any) => r.trigger).join(", ")}
            </p>
          )}
        </div>
      </div>

      <div className="mll-section-title"><FlaskConical size={14} className="purple" /> Hyperparameter Optimization</div>
      <div className="mll-panel">
        <div className="mll-form-row">
          <label>
            Algorithm
            <select value={hpoAlgo} onChange={(e) => setHpoAlgo(e.target.value)}>
              {lab.hpoSupported.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
          <label>
            Method
            <select value={hpoMethod} onChange={(e) => setHpoMethod(e.target.value)}>
              <option value="grid">Grid Search</option>
              <option value="random">Random Search</option>
              <option value="bayesian">Bayesian (Optuna TPE)</option>
            </select>
          </label>
          <label>
            Trials
            <select value={hpoTrials} onChange={(e) => setHpoTrials(Number(e.target.value))}>
              {[10, 20, 30, 40].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <button
            className="mll-train-btn"
            style={{ alignSelf: "flex-end" }}
            disabled={busy !== null}
            onClick={() => act("hpo", () => lab.startHpo({ algorithm: hpoAlgo, method: hpoMethod, n_trials: hpoTrials }), "HPO job queued — watch it in Training")}
          >
            {busy === "hpo" ? "Queuing…" : "Start optimization"}
          </button>
        </div>
      </div>

      {lab.hpoResults.length === 0 ? (
        <EmptySlate title="No optimization results yet" reason="Completed HPO runs persist here with every trial, the best parameters, and parameter importance." />
      ) : (
        <div className="mll-stack">
          <div className="mll-seg">
            {lab.hpoResults.slice(0, 6).map((r, i) => (
              <button key={r.job_id} className={hpoView === i ? "active" : ""} onClick={() => setHpoView(i)}>
                {r.algorithm} · {r.method} · {fmtAgo(r.created_at)}
              </button>
            ))}
          </div>
          {hpoResult && (
            <div className="mll-two-col">
              <div className="mll-panel">
                <div className="mll-panel-title">Best result — validation accuracy {fmtNum(hpoResult.best_value, 4)}</div>
                <pre className="mll-code">{JSON.stringify(hpoResult.best_params, null, 2)}</pre>
                {hpoResult.param_importance && (
                  <>
                    <div className="mll-panel-title" style={{ marginTop: 10 }}>Parameter importance (Optuna)</div>
                    {Object.entries(hpoResult.param_importance).map(([k, v]: [string, any]) => (
                      <div key={k} className="mll-param-imp">
                        <span>{k}</span>
                        <ProgressBar pct={v * 100} tone="green" />
                        <b>{fmtNum(v * 100, 1)}%</b>
                      </div>
                    ))}
                  </>
                )}
                <div className="mll-panel-title" style={{ marginTop: 10 }}>Optimization history</div>
                <HpoHistoryChart history={hpoResult.history || []} />
              </div>
              <div className="mll-panel">
                <div className="mll-panel-title">Top {Math.min(20, hpoResult.top_trials?.length ?? 0)} trials</div>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead><tr><th>#</th><th>Val. accuracy</th><th>Parameters</th></tr></thead>
                    <tbody>
                      {(hpoResult.top_trials || []).map((t: any) => (
                        <tr key={t.trial}>
                          <td>{t.trial}</td>
                          <td><b>{fmtNum(t.value, 4)}</b></td>
                          <td className="mll-mono mll-dim">{Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join("  ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
