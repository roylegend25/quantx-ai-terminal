import { fmtPct, fmtUsd } from "../../lib/format";
import {
  drawdownCurveOf,
  equityCurveOf,
  historicalVaR95,
  maxDrawdownOf,
  marginUsagePct,
  riskLevelOf,
  type Portfolio,
  type Position,
  type Trade,
} from "../../lib/portfolioStats";

type Props = {
  portfolio: Portfolio | null;
  positions: Position[];
  history: Trade[];
};

export default function RiskStatusCard({ portfolio, positions, history }: Props) {
  const margin = marginUsagePct(positions, portfolio);
  const risk = riskLevelOf(margin);

  const equityCurve = equityCurveOf(portfolio, history);
  const drawdownCurve = drawdownCurveOf(equityCurve);
  const maxDrawdown = maxDrawdownOf(drawdownCurve);
  const var95 = historicalVaR95(history);

  return (
    <div className="stack-card">
      <div className="card-title">Risk Status</div>

      <div className={`half-dial tone-${risk.tone}`} style={{ ["--pct" as any]: margin }}>
        <div className="half-dial-fill" />
        <div className="half-dial-inner">
          <b className={risk.tone}>{risk.label}</b>
          <span>{fmtPct(margin, 1)}</span>
        </div>
      </div>

      <div className="kv-grid">
        <div>
          <span className="tile-label">Max Drawdown</span>
          <b className="tile-value red">{fmtPct(Math.abs(maxDrawdown), 1)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">VaR (95%)</span>
          <b className="tile-value">{var95 !== null ? fmtUsd(var95) : "—"}</b>
        </div>
      </div>
    </div>
  );
}
