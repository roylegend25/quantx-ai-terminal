type Props = {
  /** Number of skeleton lines/blocks to render. */
  lines?: number;
  height?: number;
  className?: string;
};

/** CSS-only shimmer, respects prefers-reduced-motion via the .qp-skeleton
 * rule in components.css (falls back to a static tone, no animation). */
export default function LoadingSkeleton({ lines = 3, height = 14, className }: Props) {
  return (
    <div className={`qp-skeleton-group${className ? ` ${className}` : ""}`} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="qp-skeleton" style={{ height, width: i === lines - 1 ? "60%" : "100%" }} />
      ))}
    </div>
  );
}
