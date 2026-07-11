import { useEffect } from "react";
import Card from "../components/Layout/Card";
import PortfolioAnalytics from "../components/Dashboard/PortfolioAnalytics";
import { fmtNum, fmtPct } from "../lib/format";
import LocalTime from "../components/LocalTime";
import AutoCardTable, { type AutoCardColumn } from "../components/Responsive/AutoCardTable";
import type { AppData } from "../hooks/useAppData";

const TIMEFRAME_COLUMNS: AutoCardColumn<[string, any]>[] = [
  { key: "tf", label: "Timeframe", render: ([tf]) => tf },
  { key: "predictions", label: "Predictions", render: ([, s]) => s.predictions },
  { key: "hitRate", label: "Hit Rate", render: ([, s]) => <span className={s.hit_rate_pct >= 50 ? "green" : "red"}>{fmtPct(s.hit_rate_pct, 1)}</span> },
  { key: "avgError", label: "Avg Error", render: ([, s]) => fmtPct(s.avg_error_pct, 2) },
];

const RELIABILITY_COLUMNS: AutoCardColumn<any>[] = [
  { key: "bucket", label: "Confidence Bucket", render: (b) => b.bucket },
  { key: "predictions", label: "Predictions", render: (b) => b.predictions },
  { key: "hitRate", label: "Actual Hit Rate", render: (b) => fmtPct(b.hit_rate_pct, 1) },
  { key: "statedConfidence", label: "Stated Confidence", render: (b) => fmtNum(b.avg_confidence, 1) },
];

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
            <div style={{ marginTop: 12 }}>
              <AutoCardTable columns={TIMEFRAME_COLUMNS} rows={byTimeframe} keyField={([tf]) => tf} titleColumn="tf" />
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
            <div style={{ marginTop: 12 }}>
              <AutoCardTable columns={RELIABILITY_COLUMNS} rows={reliability} keyField={(b) => b.bucket} titleColumn="bucket" />
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
