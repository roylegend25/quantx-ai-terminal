import { Flame } from "lucide-react";

export default function LiquidationHeatmapCard() {
  return (
    <div className="coming-soon-panel">
      <Flame size={28} />
      <b>Liquidation Heatmap</b>
      <p>
        Per-price-level liquidation density isn't exposed by the backend yet — this view will light up once
        the liquidations feed is aggregated into price buckets.
      </p>
      <span className="badge">Coming Soon</span>
    </div>
  );
}
