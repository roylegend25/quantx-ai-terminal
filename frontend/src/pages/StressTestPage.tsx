import { useState } from "react";
import { CheckCircle2, PlayCircle, RotateCw, XCircle } from "lucide-react";
import Card from "../components/Layout/Card";
import type { AppData } from "../hooks/useAppData";

type Props = AppData;

type StressResult = {
  scenario_id: string;
  scenario_name: string;
  category?: string;
  status: "PASSED" | "FAILED";
  reason: string;
  new_trades_blocked?: boolean;
  open_positions_protected?: boolean;
  risk_result?: { allowed?: boolean | null; reason?: string } | null;
  timestamp?: string;
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

  const summary = stressReport?.summary;
  const results: StressResult[] = stressReport?.results || [];

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
                  <th>Scenario</th>
                  <th>Status</th>
                  <th>Reason</th>
                  <th>Risk Result</th>
                  <th>Timestamp</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr key={r.scenario_id}>
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
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
