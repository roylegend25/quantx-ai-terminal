import { useMemo, useState } from "react";
import {
  Brain,
  Dices,
  GitCompare,
  Lightbulb,
  Loader2,
  PlayCircle,
  SlidersHorizontal,
  Waves,
} from "lucide-react";
import Card from "../Layout/Card";
import { fmtNum, fmtPct, fmtRelativeTime } from "../../lib/format";

type TabKey = "optimizer" | "walkforward" | "montecarlo" | "models" | "importance" | "recommendations";

const TABS: { key: TabKey; label: string; icon: React.ReactNode }[] = [
  { key: "optimizer", label: "Optimizer", icon: <SlidersHorizontal size={14} /> },
  { key: "walkforward", label: "Walk-Forward", icon: <Waves size={14} /> },
  { key: "montecarlo", label: "Monte Carlo", icon: <Dices size={14} /> },
  { key: "models", label: "Champion vs Challenger", icon: <GitCompare size={14} /> },
  { key: "importance", label: "Importance & Drift", icon: <Brain size={14} /> },
  { key: "recommendations", label: "Recommendations", icon: <Lightbulb size={14} /> },
];

type Props = {
  runId: string | null;
  busy: string | null;
  optResult: any | null;
  walkForward: any | null;
  monteCarlo: any | null;
  mlCompareRows: any[] | null;
  shap: any | null;
  drift: any | null;
  recommendations: any[] | null;
  onOptimize: () => void;
  onWalkForward: () => void;
  onMonteCarlo: () => void;
};

export default function OptimizationPanel(props: Props) {
  const [tab, setTab] = useState<TabKey>("optimizer");

  return (
    <Card title="Optimization & Validation" full className="bt-opt-card">
      <div className="bt-opt-tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`bt-chip ${tab === t.key ? "active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab === "optimizer" && <OptimizerTab {...props} />}
      {tab === "walkforward" && <WalkForwardTab {...props} />}
      {tab === "montecarlo" && <MonteCarloTab {...props} />}
      {tab === "models" && <ModelsTab rows={props.mlCompareRows} />}
      {tab === "importance" && <ImportanceTab shap={props.shap} drift={props.drift} />}
      {tab === "recommendations" && <RecommendationsTab recommendations={props.recommendations} />}
    </Card>
  );
}

function RunButton({
  label,
  busyLabel,
  busy,
  disabled,
  onClick,
}: {
  label: string;
  busyLabel: string;
  busy: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <div className="controls" style={{ marginBottom: 14 }}>
      <button type="button" disabled={busy || disabled} onClick={onClick}>
        {busy ? <Loader2 size={15} className="bt-spin" /> : <PlayCircle size={15} />}
        {busy ? busyLabel : label}
      </button>
    </div>
  );
}

// ------------------------------------------------------------- Optimizer

