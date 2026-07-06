import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { EmptySlate } from "./widgets";

/** Chart palette matching the QuantX dark theme. */
export const C = {
  green: "#00f5a0",
  red: "#ff5d73",
  yellow: "#ffd166",
  purple: "#7c5cff",
  blue: "#00a8ff",
  cyan: "#00f5d4",
  dim: "#8b90a8",
  grid: "rgba(255,255,255,0.06)",
};

const tooltipStyle = {
  backgroundColor: "rgba(13, 16, 33, 0.95)",
  border: "1px solid rgba(124, 92, 255, 0.35)",
  borderRadius: 8,
  fontSize: 12,
  color: "#c8cbe0",
} as const;

// ------------------------------------------------------------- importance

type ImportanceFeature = {
  feature: string;
  mean_abs_shap?: number;
  mean_shap?: number;
  importance?: number;
};

export function ImportancePanel({
  importance,
  sample,
}: {
  importance: { method: string; reason?: string; features: ImportanceFeature[] } | null;
  sample: { features: string[]; shap_rows: number[][]; value_rows: number[][] } | null;
}) {
  const [mode, setMode] = useState<"bar" | "waterfall" | "beeswarm">("bar");

  if (!importance || !importance.features?.length) {
    return (
      <EmptySlate
        title="No feature attribution"
        reason={importance?.reason || "Train a model to compute feature attribution from its holdout slice."}
      />
    );
  }

  const isShap = importance.method === "native_tree_shap";
  const rows = importance.features.slice(0, 12).map((f) => ({
    feature: f.feature,
    value: isShap ? f.mean_abs_shap ?? 0 : f.importance ?? 0,
    signed: isShap ? f.mean_shap ?? 0 : f.importance ?? 0,
  }));

  return (
    <div>
      <div className="mll-chart-toolbar">
        <span className="tile-label">
          {isShap ? "SHAP (native TreeSHAP)" : "Permutation importance"}
        </span>
        <div className="mll-seg">
          {(["bar", "waterfall", "beeswarm"] as const).map((m) => (
            <button
              key={m}
              className={mode === m ? "active" : ""}
              onClick={() => setMode(m)}
              disabled={m === "beeswarm" && !sample}
              title={m === "beeswarm" && !sample ? "Beeswarm needs per-row SHAP samples (tree models only)" : undefined}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      {mode === "bar" && (
        <ResponsiveContainer width="100%" height={Math.max(220, rows.length * 26)}>
          <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 16 }}>
            <CartesianGrid stroke={C.grid} horizontal={false} />
            <XAxis type="number" tick={{ fill: C.dim, fontSize: 10 }} />
            <YAxis type="category" dataKey="feature" width={130} tick={{ fill: C.dim, fontSize: 10 }} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => [Number(v).toFixed(5), isShap ? "mean |SHAP|" : "importance"]} />
            <Bar dataKey="value" radius={[0, 4, 4, 0]}>
              {rows.map((r, i) => (
                <Cell key={i} fill={r.signed >= 0 ? C.cyan : C.red} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}

      {mode === "waterfall" && <WaterfallChart rows={rows} isShap={isShap} />}
      {mode === "beeswarm" && sample && <BeeswarmChart sample={sample} />}
    </div>
  );
}

function WaterfallChart({ rows, isShap }: { rows: { feature: string; signed: number }[]; isShap: boolean }) {
  // cumulative walk from 0: each feature's signed mean contribution
  const data = useMemo(() => {
    let cum = 0;
    return rows.map((r) => {
      const start = cum;
      cum += r.signed;
      return { feature: r.feature, range: [Math.min(start, cum), Math.max(start, cum)], signed: r.signed, cum };
    });
  }, [rows]);
  return (
    <ResponsiveContainer width="100%" height={Math.max(220, rows.length * 26)}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
        <CartesianGrid stroke={C.grid} horizontal={false} />
        <XAxis type="number" tick={{ fill: C.dim, fontSize: 10 }} />
        <YAxis type="category" dataKey="feature" width={130} tick={{ fill: C.dim, fontSize: 10 }} />
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(_: any, __: any, entry: any) => [
            `${entry.payload.signed >= 0 ? "+" : ""}${entry.payload.signed.toFixed(5)} (cum ${entry.payload.cum.toFixed(5)})`,
            isShap ? "mean SHAP" : "importance",
          ]}
        />
        <ReferenceLine x={0} stroke={C.dim} />
        <Bar dataKey="range" radius={[0, 3, 3, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.signed >= 0 ? C.green : C.red} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function BeeswarmChart({ sample }: { sample: { features: string[]; shap_rows: number[][]; value_rows: number[][] } }) {
  // per-feature strips: x = SHAP value, y = feature index + jitter,
  // color = feature value percentile (low blue -> high red)
  const { points, order } = useMemo(() => {
    const featureCount = sample.features.length;
    const meanAbs = Array.from({ length: featureCount }, (_, f) =>
      sample.shap_rows.reduce((a, row) => a + Math.abs(row[f] ?? 0), 0)
    );
    const order = meanAbs
      .map((v, i) => [v, i] as [number, number])
      .sort((a, b) => b[0] - a[0])
      .slice(0, 8)
      .map(([, i]) => i);

    const pts: { x: number; y: number; c: number }[] = [];
    order.forEach((f, rank) => {
      const values = sample.value_rows.map((r) => r[f] ?? 0);
      const lo = Math.min(...values);
      const hi = Math.max(...values);
      sample.shap_rows.forEach((row, ri) => {
        const pct = hi > lo ? (values[ri] - lo) / (hi - lo) : 0.5;
        pts.push({ x: row[f] ?? 0, y: rank + ((ri * 2654435761) % 1000) / 1000 * 0.7 - 0.35, c: pct });
      });
    });
    return { points: pts, order };
  }, [sample]);

  return (
    <ResponsiveContainer width="100%" height={Math.max(240, order.length * 34)}>
      <ScatterChart margin={{ left: 8, right: 16, top: 8 }}>
        <CartesianGrid stroke={C.grid} />
        <XAxis type="number" dataKey="x" tick={{ fill: C.dim, fontSize: 10 }} name="SHAP" />
        <YAxis
          type="number"
          dataKey="y"
          domain={[-0.6, order.length - 0.4]}
          ticks={order.map((_, i) => i)}
          tickFormatter={(v: number) => sample.features[order[Math.round(v)]] ?? ""}
          width={130}
          tick={{ fill: C.dim, fontSize: 10 }}
        />
        <ZAxis range={[14, 14]} />
        <ReferenceLine x={0} stroke={C.dim} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => Number(v).toFixed(5)} />
        <Scatter data={points} isAnimationActive={false}>
          {points.map((p, i) => (
            <Cell key={i} fill={`rgb(${Math.round(60 + p.c * 195)}, ${Math.round(120 - p.c * 40)}, ${Math.round(255 - p.c * 140)})`} fillOpacity={0.75} />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}

// ------------------------------------------------------------------ curves

export function CurvePanel({ artifacts }: { artifacts: Record<string, any> }) {
  const [tab, setTab] = useState<"roc" | "pr" | "calibration" | "lift" | "gain">("roc");
  const roc = artifacts.roc_curve as { x: number; y: number }[] | undefined;
  const pr = artifacts.pr_curve as { x: number; y: number }[] | undefined;
  const calibration = artifacts.calibration_curve as any[] | undefined;
  const lift = artifacts.lift_gain as any | undefined;

  const tabs: Array<[typeof tab, string, boolean]> = [
    ["roc", "ROC", !!roc],
    ["pr", "Precision-Recall", !!pr],
    ["calibration", "Calibration", !!calibration],
    ["lift", "Lift", !!lift],
    ["gain", "Gain", !!lift],
  ];

  return (
    <div>
      <div className="mll-seg" style={{ marginBottom: 10 }}>
        {tabs.map(([id, label, enabled]) => (
          <button key={id} className={tab === id ? "active" : ""} disabled={!enabled} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>

      {tab === "roc" && roc && (
        <ResponsiveContainer width="100%" height={230}>
          <ComposedChart data={roc} margin={{ left: 0, right: 12 }}>
            <CartesianGrid stroke={C.grid} />
            <XAxis dataKey="x" type="number" domain={[0, 1]} tick={{ fill: C.dim, fontSize: 10 }} label={{ value: "FPR", fill: C.dim, fontSize: 10, position: "insideBottom", offset: -2 }} />
            <YAxis domain={[0, 1]} tick={{ fill: C.dim, fontSize: 10 }} label={{ value: "TPR", fill: C.dim, fontSize: 10, angle: -90, position: "insideLeft" }} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => Number(v).toFixed(3)} />
            <Line type="monotone" dataKey="y" stroke={C.cyan} dot={false} strokeWidth={2} />
            <Line type="linear" dataKey="x" stroke={C.dim} dot={false} strokeDasharray="5 4" strokeWidth={1} />
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {tab === "pr" && pr && (
        <ResponsiveContainer width="100%" height={230}>
          <LineChart data={pr} margin={{ left: 0, right: 12 }}>
            <CartesianGrid stroke={C.grid} />
            <XAxis dataKey="x" type="number" domain={[0, 1]} tick={{ fill: C.dim, fontSize: 10 }} label={{ value: "Recall", fill: C.dim, fontSize: 10, position: "insideBottom", offset: -2 }} />
            <YAxis domain={[0, 1]} tick={{ fill: C.dim, fontSize: 10 }} label={{ value: "Precision", fill: C.dim, fontSize: 10, angle: -90, position: "insideLeft" }} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => Number(v).toFixed(3)} />
            <Line type="monotone" dataKey="y" stroke={C.purple} dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      )}

      {tab === "calibration" && calibration && (
        <ResponsiveContainer width="100%" height={230}>
          <ComposedChart data={calibration.filter((c) => c.count > 0)} margin={{ left: 0, right: 12 }}>
            <CartesianGrid stroke={C.grid} />
            <XAxis dataKey="bin_mid" type="number" domain={[0, 1]} tick={{ fill: C.dim, fontSize: 10 }} label={{ value: "Predicted P(up)", fill: C.dim, fontSize: 10, position: "insideBottom", offset: -2 }} />
            <YAxis domain={[0, 1]} tick={{ fill: C.dim, fontSize: 10 }} label={{ value: "Actual rate", fill: C.dim, fontSize: 10, angle: -90, position: "insideLeft" }} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => (v == null ? "—" : Number(v).toFixed(3))} />
            <Line type="monotone" dataKey="bin_mid" stroke={C.dim} dot={false} strokeDasharray="5 4" strokeWidth={1} name="perfect" />
            <Line type="monotone" dataKey="actual" stroke={C.yellow} strokeWidth={2} name="actual" />
            <Bar dataKey="count" yAxisId={1} hide />
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {tab === "lift" && lift && (
        <ResponsiveContainer width="100%" height={230}>
          <BarChart data={lift.deciles} margin={{ left: 0, right: 12 }}>
            <CartesianGrid stroke={C.grid} />
            <XAxis dataKey="decile" tick={{ fill: C.dim, fontSize: 10 }} label={{ value: "Score decile", fill: C.dim, fontSize: 10, position: "insideBottom", offset: -2 }} />
            <YAxis tick={{ fill: C.dim, fontSize: 10 }} />
            <Tooltip contentStyle={tooltipStyle} />
            <ReferenceLine y={1} stroke={C.dim} strokeDasharray="4 4" />
            <Bar dataKey="lift" radius={[3, 3, 0, 0]}>
              {lift.deciles.map((d: any, i: number) => (
                <Cell key={i} fill={(d.lift ?? 1) >= 1 ? C.green : C.red} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}

      {tab === "gain" && lift && (
        <ResponsiveContainer width="100%" height={230}>
          <AreaChart data={lift.deciles} margin={{ left: 0, right: 12 }}>
            <CartesianGrid stroke={C.grid} />
            <XAxis dataKey="population_pct" tick={{ fill: C.dim, fontSize: 10 }} label={{ value: "% population", fill: C.dim, fontSize: 10, position: "insideBottom", offset: -2 }} />
            <YAxis domain={[0, 100]} tick={{ fill: C.dim, fontSize: 10 }} label={{ value: "% positives captured", fill: C.dim, fontSize: 10, angle: -90, position: "insideLeft" }} />
            <Tooltip contentStyle={tooltipStyle} />
            <Area type="monotone" dataKey="cumulative_gain_pct" stroke={C.blue} fill="rgba(0,168,255,0.15)" strokeWidth={2} />
            <Line type="linear" dataKey="population_pct" stroke={C.dim} strokeDasharray="5 4" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// -------------------------------------------------------- confusion matrix

export function ConfusionMatrix({ cm }: { cm: { tp: number; fp: number; tn: number; fn: number; precision: number; recall: number; f1: number; total: number } }) {
  const cells = [
    { label: "True Positive", value: cm.tp, cls: "green", hint: "Predicted UP, was UP" },
    { label: "False Positive", value: cm.fp, cls: "red", hint: "Predicted UP, was DOWN" },
    { label: "False Negative", value: cm.fn, cls: "red", hint: "Predicted DOWN, was UP" },
    { label: "True Negative", value: cm.tn, cls: "green", hint: "Predicted DOWN, was DOWN" },
  ];
  return (
    <div>
      <div className="mll-cm-grid">
        {cells.map((c) => (
          <div key={c.label} className={`mll-cm-cell ${c.cls}`} title={c.hint}>
            <span>{c.label}</span>
            <b>{c.value}</b>
            <i>{cm.total ? `${((c.value / cm.total) * 100).toFixed(1)}%` : "—"}</i>
          </div>
        ))}
      </div>
      <div className="mll-cm-stats">
        <span>Precision <b>{(cm.precision * 100).toFixed(1)}%</b></span>
        <span>Recall <b>{(cm.recall * 100).toFixed(1)}%</b></span>
        <span>F1 <b>{(cm.f1 * 100).toFixed(1)}%</b></span>
        <span>Samples <b>{cm.total}</b></span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------ drift history

export function DriftHistoryChart({ series }: { series: { drift_type: string; method: string; threshold: number | null; points: { date: string; score: number }[] }[] }) {
  const merged = useMemo(() => {
    const byDate: Record<string, any> = {};
    for (const s of series) {
      const key = `${s.drift_type}/${s.method}`;
      for (const p of s.points) {
        byDate[p.date] = byDate[p.date] || { date: p.date };
        byDate[p.date][key] = p.score;
      }
    }
    return Object.values(byDate).sort((a: any, b: any) => a.date.localeCompare(b.date));
  }, [series]);

  if (!merged.length) {
    return <EmptySlate title="No drift history yet" reason="Drift scans run on the MLOps scheduler cycle; history accumulates automatically (or run a manual scan)." />;
  }

  const colors = [C.cyan, C.purple, C.yellow, C.green, C.blue, C.red, "#e879f9", "#94a3b8"];
  const keys = series.map((s) => `${s.drift_type}/${s.method}`);
  const threshold = series.find((s) => s.threshold != null)?.threshold;

  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={merged} margin={{ left: 0, right: 12 }}>
        <CartesianGrid stroke={C.grid} />
        <XAxis dataKey="date" tick={{ fill: C.dim, fontSize: 10 }} />
        <YAxis tick={{ fill: C.dim, fontSize: 10 }} />
        <Tooltip contentStyle={tooltipStyle} />
        {threshold != null && <ReferenceLine y={threshold} stroke={C.red} strokeDasharray="5 4" label={{ value: "threshold", fill: C.red, fontSize: 10 }} />}
        {keys.slice(0, 8).map((k, i) => (
          <Line key={k} dataKey={k} stroke={colors[i % colors.length]} dot={false} strokeWidth={1.5} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------- training curve

export function TrainingCurveChart({ curve }: { curve: { epoch: number; loss?: number | null; val_loss?: number | null; accuracy?: number | null }[] }) {
  if (!curve?.length) return <EmptySlate title="No training curve" reason="This algorithm reports no per-iteration loss (single-shot fit)." />;
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={curve} margin={{ left: 0, right: 12 }}>
        <CartesianGrid stroke={C.grid} />
        <XAxis dataKey="epoch" tick={{ fill: C.dim, fontSize: 10 }} />
        <YAxis tick={{ fill: C.dim, fontSize: 10 }} domain={["auto", "auto"]} />
        <Tooltip contentStyle={tooltipStyle} />
        <Line dataKey="loss" stroke={C.cyan} dot={false} strokeWidth={1.5} connectNulls />
        <Line dataKey="val_loss" stroke={C.yellow} dot={false} strokeWidth={1.5} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ------------------------------------------------------- performance history

export function MetricHistoryChart({
  data,
  dataKey,
  color,
  yDomain,
}: {
  data: any[];
  dataKey: string;
  color: string;
  yDomain?: [number | string, number | string];
}) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ left: 0, right: 12 }}>
        <CartesianGrid stroke={C.grid} />
        <XAxis dataKey="date" tick={{ fill: C.dim, fontSize: 10 }} />
        <YAxis tick={{ fill: C.dim, fontSize: 10 }} domain={yDomain || ["auto", "auto"]} />
        <Tooltip contentStyle={tooltipStyle} />
        <Area type="monotone" dataKey={dataKey} stroke={color} fill={`${color}22`} strokeWidth={2} connectNulls />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function HpoHistoryChart({ history }: { history: { trial: number; value: number }[] }) {
  const withBest = useMemo(() => {
    let best = -Infinity;
    return history.map((h) => {
      best = Math.max(best, h.value);
      return { ...h, best };
    });
  }, [history]);
  return (
    <ResponsiveContainer width="100%" height={200}>
      <ComposedChart data={withBest} margin={{ left: 0, right: 12 }}>
        <CartesianGrid stroke={C.grid} />
        <XAxis dataKey="trial" tick={{ fill: C.dim, fontSize: 10 }} />
        <YAxis tick={{ fill: C.dim, fontSize: 10 }} domain={["auto", "auto"]} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => Number(v).toFixed(4)} />
        <Scatter dataKey="value" fill={C.blue} />
        <Line dataKey="best" stroke={C.green} dot={false} strokeWidth={2} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
