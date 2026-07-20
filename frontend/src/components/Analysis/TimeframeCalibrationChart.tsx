import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../../services/api";
import { fmtLocalDateTime } from "../../lib/format";

/** Professional per-timeframe prediction analytics: stacked resolution
 *  outcomes across the canonical 1m..1M axis (1M = one calendar month),
 *  calibration progress and honest readiness for the selected timeframe,
 *  plus the safe non-destructive "Start New Prediction Cycle" control.
 *  Legacy/unattributed rows are intentionally NOT on this axis - the API
 *  reports them separately and they are excluded from calibration. */

type TfRow = {
  key: string;
  total_predictions: number;
  resolved: number;
  unresolved: number;
  correct: number;
  wrong: number;
  neutral: number;
  accuracy: number | null;
  neutral_rate: number | null;
  first_prediction: string | null;
  latest_prediction: string | null;
  oldest_unresolved_at?: string | null;
  next_resolution_at?: string | null;
  unresolved_reasons?: Record<string, number>;
  relevant_calibration_samples?: number;
  required_calibration_samples?: number;
  readiness_status?: string;
  expected_horizon_seconds?: number | null;
  average_resolution_delay_seconds?: number | null;
};

type Props = { summary: any; onRefresh?: () => void };

const OUTCOME_COLORS: Record<string, string> = {
  correct: "#22c55e",
  wrong: "#ef4444",
  neutral: "#eab308",
  unresolved: "#64748b",
};

const REASON_LABELS: Record<string, string> = {
  awaiting_horizon: "Awaiting horizon",
  awaiting_future_candle: "Awaiting future candle",
  market_data_gap: "Market data gap",
  resolver_backlog: "Resolver backlog",
  resolver_not_running: "Resolver not running",
  legacy_missing_metadata: "Legacy metadata missing",
  unsupported_timeframe: "Unsupported timeframe",
};

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const row: TfRow | undefined = payload[0]?.payload;
  return (
    <div className="chart-tooltip" role="tooltip">
      <b>{label === "1M" ? "1M (calendar month)" : label}</b>
      {payload.map((p: any) => (
        <div key={p.dataKey}>
          <span style={{ color: p.fill }}>{p.name}</span>: {p.value}
        </div>
      ))}
      {row?.accuracy != null && <div>Directional accuracy: {(row.accuracy * 100).toFixed(1)}%</div>}
      {row?.first_prediction && <div>Collecting since {fmtLocalDateTime(row.first_prediction)}</div>}
    </div>
  );
}

