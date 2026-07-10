import { useEffect } from "react";
import Card from "../components/Layout/Card";
import PortfolioAnalytics from "../components/Dashboard/PortfolioAnalytics";
import { fmtNum, fmtPct } from "../lib/format";
import LocalTime from "../components/LocalTime";
import type { AppData } from "../hooks/useAppData";

export default function PerformancePage(props: AppData) {
  const { portfolio, positions, history, learningPerformance, loadLearning } = props;

  useEffect(() => {
    loadLearning();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const byTimeframe: [string, any][] = Object.entries(learningPerformance?.by_timeframe ?? {});
  const reliability: any[] = (learningPerformance?.confidence_reliability ?? []).filter(
    (b: any) => b.predictions > 0
  );

  return (
    <div className="page-grid">
      <Card title="Portfolio Analytics" full>
        <PortfolioAnalytics portfolio={portfolio} positions={positions} history={history} />
      </Card>

      <Card title="Prediction Accuracy by Timeframe">
        {byTimeframe.length ? (
          <>
            <p className="regime-desc">
              From the learning loop's last evaluation (<LocalTime value={learningPerformance.evaluated_at} label="Evaluated" />) —
              predictions resolved against recorded outcomes only.
            </p>
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Timeframe</th>
                    <th>Predictions</th>
                    <th>Hit Rate</th>
                    <th>Avg Error</th>
                  </tr>
                </thead>
                <tbody>
                  {byTimeframe.map(([tf, s]) => (
                    <tr key={tf}>
                      <td>{tf}</td>
                      <td>{s.predictions}</td>
                      <td className={s.hit_rate_pct >= 50 ? "green" : "red"}>{fmtPct(s.hit_rate_pct, 1)}</td>
                      <td>{fmtPct(s.avg_error_pct, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="analytics-empty">
            No learning evaluation yet — run "Evaluate Prediction History" in the Research Lab.
          </p>
        )}
      </Card>

      <Card title="Model Confidence Reliability">
        {reliability.length ? (
          <>
            <p className="regime-desc">
              How often each stated-confidence bucket was actually right. A calibrated model's hit rate tracks its
              bucket.
            </p>
            <div className="table-wrap" style={{ marginTop: 12 }}>
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
                  {reliability.map((b: any) => (
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
          </>
        ) : (
          <p className="analytics-empty">
            No calibration data yet — run "Evaluate Prediction History" in the Research Lab.
          </p>
        )}
      </Card>
    </div>
  );
}
