/** Phase 34: selects the latest N ELIGIBLE AI predictions, chronologically,
 *  for the main chart's "latest 10 AI predictions" overlay. Eligible means
 *  a real predicted_price and timestamp - a point missing either is never
 *  silently connected into the line. Pure/stateless so it's testable
 *  without mounting the (canvas-heavy) chart component. */

export type PredictionHistoryPoint = {
  timestamp: number;
  predicted_price: number | null;
  [key: string]: unknown;
};

export function selectLatestEligiblePredictions<T extends PredictionHistoryPoint>(
  points: T[],
  count = 10
): T[] {
  const eligible = points.filter(
    (pt) => typeof pt.predicted_price === "number" && Number.isFinite(pt.predicted_price) && typeof pt.timestamp === "number"
  );
  const sorted = [...eligible].sort((a, b) => a.timestamp - b.timestamp);
  return sorted.slice(-count);
}