export default function TimeframeCalibrationChart({ summary, onRefresh }: Props) {
  const [selected, setSelected] = useState<string>("1m");
  const [busy, setBusy] = useState(false);
  const [cycleMessage, setCycleMessage] = useState("");
  const timeframes: string[] = summary?.timeframes ?? [];
  const rows: TfRow[] = useMemo(() => summary?.by_timeframe ?? [], [summary]);
  const row = rows.find((x) => x.key === selected);

  if (!summary) return <p className="analytics-empty">Loading timeframe analytics…</p>;
  if (!rows.length) return <p className="analytics-empty">No predictions recorded yet.</p>;

  async function startCycle() {
    if (!window.confirm(
      "Start a new prediction cycle?\n\nThis is non-destructive: previous predictions and outcomes are preserved and archived under their old cycle. No trade is placed - normal confidence, edge, authority, and risk gates still decide everything.")) return;
    setBusy(true);
    setCycleMessage("");
    try {
      const res = await api.startPredictionCycle(undefined, crypto.randomUUID());
      setCycleMessage(res.created
        ? `New cycle started ${fmtLocalDateTime(res.started_at)} · evaluation: ${res.evaluation?.signal ?? "pending"}`
        : "An identical cycle request was already processed.");
      onRefresh?.();
    } catch (e: any) {
      setCycleMessage(`Could not start cycle: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  const progress = row
    ? Math.min(100, ((row.relevant_calibration_samples ?? 0) / (row.required_calibration_samples || 20)) * 100)
    : 0;

  return (
    <div className="tf-calibration" data-testid="timeframe-calibration-chart">
      <div className="tf-cal-toolbar">
        <div className="tf-cal-selector" role="tablist" aria-label="Timeframe">
          {timeframes.map((tf) => (
            <button
              key={tf}
              role="tab"
              aria-selected={tf === selected}
              className={tf === selected ? "chip green" : "chip"}
              onClick={() => setSelected(tf)}
              title={tf === "1M" ? "One calendar month" : tf === "1m" ? "One minute" : tf}
            >
              {tf}
            </button>
          ))}
        </div>
        <div className="tf-cal-actions">
          <button className="mini-btn" onClick={() => setSelected("1m")} title="Reset chart zoom, filters and selection only - no data is changed">
            Reset Chart View
          </button>
          <button className="mini-btn mini-btn-edit" disabled={busy} onClick={startCycle}>
            {busy ? "Starting…" : "Start New Prediction Cycle"}
          </button>
        </div>
      </div>
      {cycleMessage && <p className="regime-desc">{cycleMessage}</p>}

      <div style={{ width: "100%", height: 260 }}>
        <ResponsiveContainer>
          <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 4 }} aria-label="Prediction outcomes by timeframe">
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,.15)" />
            <XAxis dataKey="key" tick={{ fontSize: 12 }} label={{ value: "Timeframe (1M = calendar month)", position: "insideBottom", offset: -2, fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} width={44} allowDecimals={false} />
            <Tooltip content={<ChartTooltip />} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="correct" name="Correct" stackId="o" fill={OUTCOME_COLORS.correct} />
            <Bar dataKey="wrong" name="Wrong" stackId="o" fill={OUTCOME_COLORS.wrong} />
            <Bar dataKey="neutral" name="Neutral" stackId="o" fill={OUTCOME_COLORS.neutral} />
            <Bar dataKey="unresolved" name="Unresolved" stackId="o" fill={OUTCOME_COLORS.unresolved} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {row && (
        <div className="engine-metric-grid tf-cal-details">
          <span><b>{row.key === "1M" ? "1M · calendar month" : row.key}</b><br />
            {row.total_predictions} predictions · {row.resolved} resolved · {row.unresolved} unresolved</span>
          <span><b>Directional accuracy</b><br />
            {row.accuracy == null ? "Insufficient sample" : `${(row.accuracy * 100).toFixed(1)}%`}
            {row.neutral_rate != null && ` · neutral rate ${(row.neutral_rate * 100).toFixed(1)}%`}</span>
          <span><b>Collecting since</b><br />{row.first_prediction ? fmtLocalDateTime(row.first_prediction) : "No predictions yet"}</span>
          <span><b>Calibration progress</b><br />
            <progress value={progress} max={100} aria-label="Calibration progress" /> {row.relevant_calibration_samples ?? 0}/{row.required_calibration_samples ?? 20}
            {" · "}{(row.readiness_status ?? "unknown").replaceAll("_", " ")}</span>
          <span><b>Next resolution</b><br />{row.next_resolution_at ? fmtLocalDateTime(row.next_resolution_at) : "None pending"}</span>
          <span><b>Unresolved reasons</b><br />
            {row.unresolved_reasons && Object.keys(row.unresolved_reasons).length
              ? Object.entries(row.unresolved_reasons).map(([k, v]) => `${REASON_LABELS[k] ?? k}: ${v}`).join(" · ")
              : "None"}</span>
        </div>
      )}

      {summary.legacy?.total_predictions > 0 && (
        <details className="tf-cal-legacy">
          <summary>Legacy data — {summary.legacy.total_predictions} unattributed records (excluded from calibration)</summary>
          <p className="regime-desc">{summary.legacy.note}</p>
        </details>
      )}
    </div>
  );
}
