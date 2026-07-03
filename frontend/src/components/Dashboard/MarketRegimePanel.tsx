import { memo } from "react";

type Regime = "TRENDING" | "RANGING" | "HIGH_VOL" | "NORMAL";
type Tone = "green" | "red" | "yellow";

type Features = {
  rsi?: number | null;
  realized_volatility?: number | null;
  trend_score?: number | null;
};

type Prediction = {
  regime?: string;
  feature_regime?: string;
  features?: Features;
};

type Props = {
  prediction: Prediction | null;
};

const STRATEGY_FOCUS: Record<Regime, string> = {
  TRENDING: "Trend + Momentum",
  RANGING: "Mean Reversion",
  HIGH_VOL: "Breakout + Risk Reduction",
  NORMAL: "Balanced Ensemble",
};

const REGIME_DESCRIPTIONS: Record<Regime, string> = {
  TRENDING: "Strong directional alignment across EMAs — trend-following edge.",
  RANGING: "Price compressed inside a tight Bollinger band — mean-reversion edge.",
  HIGH_VOL: "Elevated realized volatility — favor breakouts, cut position size.",
  NORMAL: "No dominant regime — spread risk across the full strategy ensemble.",
};

const REGIME_TONE: Record<Regime, Tone> = {
  TRENDING: "green",
  RANGING: "yellow",
  HIGH_VOL: "red",
  NORMAL: "yellow",
};

function isRegime(value: string | undefined): value is Regime {
  return value === "TRENDING" || value === "RANGING" || value === "HIGH_VOL" || value === "NORMAL";
}

function trendLabel(score: number): string {
  if (score >= 3) return "Strong";
  if (score === 2) return "Moderate";
  if (score === 1) return "Weak";
  return "None";
}

function volatilityLabel(vol: number): string {
  if (vol > 0.03) return "High";
  if (vol > 0.015) return "Moderate";
  return "Low";
}

function rsiState(rsi: number): { label: string; tone: Tone } {
  if (rsi > 70) return { label: "Overbought", tone: "red" };
  if (rsi < 30) return { label: "Oversold", tone: "green" };
  return { label: "Neutral", tone: "yellow" };
}

function MarketRegimePanel({ prediction }: Props) {
  const regime = isRegime(prediction?.regime) ? prediction.regime : null;
  const featureRegime = prediction?.feature_regime;

  const trendScore = prediction?.features?.trend_score;
  const volatility = prediction?.features?.realized_volatility;
  const rsi = prediction?.features?.rsi;
  const rsiInfo = typeof rsi === "number" ? rsiState(rsi) : null;

  return (
    <div className="regime-panel">
      <div className="regime-banner">
        <span className={`flow-dot ${regime ? REGIME_TONE[regime] : "yellow"} ${regime ? "flow-pulse" : ""}`} />
        <div>
          <h3>{prediction?.regime ?? "Waiting for data"}</h3>
          <p className="regime-desc">
            {regime ? REGIME_DESCRIPTIONS[regime] : "Waiting for the backend to classify the current regime."}
          </p>
        </div>
      </div>

      <div className="analytics-grid">
        <div className="analytics-tile">
          <span className="tile-label">Feature Regime</span>
          <b className="tile-value">{featureRegime ?? "—"}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Trend Strength</span>
          <b className="tile-value">
            {typeof trendScore === "number" ? `${trendScore}/3 · ${trendLabel(trendScore)}` : "—"}
          </b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Volatility</span>
          <b className="tile-value">
            {typeof volatility === "number"
              ? `${(volatility * 100).toFixed(2)}% · ${volatilityLabel(volatility)}`
              : "—"}
          </b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">RSI</span>
          <b className={`tile-value ${rsiInfo ? rsiInfo.tone : ""}`}>
            {typeof rsi === "number" ? `${rsi.toFixed(1)} · ${rsiInfo?.label}` : "—"}
          </b>
        </div>
      </div>

      <div className="regime-focus">
        <span className="tile-label">Recommended Strategy</span>
        <b className="tile-value">{regime ? STRATEGY_FOCUS[regime] : "Waiting for regime classification"}</b>
      </div>
    </div>
  );
}

export default memo(MarketRegimePanel);
