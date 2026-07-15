import { memo } from "react";
import { formatMarketRegime, pct01 } from "../../lib/activeDrive";

type Signal = "bullish" | "bearish" | "neutral";
type Tone = "green" | "red" | "yellow";

type StrategyResult = {
  direction: string;
  confidence: number;
};

type Features = {
  atr?: number | null;
  rsi?: number | null;
  macd_hist?: number | null;
};

type Risk = {
  allowed: boolean;
  reason: string;
};

type DataQuality = {
  source?: string;
  provider_ok?: boolean;
  quality_score?: number | null;
  reliable?: boolean;
  reason?: string | null;
};

type Prediction = {
  direction?: string;
  confidence?: number;
  probability_up?: number;
  probability_down?: number;
  regime?: any;
  feature_regime?: string;
  stop?: number;
  target?: number;
  features?: Features;
  strategies?: Record<string, StrategyResult>;
  risk?: Risk;
  data_quality?: DataQuality;
  decision_engine?: any;
};

type Props = {
  symbol: string;
  prediction: Prediction | null;
  lastUpdated: Date | null;
};

function signalFromDirection(direction?: string): Signal {
  if (direction === "LONG") return "bullish";
  if (direction === "SHORT") return "bearish";
  return "neutral";
}

function toneClass(signal: Signal): Tone {
  if (signal === "bullish") return "green";
  if (signal === "bearish") return "red";
  return "yellow";
}

function fmtUsd(n?: number | null): string {
  if (typeof n !== "number") return "—";
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function fmtNum(n?: number | null, digits = 2): string {
  return typeof n === "number" ? n.toFixed(digits) : "—";
}

function AIDecisionCenter({ symbol, prediction, lastUpdated }: Props) {
  const signal = signalFromDirection(prediction?.direction);
  const tone = toneClass(signal);

  const risk = prediction?.risk;
  const riskTone: Tone = risk ? (risk.allowed ? "green" : "red") : "yellow";

  const strategies = Object.entries(prediction?.strategies ?? {});
  const bullish = strategies.filter(([, v]) => v.direction === "LONG").length;
  const bearish = strategies.filter(([, v]) => v.direction === "SHORT").length;
  const neutral = strategies.length - bullish - bearish;

  const macdHist = prediction?.features?.macd_hist;
  const v2 = prediction?.decision_engine;
  const noTrade = prediction?.direction === "NO_TRADE";
  const confidenceDiagnostics = v2?.confidence_diagnostics ?? {};
  const finalProbabilityAvailable = typeof prediction?.probability_up === "number";
  const displayUp = finalProbabilityAvailable ? prediction?.probability_up : typeof confidenceDiagnostics.indicative_point_score_up === "number" ? confidenceDiagnostics.indicative_point_score_up * 100 : null;
  const displayDown = finalProbabilityAvailable ? prediction?.probability_down : typeof confidenceDiagnostics.indicative_point_score_down === "number" ? confidenceDiagnostics.indicative_point_score_down * 100 : null;

  // data_quality comes from the Phase 20 data engine: reliable=false means
  // the risk gate is blocking on data grounds; source cached_db means the
  // provider was down and stored candles served this prediction.
  const dataQuality = prediction?.data_quality;
  const dataFeedLabel = !dataQuality
    ? "—"
    : dataQuality.reliable === false
      ? "UNRELIABLE"
      : dataQuality.source === "cached_db"
        ? "CACHED"
        : "LIVE";
  const dataFeedTone: Tone = !dataQuality ? "yellow" : dataQuality.reliable === false ? "red" : dataQuality.source === "cached_db" ? "yellow" : "green";

  return (
    <div className="decision-center">
      <div className="regime-banner">
        <span className={`flow-dot ${tone} ${prediction ? "flow-pulse" : ""}`} />
        <div>
          <h3>
            {symbol} · {prediction?.direction ?? "Waiting for data"}
          </h3>
          <p className="regime-desc">
            {prediction
              ? noTrade
                ? (v2?.blocking_reasons?.[0] ?? "No actionable decision")
                : `${pct01(v2?.directional_confidence)} directional confidence · ${fmtNum(prediction.probability_up, 0)}% up / ${fmtNum(prediction.probability_down, 0)}% down`
              : "Waiting for the AI pipeline to produce a decision."}
          </p>
        </div>
        <span className="decision-updated">
          {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : "Not updated yet"}
        </span>
      </div>

      <div className="analytics-grid">
        <div className="analytics-tile">
          <span className="tile-label">{finalProbabilityAvailable ? "Calibrated Probability Up" : "Indicative Point Score Up"}</span>
          <b className="tile-value green">{displayUp == null ? "—" : `${fmtNum(displayUp, 0)}%`}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">{finalProbabilityAvailable ? "Calibrated Probability Down" : "Indicative Point Score Down"}</span>
          <b className="tile-value red">{displayDown == null ? "—" : `${fmtNum(displayDown, 0)}%`}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Market Regime</span>
          <b className="tile-value">{formatMarketRegime(v2?.market_regime ?? prediction?.regime)}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Feature Regime</span>
          <b className="tile-value">{prediction?.feature_regime ?? "—"}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Stop</span>
          <b className="tile-value red">{noTrade ? "Not applicable" : fmtUsd(prediction?.stop)}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Target</span>
          <b className="tile-value green">{noTrade ? "Not applicable" : fmtUsd(prediction?.target)}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">ATR</span>
          <b className="tile-value">{fmtNum(prediction?.features?.atr)}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">RSI</span>
          <b className="tile-value">{fmtNum(prediction?.features?.rsi)}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">MACD Histogram</span>
          <b className={`tile-value ${(macdHist ?? 0) >= 0 ? "green" : "red"}`}>{fmtNum(macdHist, 4)}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Data Feed</span>
          <b className={`tile-value ${dataFeedTone}`}>{dataFeedLabel}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Data Quality Score</span>
          <b className="tile-value">
            {typeof dataQuality?.quality_score === "number" ? fmtNum(dataQuality.quality_score, 1) : "—"}
          </b>
        </div>
      </div>

      <div className={`regime-focus ${risk ? (risk.allowed ? "allowed" : "blocked") : ""}`}>
        <span className="tile-label">Risk Manager</span>
        <b className={`tile-value ${riskTone}`}>{risk ? (risk.allowed ? "ALLOWED" : "BLOCKED") : "Awaiting decision"}</b>
        <p className="regime-desc">{risk?.reason ?? "Waiting for the risk gate to evaluate this trade."}</p>
      </div>

      <div className="analytics-section">
        <div className="card-title">Strategy Summary</div>
        <p className="regime-desc">
          {strategies.length
            ? `${strategies.length} strategies · ${bullish} bullish, ${bearish} bearish, ${neutral} neutral`
            : "Waiting for strategy votes."}
        </p>
        {strategies.length > 0 && (
          <div className="chip-row">
            {strategies.map(([name, v]) => (
              <span className={`chip ${toneClass(signalFromDirection(v.direction))}`} key={name}>
                {name.replaceAll("_", " ")}: {v.direction}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(AIDecisionCenter);
