import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { fmtNum, fmtPct, fmtUsd } from "../../lib/format";
import { formatCompactLocalDateTime } from "../../utils/dateTime";
import {
  describeConfidence,
  describeEvidence,
  formatHorizonMs,
  formatTimeframeLong,
  normalizeDecision,
} from "../../lib/decisionSummary";

type Props = {
  prediction: any;
  /** Chart interval - fallback for the source timeframe and horizon when the
   *  decision payload has not loaded yet. */
  interval: string;
  intervalMs: number;
  forecastBars: number;
  lastClose: number | null;
};

function fmtSignedPct(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

/** The single authoritative Active Drive V2 decision summary. Every value is
 *  derived from one normalized decision model; no other dashboard component
 *  may render its own Direction/Confidence/Target/Stop/Horizon cards. */
export default function DecisionSummaryTiles({ prediction, interval, intervalMs, forecastBars, lastClose }: Props) {
  const d = normalizeDecision(prediction);
  const isNoTrade = !d.actionable;

  const directionText = d.signal === "LONG" ? "BULLISH" : d.signal === "SHORT" ? "BEARISH" : "NO TRADE";
  const directionTone = d.signal === "LONG" ? "green" : d.signal === "SHORT" ? "red" : "yellow";
  const DirectionIcon = d.signal === "LONG" ? ArrowUpRight : d.signal === "SHORT" ? ArrowDownRight : Minus;

  const confidence = d.confidence.calibratedPct;
  const confidenceLabel =
    !isNoTrade && confidence != null
      ? confidence >= 80
        ? "High Confidence"
        : confidence >= 60
        ? "Medium Confidence"
        : "Low Confidence"
      : describeConfidence(d);

  const sourceTimeframe = d.sourceTimeframe ?? interval;
  const horizonText = formatHorizonMs(d.horizonMs ?? forecastBars * intervalMs);
  const updatedAt = d.updatedAtMs != null ? formatCompactLocalDateTime(d.updatedAtMs) : null;

  const targetPct = lastClose && d.target != null ? ((d.target - lastClose) / lastClose) * 100 : NaN;
  const stopPct = lastClose && d.stop != null ? ((d.stop - lastClose) / lastClose) * 100 : NaN;

  const signalSub = isNoTrade
    ? d.primaryBlockReason ?? "No qualifying trade signal"
    : confidence != null && confidence >= 80
    ? `Strong ${d.signal === "LONG" ? "Buy" : "Sell"} Signal`
    : confidence != null && confidence >= 60
    ? `${d.signal === "LONG" ? "Buy" : "Sell"} Signal`
    : "Weak Signal";

  return (
    <>
      <div className="pc-summary">
        <div className="pc-tile">
          <span className="tile-label">Signal</span>
          <div className={`pc-direction-badge ${directionTone}`}>
            <DirectionIcon size={16} />
            {directionText}
          </div>
          <span className="pc-tile-sub">{signalSub}</span>
        </div>

        {isNoTrade && (
          <div className="pc-tile">
            <span className="tile-label">Forecast (informational)</span>
            <b className="tile-value">{d.forecastDirection ?? "Unavailable"}</b>
            <span className="pc-tile-sub">Not a trade signal</span>
          </div>
        )}

        <div className="pc-tile">
          <span className="tile-label">Confidence</span>
          {isNoTrade || confidence == null ? (
            <b className="tile-value">Not established</b>
          ) : (
            <div className="pc-mini-gauge" style={{ ["--pct" as any]: confidence, ["--tone" as any]: `var(--c-${directionTone})` }}>
              <div className="pc-mini-gauge-inner">{`${fmtNum(confidence, 0)}%`}</div>
            </div>
          )}
          <span className="pc-tile-sub">{confidenceLabel}</span>
        </div>

        <div className="pc-tile">
          <span className="tile-label">Evidence</span>
          <b className="tile-value">
            {d.evidence.samples != null && d.evidence.required != null
              ? `${d.evidence.samples} / ${d.evidence.required}`
              : "Unavailable"}
          </b>
          <span className="pc-tile-sub">
            {describeEvidence(d)}
            {d.evidence.estimatedReadyAt &&
              Number.isFinite(Date.parse(d.evidence.estimatedReadyAt)) &&
              ` · Est. ready ${formatCompactLocalDateTime(Date.parse(d.evidence.estimatedReadyAt))}`}
          </span>
        </div>

        {!isNoTrade && (
          <div className="pc-tile">
            <span className="tile-label">Entry Price</span>
            <b className="tile-value">{fmtUsd(d.entryPrice)}</b>
            <span className="pc-tile-sub">Reference price at decision time</span>
          </div>
        )}

        {!isNoTrade && (
          <div className="pc-tile">
            <span className="tile-label">Price Target</span>
            <b className="tile-value green">{fmtUsd(d.target)}</b>
            <span className="pc-tile-sub green">{fmtSignedPct(targetPct)}</span>
          </div>
        )}

        {!isNoTrade && (
          <div className="pc-tile">
            <span className="tile-label">Stop Loss</span>
            <b className="tile-value red">{fmtUsd(d.stop)}</b>
            <span className="pc-tile-sub red">{fmtSignedPct(stopPct)}</span>
          </div>
        )}

        {!isNoTrade && (
          <div className="pc-tile">
            <span className="tile-label">Reward/Risk</span>
            <b className="tile-value">{d.rewardRisk != null ? `${fmtNum(d.rewardRisk, 2)} : 1` : "—"}</b>
            <span className="pc-tile-sub">Target distance vs stop distance</span>
          </div>
        )}

        {!isNoTrade && (
          <div className="pc-tile">
            <span className="tile-label">Expected Edge</span>
            <b className="tile-value">{d.expectedEdge != null ? fmtPct(d.expectedEdge * 100, 2) : "—"}</b>
            <span className="pc-tile-sub">Net of estimated fees, spread, slippage</span>
          </div>
        )}

        {!isNoTrade && (
          <div className="pc-tile">
            <span className="tile-label">Authority Status</span>
            <b className={`tile-value ${d.authorityStatus === "eligible" ? "green" : "red"}`}>
              {d.authorityStatus === "eligible" ? "Eligible" : "Blocked"}
            </b>
            <span className="pc-tile-sub">{d.authorityStatus === "eligible" ? "Cleared for execution" : d.authorityReason ?? "Execution not authorized"}</span>
          </div>
        )}

        {!isNoTrade && (
          <div className="pc-tile">
            <span className="tile-label">Risk Status</span>
            <b className={`tile-value ${d.riskStatus === "passed" ? "green" : "red"}`}>
              {d.riskStatus === "passed" ? "Passed" : "Not Passed"}
            </b>
            <span className="pc-tile-sub">Engine risk gate</span>
          </div>
        )}

        <div className="pc-tile">
          <span className="tile-label">Source Timeframe</span>
          <b className="tile-value">{formatTimeframeLong(sourceTimeframe)}</b>
          <span className="pc-tile-sub">Model candle interval</span>
        </div>

        <div className="pc-tile">
          <span className="tile-label">Prediction Horizon</span>
          <b className="tile-value">{horizonText}</b>
          <span className="pc-tile-sub">{updatedAt ? `Updated ${updatedAt}` : "—"}</span>
        </div>
      </div>

      {isNoTrade && (
        <p className="pc-summary-note">
          Trade levels are not created for an abstention.
          {d.secondaryBlockReasons.length > 0 && ` Also blocked: ${d.secondaryBlockReasons.join("; ")}.`}
        </p>
      )}
    </>
  );
}
