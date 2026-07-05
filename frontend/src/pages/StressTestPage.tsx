import { Fragment, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, PlayCircle, RotateCw, XCircle } from "lucide-react";
import Card from "../components/Layout/Card";
import type { AppData } from "../hooks/useAppData";

type Props = AppData;

type Check = { ok: boolean; detail: string };

type StressResult = {
  scenario_id: string;
  scenario_name: string;
  category?: string;
  status: "PASSED" | "FAILED";
  reason: string;
  checks?: Record<string, Check>;
  new_trades_blocked?: boolean;
  open_positions_protected?: boolean;
  positions_detail?: string;
  risk_result?: { allowed?: boolean | null; reason?: string } | null;
  timestamp?: string;
};

const COMPONENT_LABELS: Record<string, string> = {
  prediction_api: "Prediction Pipeline",
  scheduler: "Scheduler",
  position_manager: "Position Manager",
  risk_engine: "Risk Engine",
};

const COMPONENT_EXPECTED: Record<string, string> = {
  prediction_api: "Handles the failure gracefully - no crash, no fabricated signal.",
  scheduler: "Isolates a failed cycle; the background loop keeps running.",
  position_manager: "Isolates a failed poll; open positions stay protected.",
  risk_engine: "Blocks unsafe trades or degrades safely with a clear reason.",
};

function StatusBadge({ status }: { status?: string }) {
  if (status === "PASSED") {
    return (
      <span className="badge badge-green" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <CheckCircle2 size={12} /> PASSED
      </span>
    );
  }
  if (status === "FAILED") {
    return (
      <span className="badge badge-red" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <XCircle size={12} /> FAILED
      </span>
    );
  }
  return <span className="badge">—</span>;
}

export default function StressTestPage({ stressReport, runStressTest, showToast }: Props) {
  const [running, setRunning] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const summary = stressReport?.summary;
  const results: StressResult[] = stressReport?.results || [];

  function toggleExpanded(scenarioId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(scenarioId)) next.delete(scenarioId);
      else next.add(scenarioId);
      return next;
    });
  }

  async function handleRun(scenarioId?: string) {
    setRunning(scenarioId || "__all__");
    try {
      const res = await runStressTest(scenarioId);
      showToast(
        scenarioId
          ? `${scenarioId}: ${res.results?.[0]?.status ?? "done"}`
          : `Stress test complete: ${res.summary.passed}/${res.summary.total} passed`
      );
    } catch {
      showToast("Stress test run failed");
    } finally {
      setRunning(null);
    }
  }

  return (
    <div className="page-grid">
      <Card title="System Stress Test" full>
        <p className="regime-desc">
          Fault-injection checks that verify prediction, scheduler, position-manager and risk-engine
          behavior under bad market/data/infrastructure conditions. Every scenario is read-only against
          paper trading — no trade is ever opened or closed by these tests, and live trading is never
          enabled.
        </p>

        <div className="controls" style={{ marginTop: 14 }}>
          <button onClick={() => handleRun()} disabled={running !== null}>
            <PlayCircle size={16} />
            {running === "__all__" ? "Running all…" : "Run All Scenarios"}
          </button>
        </div>

        <div className="analytics-grid" style={{ marginTop: 16 }}>
          <div className="analytics-tile">
            <span className="tile-label">Scenarios</span>
            <b className="tile-value">{summary?.total ?? "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Passed</span>
            <b className="tile-value green">{summary?.passed ?? "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Failed</span>
            <b className={`tile-value ${summary?.failed ? "red" : ""}`}>{summary?.failed ?? "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Last Run</span>
            <b className="tile-value">
              {stressReport?.generated_at ? new Date(stressReport.generated_at).toLocaleString() : "—"}
            </b>
          </div>
        </div>
      </Card>

      <Card title="Scenario Results" full>
        {results.length === 0 ? (
          <p className="analytics-empty">No stress test has been run yet. Click "Run All Scenarios" above.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th />
                  <th>Scenario</th>
                  <th>Status</th>
                  <th>Reason</th>
                  <th>Risk Result</th>
                  <th>Timestamp</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {results.map((r) => {
                  const isOpen = expanded.has(r.scenario_id);
                  return (
                    <Fragment key={r.scenario_id}>
                      <tr>
                        <td style={{ width: 28 }}>
                          <button
                            className="link-btn"
                            style={{ padding: 0 }}
                            onClick={() => toggleExpanded(r.scenario_id)}
                            title={isOpen ? "Hide details" : "Show expected vs actual per component"}
                          >
                            {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          </button>
                        </td>
                        <td style={{ whiteSpace: "normal", minWidth: 160 }}>
                          <b>{r.scenario_name}</b>
                          <div className="tile-label" style={{ marginTop: 2 }}>
                            {r.category?.replace(/_/g, " ")}
                          </div>
                        </td>
                        <td>
                          <StatusBadge status={r.status} />
                        </td>
                        <td style={{ whiteSpace: "normal", maxWidth: 440 }}>{r.reason}</td>
                        <td style={{ whiteSpace: "normal", maxWidth: 220 }}>
                          {r.risk_result?.reason || "—"}
                          {r.risk_result?.allowed !== undefined && r.risk_result?.allowed !== null && (
                            <div
                              className={r.risk_result.allowed ? "green" : "red"}
                              style={{ display: "block", marginTop: 4 }}
                            >
                              {r.risk_result.allowed ? "Allowed" : "Blocked"}
                            </div>
                          )}
                        </td>
                        <td>{r.timestamp ? new Date(r.timestamp).toLocaleTimeString() : "—"}</td>
                        <td>
                          <button
                            className="link-btn"
                            style={{ padding: 0, display: "inline-flex", alignItems: "center", gap: 4 }}
                            disabled={running !== null}
                            onClick={() => handleRun(r.scenario_id)}
                            title="Re-run this scenario"
                          >
                            <RotateCw size={13} /> Re-run
                          </button>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr>
                          <td />
                          <td colSpan={6} style={{ whiteSpace: "normal", padding: 0 }}>
                            <div className="stress-detail-panel">
                              {r.checks &&
                                Object.entries(r.checks).map(([component, check]) => (
                                  <div className="stress-detail-row" key={component}>
                                    <div className="stress-detail-component">
                                      {check.ok ? (
                                        <CheckCircle2 size={13} className="green" />
                                      ) : (
                                        <XCircle size={13} className="red" />
                                      )}
                                      <b>{COMPONENT_LABELS[component] || component}</b>
                                    </div>
                                    <div className="stress-detail-copy">
                                      <span className="tile-label">Expected</span>
                                      <p>{COMPONENT_EXPECTED[component] || "Behaves safely under this fault."}</p>
                                      <span className="tile-label">Actual</span>
                                      <p>{check.detail}</p>
                                    </div>
                                  </div>
                                ))}

                              <div className="stress-detail-row">
                                <div className="stress-detail-component">
                                  <b>Positions</b>
                                </div>
                                <div className="stress-detail-copy">
                                  <span className="tile-label">Actual</span>
                                  <p>{r.positions_detail || "No open paper positions at test time."}</p>
                                </div>
                              </div>

                              <div className="stress-detail-row">
                                <div className="stress-detail-component">
                                  <b>Recommendation</b>
                                </div>
                                <div className="stress-detail-copy">
                                  <p>{r.reason}</p>
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
