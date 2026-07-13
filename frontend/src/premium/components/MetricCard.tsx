import type { ReactNode } from "react";
import type { StatusTone } from "./StatusBadge";

type Props = {
  label: string;
  value: ReactNode;
  sublabel?: ReactNode;
  tone?: StatusTone;
  icon?: ReactNode;
  className?: string;
};

/** Compact metric tile - the building block for market-summary rows,
 * "below chart" direction/confidence/target/stop rows, performance KPI
 * grids, etc. Deliberately dumb: it only renders props, never fetches. */
export default function MetricCard({ label, value, sublabel, tone = "neutral", icon, className }: Props) {
  return (
    <div className={`qp-metric-card${className ? ` ${className}` : ""}`}>
      <div className="qp-metric-head">
        <span className="qp-metric-label">{label}</span>
        {icon && <span className="qp-metric-icon">{icon}</span>}
      </div>
      <div className={`qp-metric-value qp-tone-text-${tone}`}>{value}</div>
      {sublabel && <div className="qp-metric-sub">{sublabel}</div>}
    </div>
  );
}
