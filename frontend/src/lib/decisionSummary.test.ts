import { describe, expect, it } from "vitest";
import {
  describeConfidence,
  describeEvidence,
  formatHorizonMs,
  formatTimeframeLong,
  normalizeDecision,
} from "./decisionSummary";

/** Modeled on a real production /api/prediction response (2026-07-17):
 *  evidence gate passed 20/20, blockers are point margin + calibrated
 *  confidence, edge short-circuited as no_trade_direction. */
const noTradePrediction = {
  direction: "NO_TRADE",
  target: null,
  stop: null,
  computed_at: 1784293785974,
  timeframe: "30m",
  decision_engine: {
    engine: "active_drive_v2",
    decision_id: "abc123",
    signal: "NO_TRADE",
    timeframe: "30m",
    directional_confidence: null,
    point_margin: 2.784,
    required_point_margin: 4.0,
    edge_block_reason: "no_trade_direction",
    expires_at: "2026-07-17T13:10:45+00:00",
    top_reasons: [
      "Point margin below required threshold",
      "Calibrated directional confidence below threshold",
    ],
    decision_metrics: {
      history: { name: "relevant_history", value: 20, required: 20, passed: true, scope: "BTCUSDT 30m" },
      confidence: { name: "directional_confidence", value: 0.0781, required: 0.6, passed: false },
    },
    confidence_diagnostics: {
      passed: false,
      calibrated_directional_confidence: 0.0781,
      calibration_sample_count: 20,
      minimum_calibration_samples: 20,
      required_confidence: 0.6,
      raw_probability_up: 0.8058,
      raw_probability_down: 0.1942,
      failure_reason: null,
    },
    forecast: { direction: "BULLISH", horizon_seconds: 14400, trade_actionable: false },
  },
};

const warmingPrediction = {
  ...noTradePrediction,
  decision_engine: {
    ...noTradePrediction.decision_engine,
    top_reasons: ["Relevant resolved history below required minimum"],
    decision_metrics: {
      history: {
        name: "relevant_history",
        value: 7,
        required: 20,
        passed: false,
        scope: "BTCUSDT 30m",
        samples_remaining: 13,
        limiting_source: "momentum",
        estimated_ready_at: null,
      },
    },
    confidence_diagnostics: {
      passed: false,
      calibrated_directional_confidence: null,
      calibration_sample_count: 7,
      minimum_calibration_samples: 20,
      required_confidence: 0.6,
      failure_reason: "Only 7 resolved; 20 required",
    },
  },
};

const longPrediction = {
  direction: "LONG",
  target: 64000,
  stop: 62000,
  computed_at: 1784293785974,
  timeframe: "15m",
  decision_engine: {
    engine: "active_drive_v2",
    decision_id: "def456",
    signal: "LONG",
    timeframe: "15m",
    directional_confidence: 0.72,
    point_margin: 6.1,
    required_point_margin: 4.0,
    top_reasons: [],
    entry_price: 63500,
    risk_reward_ratio: 2.0,
    net_expected_edge: 0.0042,
    eligible_for_execution: true,
    execution: { eligible: true, risk_gate_passed: true, reason: null },
    decision_metrics: {
      history: { name: "relevant_history", value: 31, required: 20, passed: true, scope: "BTCUSDT 15m" },
    },
    confidence_diagnostics: {
      passed: true,
      calibrated_directional_confidence: 0.72,
      calibration_sample_count: 31,
      minimum_calibration_samples: 20,
      required_confidence: 0.6,
    },
    forecast: { direction: "BULLISH", horizon_seconds: 7200, trade_actionable: true },
  },
};

describe("timeframe and horizon formatting", () => {
  it("uses lowercase m for minutes and never renders 30m as 30M", () => {
    expect(formatTimeframeLong("30m")).toBe("30 minutes");
    expect(formatTimeframeLong("30m")).not.toContain("30M");
    expect(formatTimeframeLong("1m")).toBe("1 minute");
    expect(formatTimeframeLong("15m")).toBe("15 minutes");
    expect(formatTimeframeLong("1h")).toBe("1 hour");
  });

  it("keeps 1M as a calendar month, distinct from 1m", () => {
    expect(formatTimeframeLong("1M")).toBe("1 calendar month");
    expect(formatTimeframeLong("1M")).not.toBe(formatTimeframeLong("1m"));
  });

  it("formats horizons independently of the source timeframe", () => {
    expect(formatHorizonMs(14400 * 1000)).toBe("4 Hours");
    expect(formatHorizonMs(30 * 60_000)).toBe("30 Minutes");
    expect(formatHorizonMs(null)).toBe("—");
  });
});

