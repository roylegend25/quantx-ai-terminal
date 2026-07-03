import { useState } from "react";
import {
  Activity,
  AlertTriangle,
  Archive,
  Award,
  CheckCircle2,
  Crown,
  GitBranch,
  History,
  PlayCircle,
  RotateCw,
  Target,
  TrendingUp,
  XCircle,
} from "lucide-react";
import Card from "../components/Layout/Card";
import { fmtDuration, fmtNum, fmtPct, fmtRelativeTime } from "../lib/format";
import type { AppData } from "../hooks/useAppData";

type Props = AppData;

const ALGORITHMS = ["xgboost", "lightgbm", "catboost"];

type ModelRow = {
  model_id: string;
  model_name: string;
  algorithm?: string | null;
  version: string;
  status: string;
  trained_at?: string | null;
  train_accuracy?: number | null;
  val_accuracy?: number | null;
  win_rate?: number | null;
  sharpe_ratio?: number | null;
  profit_factor?: number | null;
  max_drawdown_pct?: number | null;
  latency_ms?: number | null;
  training_duration_seconds?: number | null;
  dataset_version?: string | null;
  feature_version?: string | null;
  git_commit?: string | null;
};

type DriftRecord = {
  method: string;
  score: number | null;
  threshold: number | null;
  is_drifted: boolean;
} | null;

type ExperimentRow = {
  experiment_id: string;
  model_name: string;
  algorithm?: string | null;
  status: string;
  training_duration_seconds?: number | null;
  started_at?: string | null;
};

function StatusBadge({ status }: { status?: string | null }) {
  const cls =
    status === "Champion" ? "badge-green" : status === "Failed" ? "badge-red" : "";
  return <span className={`badge ${cls}`}>{status || "—"}</span>;
}

function accuracyPct(m: Pick<ModelRow, "val_accuracy" | "train_accuracy"> | null | undefined): number | null {
  const raw = m?.val_accuracy ?? m?.train_accuracy;
  return raw != null ? raw * 100 : null;
}

function DriftTile({ label, record }: { label: string; record: DriftRecord | undefined }) {
  const drifted = record?.is_drifted;
  const known = record != null && record.score != null;
  return (
    <div className={`regime-focus ${known ? (drifted ? "blocked" : "allowed") : ""}`}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <b>{label}</b>
        {known ? (
          drifted ? (
            <AlertTriangle size={16} className="red" />
          ) : (
            <CheckCircle2 size={16} className="green" />
          )
        ) : null}
      </div>
      <p className="regime-desc">
        {known
          ? `${record.method} score ${fmtNum(record.score, 3)} (threshold ${fmtNum(record.threshold, 2)})`
          : "No scan recorded yet."}
      </p>
    </div>
  );
}

