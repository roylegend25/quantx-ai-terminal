import type { ReactNode } from "react";

type Props = {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Chart canvases (ProChartCanvas) are dropped in unmodified here - this
   * component only supplies premium chrome around them (see plan Tier B). */
  bodyClassName?: string;
};

export default function ChartCard({ title, right, children, className, bodyClassName }: Props) {
  return (
    <div className={`qp-chart-card${className ? ` ${className}` : ""}`}>
      {(title || right) && (
        <div className="qp-chart-card-head">
          {title && <div className="qp-chart-card-title">{title}</div>}
          {right && <div className="qp-chart-card-right">{right}</div>}
        </div>
      )}
      <div className={`qp-chart-card-body${bodyClassName ? ` ${bodyClassName}` : ""}`}>{children}</div>
    </div>
  );
}
