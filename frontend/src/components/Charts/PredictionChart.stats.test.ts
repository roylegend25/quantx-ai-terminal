import { describe, expect, it } from "vitest";
import { computePastPredictionStats, outcomeBucket } from "./PredictionChart";

describe("outcomeBucket", () => {
  it("buckets CORRECT/WIN as correct and INCORRECT/LOSS as wrong", () => {
    expect(outcomeBucket("CORRECT")).toBe("correct");
    expect(outcomeBucket("WIN")).toBe("correct");
    expect(outcomeBucket("INCORRECT")).toBe("wrong");
    expect(outcomeBucket("LOSS")).toBe("wrong");
  });
  it("buckets NO_TRADE separately from PENDING - it was never a prediction to resolve", () => {
    expect(outcomeBucket("NO_TRADE")).toBe("no_trade");
    expect(outcomeBucket("PENDING")).toBe("unresolved");
    expect(outcomeBucket(null)).toBe("unresolved");
    expect(outcomeBucket(undefined)).toBe("unresolved");
  });
});

describe("computePastPredictionStats", () => {
  it("excludes NO_TRADE from total and pending - a mostly-NO_TRADE series should not look stuck", () => {
    // Regression: BTCUSDT 1m had 217 NO_TRADE rows and 13 directional rows,
    // all 13 already resolved - the dashboard used to report this as
    // "230 total, 217 unresolved" when it should read "13 total, 0 pending".
    const points = [
      ...Array(9).fill({ outcome: "CORRECT" }),
      ...Array(4).fill({ outcome: "INCORRECT" }),
      ...Array(217).fill({ outcome: "NO_TRADE" }),
    ];
    const stats = computePastPredictionStats(points);
    expect(stats.total).toBe(13);
    expect(stats.correct).toBe(9);
    expect(stats.wrong).toBe(4);
    expect(stats.unresolved).toBe(0);
    expect(stats.noTrade).toBe(217);
    expect(stats.hitRatePct).toBeCloseTo((9 / 13) * 100, 5);
  });

  it("still reports genuinely pending predictions separately from NO_TRADE", () => {
    const points = [{ outcome: "CORRECT" }, { outcome: "PENDING" }, { outcome: "NO_TRADE" }];
    const stats = computePastPredictionStats(points);
    expect(stats.total).toBe(2); // CORRECT + PENDING, NOT the NO_TRADE row
    expect(stats.unresolved).toBe(1);
    expect(stats.noTrade).toBe(1);
  });

  it("computes average error only over points that have an error_pct", () => {
    const points = [
      { outcome: "CORRECT", error_pct: 1.0 },
      { outcome: "CORRECT", error_pct: 3.0 },
      { outcome: "NO_TRADE" },
    ];
    const stats = computePastPredictionStats(points);
    expect(stats.avgErrorPct).toBeCloseTo(2.0, 5);
  });

  it("returns null hit rate and error when there is nothing resolved yet", () => {
    const stats = computePastPredictionStats([{ outcome: "PENDING" }, { outcome: "NO_TRADE" }]);
    expect(stats.hitRatePct).toBeNull();
    expect(stats.avgErrorPct).toBeNull();
  });
});
