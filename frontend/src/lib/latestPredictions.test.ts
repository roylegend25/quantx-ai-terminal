import { describe, expect, it } from "vitest";
import { selectLatestEligiblePredictions } from "./latestPredictions";

function point(timestamp: number, predicted_price: number | null) {
  return { timestamp, predicted_price };
}

describe("selectLatestEligiblePredictions", () => {
  it("returns at most the latest 10 points", () => {
    const points = Array.from({ length: 25 }, (_, i) => point(i, 100 + i));
    const result = selectLatestEligiblePredictions(points, 10);
    expect(result).toHaveLength(10);
  });

  it("returns the CHRONOLOGICALLY LATEST points, not the first 10 in array order", () => {
    const points = Array.from({ length: 25 }, (_, i) => point(i, 100 + i));
    const result = selectLatestEligiblePredictions(points, 10);
    expect(result.map((p) => p.timestamp)).toEqual([15, 16, 17, 18, 19, 20, 21, 22, 23, 24]);
  });

  it("returns results in chronological (ascending) order", () => {
    const shuffled = [point(5, 105), point(1, 101), point(3, 103), point(2, 102), point(4, 104)];
    const result = selectLatestEligiblePredictions(shuffled, 10);
    expect(result.map((p) => p.timestamp)).toEqual([1, 2, 3, 4, 5]);
  });

  it("excludes points missing a predicted_price - never silently connects across a gap", () => {
    const points = [point(1, 100), point(2, null), point(3, 102), point(4, null), point(5, 104)];
    const result = selectLatestEligiblePredictions(points, 10);
    expect(result.map((p) => p.timestamp)).toEqual([1, 3, 5]);
  });

  it("excludes points with a non-finite predicted_price", () => {
    const points = [point(1, 100), point(2, NaN), point(3, 102)];
    const result = selectLatestEligiblePredictions(points, 10);
    expect(result.map((p) => p.timestamp)).toEqual([1, 3]);
  });

  it("returns an empty array when nothing is eligible", () => {
    const points = [point(1, null), point(2, null)];
    expect(selectLatestEligiblePredictions(points, 10)).toEqual([]);
  });

  it("does not mutate the input array", () => {
    const points = [point(3, 103), point(1, 101), point(2, 102)];
    const original = [...points];
    selectLatestEligiblePredictions(points, 10);
    expect(points).toEqual(original);
  });

  it("respects a custom count", () => {
    const points = Array.from({ length: 8 }, (_, i) => point(i, 100 + i));
    expect(selectLatestEligiblePredictions(points, 3)).toHaveLength(3);
  });
});
