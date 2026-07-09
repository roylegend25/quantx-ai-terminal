import { Database, Timer } from "lucide-react";
import { fmtNum } from "../../lib/format";

export type DatasetInfo = {
  quality_score?: number | null;
  missing_candles?: number | null;
  interpolated?: number | null;
  rows?: number | null;
  provider?: string | null;
};

type Props = {
  symbol: string;
  timeframe: string;
  exchange: string;
  dataset: DatasetInfo | null;
  datasetLoading: boolean;
  modelVersion: string | null;
  strategyLabel: string;
  runId: string | null;
  estimatedRuntimeSec: number | null;
  running: boolean;
  progress: number; // 0..1 while running
};

function ConfigTile({ label, value, tone }: { label: string; value: string; tone?: "green" | "red" | "" }) {
  return (
    <div className={`bt-config-tile ${tone || ""}`}>
      <span className="tile-label">{label}</span>
      <b className="bt-config-value">{value}</b>
    </div>
  );
}

export default function ConfigPanel({
  symbol,
  timeframe,
  exchange,
  dataset,
  datasetLoading,
  modelVersion,
  strategyLabel,
  runId,
  estimatedRuntimeSec,
  running,
  progress,
}: Props) {
  const rows = dataset?.rows;
  const interpolated = dataset?.interpolated;
  const interpolationPct =
    rows && rows > 0 && interpolated != null ? (interpolated / rows) * 100 : null;
  const quality = dataset?.quality_score;

  return (
    <div className="bt-config-panel">
      <div className="bt-panel-head">
        <Database size={15} />
        <span>Backtest Configuration</span>
      </div>

      {datasetLoading ? (
        <div className="bt-skeleton-stack">
          {Array.from({ length: 6 }).map((_, i) => (
            <div className="bt-skeleton" key={i} />
          ))}
        </div>
      ) : (
        <div className="bt-config-grid">
          <ConfigTile label="Market" value={symbol} />
          <ConfigTile label="Timeframe" value={timeframe.toUpperCase()} />
          <ConfigTile label="Exchange" value={exchange} />
          <ConfigTile label="Data Provider" value={dataset?.provider || "Validated candle store"} />
          <ConfigTile
            label="Data Quality"
            value={quality != null ? `${fmtNum(quality, 1)} / 100` : "No report"}
            tone={quality == null ? "" : quality >= 80 ? "green" : quality >= 40 ? "" : "red"}
          />
          <ConfigTile label="Downloaded Candles" value={rows != null ? rows.toLocaleString() : "None stored"} />
          <ConfigTile
            label="Missing Candles"
            value={dataset?.missing_candles != null ? dataset.missing_candles.toLocaleString() : "—"}
            tone={dataset?.missing_candles ? "red" : ""}
          />
          <ConfigTile
            label="Interpolation"
            value={interpolationPct != null ? `${fmtNum(interpolationPct, 2)}%` : "—"}
          />
          <ConfigTile label="Model Version" value={modelVersion || "No champion promoted"} />
          <ConfigTile label="Strategy" value={strategyLabel} />
          <ConfigTile label="Run ID" value={runId || "Not run yet"} />
          <ConfigTile
            label="Estimated Runtime"
            value={estimatedRuntimeSec != null ? `~${fmtNum(estimatedRuntimeSec, 0)}s` : "—"}
          />
        </div>
      )}

      {running && (
        <div className="bt-progress">
          <div className="bt-progress-head">
            <Timer size={13} />
            <span>Replaying candles…</span>
            <b>{Math.round(progress * 100)}%</b>
          </div>
          <div className="bt-progress-track">
            <div className="bt-progress-fill" style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
        </div>
      )}

      {!datasetLoading && (rows == null || rows === 0) && (
        <p className="bt-note">
          No stored candles for {symbol} {timeframe.toUpperCase()} — download the dataset from the
          Research Lab's Data Engine first, then run the backtest.
        </p>
      )}
    </div>
  );
}