export default function ModelCenterPage({ modelCenter, trainModel, promoteModel, rollbackModel, archiveModel, showToast }: Props) {
  const [busy, setBusy] = useState<string | null>(null);

  const champion: ModelRow | null = modelCenter?.champion || null;
  const models: ModelRow[] = modelCenter?.models || [];
  const experiments: ExperimentRow[] = modelCenter?.experiments || [];
  const drift: { feature?: DriftRecord; prediction?: DriftRecord; market?: DriftRecord } = modelCenter?.drift || {};

  const challengers = models.filter((m) => m.status === "Challenger");
  const history = champion
    ? models.filter((m) => m.model_name === champion.model_name)
    : models.slice(0, 10);

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

  return (
    <div className="page-grid">
      <Card
        title="Champion Model"
        right={champion ? <Crown size={16} className="green" /> : undefined}
        wide
      >
        {champion ? (
          <>
            <div className="analytics-grid">
              <div className="analytics-tile">
                <span className="tile-label">Model</span>
                <b className="tile-value">{champion.model_name}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Version</span>
                <b className="tile-value">{champion.version}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Accuracy</span>
                <b className="tile-value">{fmtPct(accuracyPct(champion), 1)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Win Rate</span>
                <b className="tile-value">{fmtPct(champion.win_rate, 1)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Sharpe</span>
                <b className="tile-value">{fmtNum(champion.sharpe_ratio, 2)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Profit Factor</span>
                <b className="tile-value">{fmtNum(champion.profit_factor, 2)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Last Trained</span>
                <b className="tile-value">{fmtRelativeTime(champion.trained_at)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Training Duration</span>
                <b className="tile-value">{fmtDuration(champion.training_duration_seconds)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Dataset Version</span>
                <b className="tile-value">{champion.dataset_version || "—"}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Feature Version</span>
                <b className="tile-value">{champion.feature_version || "—"}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Max Drawdown</span>
                <b className="tile-value">{fmtPct(champion.max_drawdown_pct, 1)}</b>
              </div>
              <div className="analytics-tile">
                <span className="tile-label">Git Commit</span>
                <b className="tile-value">{champion.git_commit || "—"}</b>
              </div>
            </div>

            <div className="controls" style={{ marginTop: 16 }}>
              <button
                disabled={busy !== null}
                onClick={() => withBusy("rollback", () => rollbackModel(champion.model_id))}
              >
                <RotateCw size={16} />
                {busy === "rollback" ? "Rolling back…" : "Rollback to Previous Champion"}
              </button>
            </div>
          </>
        ) : (
          <p className="analytics-empty">No Champion model has been promoted yet.</p>
        )}
      </Card>

      <Card title="Model Health &amp; Drift" right={<Activity size={16} />}>
        <div className="chip-row" style={{ marginTop: 0, flexDirection: "column", alignItems: "stretch", gap: 10 }}>
          <DriftTile label="Feature Drift" record={drift.feature} />
          <DriftTile label="Prediction Drift" record={drift.prediction} />
          <DriftTile label="Market Drift" record={drift.market} />
        </div>
      </Card>

      <Card title="Train a Challenger" right={<PlayCircle size={16} />}>
        <p className="regime-desc">
          Manually kicks off a retrain for one algorithm. The result is registered as a new version and
          auto-promoted to Champion only if every configured threshold is met.
        </p>
        <div className="controls" style={{ marginTop: 14, flexWrap: "wrap" }}>
          {ALGORITHMS.map((algo) => (
            <button
              key={algo}
              disabled={busy !== null}
              onClick={() =>
                withBusy(`train-${algo}`, async () => {
                  const res = await trainModel(algo, algo);
                  showToast(res?.status === "success" ? `${algo}: trained ${res.model?.version}` : `${algo}: ${res?.status || "done"}`);
                })
              }
            >
              {busy === `train-${algo}` ? "Training…" : `Train ${algo}`}
            </button>
          ))}
        </div>
      </Card>

      <Card title="Champion vs Challengers" full right={<Target size={16} />}>
        {challengers.length === 0 ? (
          <p className="analytics-empty">No challengers pending review.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Version</th>
                  <th>Accuracy</th>
                  <th>Win Rate</th>
                  <th>Sharpe</th>
                  <th>Profit Factor</th>
                  <th>Drawdown</th>
                  <th>Latency</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {challengers.map((m) => (
                  <tr key={m.model_id}>
                    <td>
                      <b>{m.model_name}</b>
                      <div className="tile-label" style={{ marginTop: 2 }}>{m.algorithm}</div>
                    </td>
                    <td>{m.version}</td>
                    <td>{fmtPct(accuracyPct(m), 1)}</td>
                    <td>{fmtPct(m.win_rate, 1)}</td>
                    <td>{fmtNum(m.sharpe_ratio, 2)}</td>
                    <td>{fmtNum(m.profit_factor, 2)}</td>
                    <td>{fmtPct(m.max_drawdown_pct, 1)}</td>
                    <td>{m.latency_ms != null ? `${fmtNum(m.latency_ms, 0)}ms` : "—"}</td>
                    <td style={{ display: "flex", gap: 10 }}>
                      <button
                        className="link-btn"
                        style={{ padding: 0, display: "inline-flex", alignItems: "center", gap: 4 }}
                        disabled={busy !== null}
                        onClick={() =>
                          withBusy(`promote-${m.model_id}`, async () => {
                            const res = await promoteModel(m.model_id);
                            showToast(res?.promoted ? `${m.model_name} ${m.version} promoted` : "Not all thresholds met");
                          })
                        }
                      >
                        <Award size={13} /> Promote
                      </button>
                      <button
                        className="link-btn"
                        style={{ padding: 0, display: "inline-flex", alignItems: "center", gap: 4 }}
                        disabled={busy !== null}
                        onClick={() =>
                          withBusy(`archive-${m.model_id}`, async () => {
                            await archiveModel(m.model_id);
                            showToast(`${m.model_name} ${m.version} archived`);
                          })
                        }
                      >
                        <Archive size={13} /> Archive
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Version Timeline" right={<GitBranch size={16} />}>
        {history.length === 0 ? (
          <p className="analytics-empty">No trained versions yet.</p>
        ) : (
          <div className="chip-row" style={{ marginTop: 0, flexDirection: "column", alignItems: "stretch", gap: 8 }}>
            {history.map((m) => (
              <div key={m.model_id} className="regime-focus" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <div>
                  <b>{m.version}</b>
                  <span className="tile-label" style={{ marginLeft: 8 }}>{fmtRelativeTime(m.trained_at)}</span>
                </div>
                <StatusBadge status={m.status} />
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Experiment History" right={<History size={16} />}>
        {experiments.length === 0 ? (
          <p className="analytics-empty">No experiments recorded yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Experiment</th>
                  <th>Model</th>
                  <th>Status</th>
                  <th>Duration</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {experiments.slice(0, 20).map((e) => (
                  <tr key={e.experiment_id}>
                    <td>{e.experiment_id}</td>
                    <td>
                      {e.model_name}
                      <div className="tile-label" style={{ marginTop: 2 }}>{e.algorithm}</div>
                    </td>
                    <td>
                      {e.status === "success" ? (
                        <span className="badge badge-green" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                          <CheckCircle2 size={12} /> success
                        </span>
                      ) : e.status === "failed" ? (
                        <span className="badge badge-red" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                          <XCircle size={12} /> failed
                        </span>
                      ) : (
                        <span className="badge">{e.status}</span>
                      )}
                    </td>
                    <td>{fmtDuration(e.training_duration_seconds)}</td>
                    <td>{fmtRelativeTime(e.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Model Registry" full right={<TrendingUp size={16} />}>
        {models.length === 0 ? (
          <p className="analytics-empty">No models registered yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Algorithm</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Accuracy</th>
                  <th>Trained</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.model_id}>
                    <td>{m.model_name}</td>
                    <td>{m.algorithm || "—"}</td>
                    <td>{m.version}</td>
                    <td>
                      <StatusBadge status={m.status} />
                    </td>
                    <td>{fmtPct(accuracyPct(m), 1)}</td>
                    <td>{fmtRelativeTime(m.trained_at)}</td>
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
