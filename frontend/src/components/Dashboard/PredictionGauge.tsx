import { memo, useEffect, useRef, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { fmtPct } from "../../lib/format";
import { describeConfidence, normalizeDecision } from "../../lib/decisionSummary";

type Props = {
  prediction: any;
  lastUpdated: Date | null;
};

// Must match backend/app/api/prediction.py's PREDICTION_CACHE_TTL_SECONDS -
// that's the real cadence at which the backend recomputes a prediction.
const CYCLE_SECONDS = 60;

function directionTone(direction?: string): "green" | "red" | "yellow" {
  if (direction === "LONG") return "green";
  if (direction === "SHORT") return "red";
  return "yellow";
}

function PredictionGauge({ prediction, lastUpdated }: Props) {
  const [now, setNow] = useState(Date.now());
  // Single source of truth for the countdown: the epoch ms at which the
  // current cycle ends. Only ever written by the effect below, never derived
  // inline from `now`, so it can't drift or get stomped by an unrelated
  // render/poll.
  const [nextPredictionAt, setNextPredictionAt] = useState<number | null>(null);

  // Kept up to date every render without being a dependency of the effect
  // below, so using it as a fallback anchor doesn't force that effect to
  // re-run (and reset the countdown) on every poll tick.
  const lastUpdatedRef = useRef(lastUpdated);
  lastUpdatedRef.current = lastUpdated;

  // The one and only timer in this component: just re-renders every second
  // so the countdown display stays live. It never touches nextPredictionAt.
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  // Recomputes the countdown anchor ONLY when `prediction` itself changes.
  // useAppData dedupes the prediction state (deep-equality), so its identity
  // is stable across polls that return the same data - this effect simply
  // will not fire for those, which is what stops the countdown resetting on
  // every API refresh. It fires again once the backend genuinely produces a
  // new prediction (new computed_at / different values).
  useEffect(() => {
    if (!prediction) return;
    const backendAnchor =
      typeof prediction.next_prediction_at === "number"
        ? prediction.next_prediction_at
        : typeof prediction.computed_at === "number"
        ? prediction.computed_at + CYCLE_SECONDS * 1000
        : null;
    const fallbackAnchor = (lastUpdatedRef.current?.getTime() ?? Date.now()) + CYCLE_SECONDS * 1000;
    setNextPredictionAt(backendAnchor ?? fallbackAnchor);
  }, [prediction]);

  const direction = prediction?.direction;
  const tone = directionTone(direction);
  const summary = normalizeDecision(prediction);
  const hasConfidence = typeof prediction?.confidence === "number";
  const confidence = hasConfidence ? Math.max(0, Math.min(100, prediction.confidence)) : 0;
  const isNoTrade = direction === "NO_TRADE";
  const riskReason = summary.primaryBlockReason ?? prediction?.risk?.reason;

  const remaining = nextPredictionAt
    ? Math.max(0, Math.ceil((nextPredictionAt - now) / 1000))
    : CYCLE_SECONDS;
  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");

  const Icon = direction === "LONG" ? ArrowUpRight : direction === "SHORT" ? ArrowDownRight : Minus;

  return (
    <div className="prediction-card">
      <div className="prediction-header">
        <span className="card-title" style={{ marginBottom: 0 }}>
          AI Prediction
        </span>
        <span className="badge">Next Prediction In {mm}:{ss}</span>
      </div>

      <div className="dial" style={{ ["--pct" as any]: confidence, ["--tone" as any]: `var(--c-${tone})` }}>
        <div className="dial-inner">
          <Icon size={26} className={tone} />
          <h3 className={`${tone}${isNoTrade ? " no-trade-label" : ""}`}>{isNoTrade ? "NO TRADE" : direction || "—"}</h3>
          <p>Confidence</p>
          <b className="dial-confidence">
            {isNoTrade || !hasConfidence ? "Not established" : fmtPct(confidence, 0)}
          </b>
        </div>
      </div>

      {/* Target/stop/strength intentionally not repeated here: the
          authoritative decision summary (entry, target, stop, horizon,
          confidence) lives in PredictionChart's pc-summary. This card is
          only the at-a-glance confidence dial. */}
      {isNoTrade && (
        <div className="no-trade-panel">
          <span className="no-trade-message">No active trade setup</span>
          {riskReason && <span className="no-trade-reason">{riskReason}</span>}
          <span className="no-trade-reason">{describeConfidence(summary)}</span>
        </div>
      )}
      <progress value={isNoTrade ? 0 : confidence} max={100} className={isNoTrade ? "disabled" : undefined} />
    </div>
  );
}

export default memo(PredictionGauge);
