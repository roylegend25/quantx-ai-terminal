type Regime = "TRENDING" | "RANGING" | "HIGH_VOL" | "NORMAL";
type Tone = "green" | "red" | "yellow";

type Features = {
  rsi?: number | null;
  bb_width?: number | null;
  realized_volatility?: number | null;
  trend_score?: number | null;
};

type Prediction = {
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

// Mirrors backend/app/strategy/regime.py::detect so the panel can classify the
// regime from data already present in prediction.features (no new API calls).
function classifyRegime(trendScore: number, volatility: number, bbWidth: number): Regime {
  if (trendScore >= 3) return "TRENDING";
  if (bbWidth < 0.006) return "RANGING";
  if (volatility > 0.03) return "HIGH_VOL";
  return "NORMAL";
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

function bollingerLabel(bbw: number): string {
  if (bbw < 0.006) return "Tight";
  if (bbw > 0.02) return "Wide";
  return "Normal";
}

function rsiState(rsi: number): { label: string; tone: Tone } {
  if (rsi > 70) return { label: "Overbought", tone: "red" };
  if (rsi < 30) return { label: "Oversold", tone: "green" };
  return { label: "Neutral", tone: "yellow" };
}

export default function MarketRegimePanel({ prediction }: Props) {
  const features = prediction?.features;
  const ready =
    !!features &&
    typeof features.trend_score === "number" &&
    typeof features.realized_volatility === "number" &&
    typeof features.bb_width === "number";

  const trendScore = features?.trend_score ?? 0;
  const volatility = features?.realized_volatility ?? 0;
  const bbWidth = features?.bb_width ?? 0;
  const rsi = features?.rsi;

  const regime = ready ? classifyRegime(trendScore, volatility, bbWidth) : null;
  const rsiInfo = typeof rsi === "number" ? rsiState(rsi) : null;

  return (
    <div className="regime-panel">
      <div className="regime-banner">
        <span className={`flow-dot ${regime ? REGIME_TONE[regime] : "yellow"} ${regime ? "flow-pulse" : ""}`} />
        <div>
          <h3>{regime ?? "Waiting for data"}</h3>
          <p className="regime-desc">
            {regime
              ? REGIME_DESCRIPTIONS[regime]
              : "Waiting for the feature engine to compute the current regime."}
          </p>
        </div>
      </div>

      <div className="analytics-grid">
        <div className="analytics-tile">
          <span className="tile-label">Trend Strength</span>
          <b className="tile-value">{ready ? `${trendScore}/3 · ${trendLabel(trendScore)}` : "—"}</b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Volatility Level</span>
          <b className="tile-value">
            {ready ? `${(volatility * 100).toFixed(2)}% · ${volatilityLabel(volatility)}` : "—"}
          </b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">Bollinger Width</span>
          <b className="tile-value">
            {ready ? `${(bbWidth * 100).toFixed(2)}% · ${bollingerLabel(bbWidth)}` : "—"}
          </b>
        </div>

        <div className="analytics-tile">
          <span className="tile-label">RSI State</span>
          <b className={`tile-value ${rsiInfo ? rsiInfo.tone : ""}`}>
            {typeof rsi === "number" ? `${rsi.toFixed(1)} · ${rsiInfo?.label}` : "—"}
          </b>
        </div>
      </div>

      <div className="regime-focus">
        <span className="tile-label">Recommended Strategy Focus</span>
        <b className="tile-value">{regime ? STRATEGY_FOCUS[regime] : "Waiting for regime classification"}</b>
      </div>
    </div>
  );
}