describe("normalizeDecision for a NO_TRADE abstention", () => {
  const d = normalizeDecision(noTradePrediction);

  it("keeps the abstention and separates the informational forecast", () => {
    expect(d.signal).toBe("NO_TRADE");
    expect(d.actionable).toBe(false);
    expect(d.forecastDirection).toBe("BULLISH");
  });

  it("reports sufficient evidence when the history gate passed", () => {
    expect(d.evidence.status).toBe("sufficient");
    expect(d.evidence.samples).toBe(20);
    expect(d.evidence.required).toBe(20);
    expect(d.evidence.remaining).toBe(0);
    expect(describeEvidence(d)).toBe("20 of 20 qualifying resolved predictions");
  });

  it("shows the legitimate calibrated value that failed the gate, with the threshold", () => {
    expect(d.confidence.status).toBe("below_threshold");
    expect(d.confidence.calibratedPct).toBeCloseTo(7.81);
    expect(describeConfidence(d)).toBe("Calibrated 8% is below the 60% required");
  });

  it("ranks primary and secondary blocking reasons", () => {
    expect(d.primaryBlockReason).toBe("Point margin below required threshold");
    expect(d.secondaryBlockReasons).toEqual(["Calibrated directional confidence below threshold"]);
  });

  it("marks edge as not evaluated with no_trade_direction, never invalid_entry_target_stop", () => {
    expect(d.edgeStatus).toBe("not_evaluated");
    expect(d.edgeBlockReason).toBe("no_trade_direction");
    expect(d.edgeBlockReason).not.toBe("invalid_entry_target_stop");
  });

  it("distinguishes source timeframe from prediction horizon", () => {
    expect(d.sourceTimeframe).toBe("30m");
    expect(d.horizonMs).toBe(14400 * 1000);
  });
});

describe("normalizeDecision while evidence is warming up", () => {
  const d = normalizeDecision(warmingPrediction);

  it("reports warming-up evidence with progress, not a blanket 'insufficient'", () => {
    expect(d.evidence.status).toBe("warming_up");
    expect(d.evidence.remaining).toBe(13);
    expect(d.evidence.limitingSource).toBe("momentum");
    expect(describeEvidence(d)).toBe(
      "Warming up: 7 of 20 qualifying resolved predictions (limiting source: momentum)"
    );
  });

  it("never fabricates a confidence number before calibration is valid", () => {
    expect(d.confidence.status).toBe("not_established");
    expect(d.confidence.calibratedPct).toBeNull();
    expect(describeConfidence(d)).toBe("7 of 20 calibration samples available");
  });
});

describe("normalizeDecision for an actionable signal", () => {
  const d = normalizeDecision(longPrediction);

  it("establishes calibrated confidence from valid calibration", () => {
    expect(d.signal).toBe("LONG");
    expect(d.actionable).toBe(true);
    expect(d.confidence.status).toBe("established");
    expect(d.confidence.calibratedPct).toBeCloseTo(72);
  });

  it("carries validated trade levels", () => {
    expect(d.target).toBe(64000);
    expect(d.stop).toBe(62000);
  });

  it("exposes entry, reward/risk, expected edge, authority, and risk status", () => {
    expect(d.entryPrice).toBe(63500);
    expect(d.rewardRisk).toBe(2.0);
    expect(d.expectedEdge).toBeCloseTo(0.0042);
    expect(d.authorityStatus).toBe("eligible");
    expect(d.riskStatus).toBe("passed");
  });
});

describe("normalizeDecision without a payload", () => {
  it("fails closed to NO_TRADE with explicit unavailable states", () => {
    const d = normalizeDecision(null);
    expect(d.signal).toBe("NO_TRADE");
    expect(d.actionable).toBe(false);
    expect(d.evidence.status).toBe("unavailable");
    expect(d.confidence.status).toBe("unavailable");
    expect(d.target).toBeNull();
    expect(d.stop).toBeNull();
    expect(d.authorityStatus).toBe("blocked");
    expect(d.riskStatus).toBe("not_passed");
    expect(d.entryPrice).toBeNull();
    expect(d.expectedEdge).toBeNull();
  });
});