function OptimizerTab({ optResult, busy, onOptimize }: Props) {
  const heatmap = useMemo(() => {
    const results: any[] = optResult?.results || [];
    if (!results.length || !optResult?.best) return null;
    const bestThreshold = optResult.best.entry_confidence_threshold;
    const atBest = results.filter((r) => r.entry_confidence_threshold === bestThreshold);
    const slVals = [...new Set(atBest.map((r) => r.atr_sl_mult))].sort((a, b) => a - b);
    const tpVals = [...new Set(atBest.map((r) => r.atr_tp_mult))].sort((a, b) => a - b);
    const maxAbs = Math.max(0.001, ...atBest.map((r) => Math.abs(r.score ?? 0)));
    const cell = (sl: number, tp: number) =>
      atBest.find((r) => r.atr_sl_mult === sl && r.atr_tp_mult === tp) || null;
    return { slVals, tpVals, cell, maxAbs, bestThreshold };
  }, [optResult]);

  return (
    <div>
      <RunButton
        label="Run Parameter Optimization"
        busyLabel="Optimizing…"
        busy={busy === "optimize"}
        onClick={onOptimize}
      />
      {!optResult ? (
        <p className="analytics-empty">
          Grid-searches ATR stop/target multiples and the entry-confidence threshold over the same
          validated dataset, ranked by Sharpe. Results appear here.
        </p>
      ) : (
        <>
          {optResult.best && (
            <div className="bt-best-grid">
              <div className="bt-config-tile hero">
                <span className="tile-label">Best SL ×</span>
                <b className="bt-config-value">{fmtNum(optResult.best.atr_sl_mult, 1)}</b>
              </div>
              <div className="bt-config-tile hero">
                <span className="tile-label">Best TP ×</span>
                <b className="bt-config-value">{fmtNum(optResult.best.atr_tp_mult, 1)}</b>
              </div>
              <div className="bt-config-tile hero">
                <span className="tile-label">Best Min Confidence</span>
                <b className="bt-config-value">{fmtNum(optResult.best.entry_confidence_threshold, 0)}</b>
              </div>
              <div className="bt-config-tile hero">
                <span className="tile-label">Best Score (Sharpe)</span>
                <b className="bt-config-value">{fmtNum(optResult.best.score, 3)}</b>
              </div>
              <div className="bt-config-tile">
                <span className="tile-label">Grid Size</span>
                <b className="bt-config-value">{optResult.combinations_tested}</b>
              </div>
            </div>
          )}

          <div className="bt-opt-split">
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>SL ×</th>
                    <th>TP ×</th>
                    <th>Min Conf</th>
                    <th>Score</th>
                    <th>Return</th>
                    <th>Win Rate</th>
                    <th>Max DD</th>
                    <th>Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {(optResult.results || []).slice(0, 10).map((r: any, i: number) => (
                    <tr key={i}>
                      <td>{i + 1}</td>
                      <td>{fmtNum(r.atr_sl_mult, 1)}</td>
                      <td>{fmtNum(r.atr_tp_mult, 1)}</td>
                      <td>{fmtNum(r.entry_confidence_threshold, 0)}</td>
                      <td>{fmtNum(r.score, 3)}</td>
                      <td>
                        <span className={(r.metrics?.total_return_pct ?? 0) >= 0 ? "green" : "red"}>
                          {fmtPct(r.metrics?.total_return_pct, 2)}
                        </span>
                      </td>
                      <td>{fmtPct(r.metrics?.win_rate, 1)}</td>
                      <td>{fmtPct(r.metrics?.max_drawdown_pct, 2)}</td>
                      <td>{r.metrics?.total_trades ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {heatmap && (
              <div className="bt-param-heatmap">
                <span className="tile-label">
                  Score heatmap (SL × TP at min confidence {fmtNum(heatmap.bestThreshold, 0)})
                </span>
                <div
                  className="bt-heatmap"
                  style={{ gridTemplateColumns: `44px repeat(${heatmap.tpVals.length}, 1fr)` }}
                >
                  <span />
                  {heatmap.tpVals.map((tp) => (
                    <span className="bt-heatmap-label" key={tp}>TP {tp}</span>
                  ))}
                  {heatmap.slVals.map((sl) => (
                    <HeatRow key={sl} sl={sl} heatmap={heatmap} />
                  ))}
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function HeatRow({ sl, heatmap }: { sl: number; heatmap: any }) {
  return (
    <>
      <span className="bt-heatmap-label">SL {sl}</span>
      {heatmap.tpVals.map((tp: number) => {
        const r = heatmap.cell(sl, tp);
        if (!r) return <span className="bt-heatmap-cell empty" key={tp} />;
        const score = r.score ?? 0;
        const alpha = 0.12 + Math.min(1, Math.abs(score) / heatmap.maxAbs) * 0.55;
        return (
          <span
            key={tp}
            className="bt-heatmap-cell"
            style={{ background: score >= 0 ? `rgba(0,245,160,${alpha})` : `rgba(255,93,115,${alpha})` }}
            title={`SL ${sl} / TP ${tp}: score ${fmtNum(score, 3)}, return ${fmtPct(r.metrics?.total_return_pct, 2)}`}
          >
            {fmtNum(score, 2)}
          </span>
        );
      })}
    </>
  );
}

// ------------------------------------------------------------- Walk-forward

function WalkForwardTab({ walkForward, runId, busy, onWalkForward }: Props) {
  return (
    <div>
      <RunButton
        label="Run Walk-Forward Validation"
        busyLabel="Validating…"
        busy={busy === "walkforward"}
        disabled={!runId}
        onClick={onWalkForward}
      />
      {!runId ? (
        <p className="analytics-empty">Run a backtest first — walk-forward replays its stored configuration.</p>
      ) : !walkForward?.ok ? (
        <p className="analytics-empty">
          Rolls a train/validate window across the dataset to test out-of-sample stability. Results appear here.
        </p>
      ) : (
        (walkForward.results || []).map((r: any) => (
          <div key={`${r.symbol}-${r.timeframe}`} style={{ marginBottom: 16 }}>
            <p className="bt-note">
              {r.symbol} {r.timeframe}: {r.windows_run} windows ({r.train_bars} train / {r.validate_bars}{" "}
              validate bars) — avg return {fmtPct(r.average_metrics?.total_return_pct, 2)}, avg win rate{" "}
              {fmtPct(r.average_metrics?.win_rate, 1)}
            </p>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Window</th>
                    <th>Return</th>
                    <th>Sharpe</th>
                    <th>Win Rate</th>
                    <th>Max DD</th>
                    <th>Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {(r.windows || []).map((w: any) => (
                    <tr key={w.window}>
                      <td>{w.window}</td>
                      <td>
                        <span className={(w.metrics?.total_return_pct ?? 0) >= 0 ? "green" : "red"}>
                          {fmtPct(w.metrics?.total_return_pct, 2)}
                        </span>
                      </td>
                      <td>{fmtNum(w.metrics?.sharpe_ratio, 2)}</td>
                      <td>{fmtPct(w.metrics?.win_rate, 1)}</td>
                      <td>{fmtPct(w.metrics?.max_drawdown_pct, 2)}</td>
                      <td>{w.metrics?.total_trades ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ------------------------------------------------------------- Monte Carlo

function MonteCarloTab({ monteCarlo, runId, busy, onMonteCarlo }: Props) {
  return (
    <div>
      <RunButton
        label="Run Monte Carlo (1000 sims)"
        busyLabel="Simulating…"
        busy={busy === "montecarlo"}
        disabled={!runId}
        onClick={onMonteCarlo}
      />
      {!runId ? (
        <p className="analytics-empty">Run a backtest first — Monte Carlo reshuffles its closed trades.</p>
      ) : !monteCarlo?.ok ? (
        <p className="analytics-empty">
          Shuffles the run's trade sequence 1000× to measure sequence-of-returns risk (risk of ruin,
          drawdown distribution, final-balance confidence interval).
        </p>
      ) : (
        (monteCarlo.results || []).map((r: any) =>
          r.ok === false ? (
            <p className="analytics-empty" key={`${r.symbol}-${r.timeframe}`}>
              {r.symbol} {r.timeframe}: {r.error}
            </p>
          ) : (
            <div className="bt-best-grid" key={`${r.symbol}-${r.timeframe}`} style={{ marginBottom: 12 }}>
              <div className="bt-config-tile">
                <span className="tile-label">{r.symbol} {r.timeframe} · Sims</span>
                <b className="bt-config-value">{r.simulations}</b>
              </div>
              <div className="bt-config-tile">
                <span className="tile-label">Risk of Ruin</span>
                <b className={`bt-config-value ${(r.risk_of_ruin_pct ?? 0) > 5 ? "red" : "green"}`}>
                  {fmtPct(r.risk_of_ruin_pct, 2)}
                </b>
              </div>
              <div className="bt-config-tile">
                <span className="tile-label">Worst Drawdown</span>
                <b className="bt-config-value red">{fmtPct(r.worst_drawdown_pct, 2)}</b>
              </div>
              <div className="bt-config-tile">
                <span className="tile-label">Median Final Balance</span>
                <b className="bt-config-value">{fmtNum(r.median_final_balance, 0)}</b>
              </div>
              <div className="bt-config-tile">
                <span className="tile-label">90% Confidence Interval</span>
                <b className="bt-config-value">
                  {r.final_balance_confidence_interval_90pct
                    ? `${fmtNum(r.final_balance_confidence_interval_90pct[0], 0)} – ${fmtNum(r.final_balance_confidence_interval_90pct[1], 0)}`
                    : "—"}
                </b>
              </div>
              <div className="bt-config-tile">
                <span className="tile-label">Median CAGR</span>
                <b className="bt-config-value">{fmtPct(r.median_cagr_pct, 2)}</b>
              </div>
            </div>
          )
        )
      )}
    </div>
  );
}

// ------------------------------------------------------------- Models

function ModelsTab({ rows }: { rows: any[] | null }) {
  if (!rows) return <p className="analytics-empty">Loading model registry…</p>;
  if (!rows.length)
    return (
      <p className="analytics-empty">
        No trained models in the registry yet — train one in the Model Center to compare champion vs
        challengers here.
      </p>
    );
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Role</th>
            <th>Model</th>
            <th>Algorithm</th>
            <th>Version</th>
            <th>Val Accuracy</th>
            <th>F1</th>
            <th>Win Rate</th>
            <th>Sharpe</th>
            <th>Profit Factor</th>
            <th>Max DD</th>
            <th>Trained</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r: any) => (
            <tr key={r.model_id || r.id}>
              <td>
                <span className={`bt-role-pill ${r.role === "champion" ? "champion" : ""}`}>{r.role}</span>
              </td>
              <td>{r.model_name || "—"}</td>
              <td>{r.algorithm || "—"}</td>
              <td>{r.version ?? "—"}</td>
              <td>{fmtPct(typeof r.val_accuracy === "number" ? r.val_accuracy * 100 : null, 1)}</td>
              <td>{fmtNum(r.f1, 3)}</td>
              <td>{fmtPct(r.win_rate, 1)}</td>
              <td>{fmtNum(r.sharpe_ratio, 2)}</td>
              <td>{fmtNum(r.profit_factor, 2)}</td>
              <td>{fmtPct(r.max_drawdown_pct, 2)}</td>
              <td>{r.trained_at ? fmtRelativeTime(r.trained_at) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------- Importance & drift

function ImportanceTab({ shap, drift }: { shap: any | null; drift: any | null }) {
  const features: any[] = shap?.importance?.features || [];
  const top = features.slice(0, 12);
  const maxImp = Math.max(0.000001, ...top.map((f) => Math.abs(f.importance ?? f.mean_abs_shap ?? 0)));

  const driftEntries = useMemo(() => {
    if (!drift) return [];
    return Object.entries(drift)
      .map(([type, d]: [string, any]) => ({ type, latest: d?.latest }))
      .filter((d) => d.latest);
  }, [drift]);

  return (
    <div className="bt-opt-split">
      <div>
        <span className="tile-label">Feature Importance (champion model)</span>
        {!shap ? (
          <p className="analytics-empty">No champion model with a stored attribution artifact yet.</p>
        ) : shap.available === false ? (
          <p className="analytics-empty">{shap.reason || "No attribution artifact stored for this model."}</p>
        ) : !top.length ? (
          <p className="analytics-empty">The attribution artifact contains no features.</p>
        ) : (
          <div className="bt-importance-list">
            {top.map((f) => {
              const val = Math.abs(f.importance ?? f.mean_abs_shap ?? 0);
              return (
                <div className="bt-importance-row" key={f.feature}>
                  <span className="bt-importance-name">{f.feature}</span>
                  <div className="bt-regime-bar-track">
                    <div
                      className="bt-regime-bar-fill"
                      style={{
                        width: `${(val / maxImp) * 100}%`,
                        background: "linear-gradient(90deg, rgba(124,92,255,0.5), var(--c-purple))",
                      }}
                    />
                  </div>
                  <span className="bt-importance-val">{fmtNum(val, 4)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div>
        <span className="tile-label">Model Drift</span>
        {!driftEntries.length ? (
          <p className="analytics-empty">No drift scans recorded yet — run one from the Model Center.</p>
        ) : (
          <div className="bt-best-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
            {driftEntries.map((d) => (
              <div className="bt-config-tile" key={d.type}>
                <span className="tile-label">{d.type.replace(/_/g, " ")}</span>
                <b
                  className={`bt-config-value ${
                    d.latest.is_drifted ? "red" : d.latest.status === "warning" ? "" : "green"
                  }`}
                >
                  {fmtNum(d.latest.score, 3)}
                  <small style={{ fontWeight: 500, opacity: 0.7 }}>
                    {" "}/ {fmtNum(d.latest.threshold, 2)} {d.latest.is_drifted ? "· DRIFTED" : ""}
                  </small>
                </b>
                <span className="tile-label" style={{ marginBottom: 0 }}>
                  {d.latest.method} · {fmtRelativeTime(d.latest.checked_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------- Recommendations

function RecommendationsTab({ recommendations }: { recommendations: any[] | null }) {
  if (!recommendations?.length)
    return (
      <p className="analytics-empty">
        The learning loop has no recommendations yet — evaluate prediction history in the Research Lab
        to generate them.
      </p>
    );
  return (
    <div className="bt-reco-list">
      {recommendations.map((r: any, i: number) => (
        <div className={`bt-reco severity-${(r.severity || "info").toLowerCase()}`} key={i}>
          <span className="bt-reco-tag">
            {r.severity} · {(r.type || "").replace(/_/g, " ")}
          </span>
          <p>{r.reason}</p>
          <p className="bt-reco-action">→ {r.action}</p>
        </div>
      ))}
    </div>
  );
}
