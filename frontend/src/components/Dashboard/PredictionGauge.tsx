import { useEffect, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { fmtPct, fmtUsd } from "../../lib/format";

type Props = {
  prediction: any;
  lastUpdated: Date | null;
};

const CYCLE_SECONDS = 60;

function directionTone(direction?: string): "green" | "red" | "yellow" {
  if (direction === "LONG") return "green";
  if (direction === "SHORT") return "red";
  return "yellow";
}

export default function PredictionGauge({ prediction, lastUpdated }: Props) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const direction = prediction?.direction;
  const tone = directionTone(direction);
  const confidence = Math.max(0, Math.min(100, prediction?.confidence ?? 0));

  const elapsed = lastUpdated ? Math.floor((now - lastUpdated.getTime()) / 1000) : 0;
  const remaining = CYCLE_SECONDS - (elapsed % CYCLE_SECONDS);
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
          <h3 className={tone}>{direction || "—"}</h3>
          <p>Confidence</p>
          <b className="dial-confidence">{fmtPct(confidence, 0)}</b>
        </div>
      </div>

      <div className="target-stop-row">
        <div>
          <span className="tile-label">Target Price</span>
          <b className="tile-value green">{fmtUsd(prediction?.target)}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Stop Loss</span>
          <b className="tile-value red">{fmtUsd(prediction?.stop)}</b>
        </div>
      </div>

      <div className="strength-row">
        <span className="tile-label">Prediction Strength</span>
        <b>{fmtPct(confidence, 0)}</b>
      </div>
      <progress value={confidence} max={100} />
    </div>
  );
}
