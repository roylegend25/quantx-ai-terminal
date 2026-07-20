import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DecisionSummaryTiles from "./DecisionSummaryTiles";

const noTradePrediction = {
  direction: "NO_TRADE",
  target: null,
  stop: null,
  computed_at: 1784293785974,
  timeframe: "30m",
  decision_engine: {
    engine: "active_drive_v2",
    signal: "NO_TRADE",
    timeframe: "30m",
    directional_confidence: null,
    point_margin: 2.784,
    required_point_margin: 4.0,
    edge_block_reason: "no_trade_direction",
    top_reasons: [
      "Point margin below required threshold",
      "Calibrated directional confidence below threshold",
    ],
    decision_metrics: {
      history: { value: 20, required: 20, passed: true, scope: "BTCUSDT 30m" },
    },
    confidence_diagnostics: {
      passed: false,
      calibrated_directional_confidence: 0.0781,
      calibration_sample_count: 20,
      minimum_calibration_samples: 20,
      required_confidence: 0.6,
    },
    forecast: { direction: "BULLISH", horizon_seconds: 14400, trade_actionable: false },
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
    signal: "LONG",
    timeframe: "15m",
    directional_confidence: 0.72,
    top_reasons: [],
    entry_price: 63500,
    risk_reward_ratio: 2.0,
    net_expected_edge: 0.0042,
    eligible_for_execution: true,
    execution: { eligible: true, risk_gate_passed: true, reason: null },
    decision_metrics: { history: { value: 31, required: 20, passed: true } },
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

const renderTiles = (prediction: any) =>
  render(
    <DecisionSummaryTiles prediction={prediction} interval="30m" intervalMs={1_800_000} forecastBars={40} lastClose={62834.2} />
  );

describe("DecisionSummaryTiles for NO_TRADE", () => {
  it("renders exactly one summary with no target/stop metric cards", () => {
    renderTiles(noTradePrediction);
    expect(screen.getAllByText("Signal")).toHaveLength(1);
    expect(screen.queryByText("Price Target")).toBeNull();
    expect(screen.queryByText("Stop Loss")).toBeNull();
    expect(screen.queryByText("Entry Price")).toBeNull();
    expect(screen.queryByText("Reward/Risk")).toBeNull();
    expect(screen.queryByText("Expected Edge")).toBeNull();
    expect(screen.queryByText("Authority Status")).toBeNull();
    expect(screen.queryByText("Risk Status")).toBeNull();
    expect(screen.getByText("Trade levels are not created for an abstention.", { exact: false })).toBeTruthy();
  });

  it("does not fabricate confidence and explains why", () => {
    renderTiles(noTradePrediction);
    expect(screen.getByText("Not established")).toBeTruthy();
    expect(screen.getByText("Calibrated 8% is below the 60% required")).toBeTruthy();
  });

  it("shows evidence progress instead of a bare 'Insufficient'", () => {
    renderTiles(noTradePrediction);
    expect(screen.getByText("20 / 20")).toBeTruthy();
    expect(screen.getByText("20 of 20 qualifying resolved predictions")).toBeTruthy();
    expect(screen.queryByText("Insufficient")).toBeNull();
  });

  it("ranks the primary blocker first and lists secondary blockers in the note", () => {
    renderTiles(noTradePrediction);
    expect(screen.getByText("Point margin below required threshold")).toBeTruthy();
    expect(screen.getByText(/Also blocked: Calibrated directional confidence below threshold/)).toBeTruthy();
  });

  it("separates source timeframe from prediction horizon and never renders 30M", () => {
    renderTiles(noTradePrediction);
    expect(screen.getByText("Source Timeframe")).toBeTruthy();
    expect(screen.getByText("30 minutes")).toBeTruthy();
    expect(screen.getByText("Prediction Horizon")).toBeTruthy();
    expect(screen.getByText("4 Hours")).toBeTruthy();
    expect(screen.queryByText("30M")).toBeNull();
    expect(screen.queryByText(/^Horizon$/)).toBeNull();
  });

  it("marks the forecast as informational, distinct from the signal", () => {
    renderTiles(noTradePrediction);
    expect(screen.getByText("NO TRADE")).toBeTruthy();
    expect(screen.getByText("Forecast (informational)")).toBeTruthy();
    expect(screen.getByText("BULLISH")).toBeTruthy();
    expect(screen.getByText("Not a trade signal")).toBeTruthy();
  });
});

describe("DecisionSummaryTiles for an actionable signal", () => {
  it("shows validated entry levels and calibrated confidence", () => {
    renderTiles(longPrediction);
    expect(screen.getByText("Price Target")).toBeTruthy();
    expect(screen.getByText("Stop Loss")).toBeTruthy();
    expect(screen.getByText("72%")).toBeTruthy();
    expect(screen.queryByText(/Trade levels are not created/)).toBeNull();
    expect(screen.queryByText("Forecast (informational)")).toBeNull();
  });

  it("shows entry, reward/risk, expected edge, authority, and risk status", () => {
    renderTiles(longPrediction);
    expect(screen.getByText("Entry Price")).toBeTruthy();
    expect(screen.getByText("$63,500.00")).toBeTruthy();
    expect(screen.getByText("Reward/Risk")).toBeTruthy();
    expect(screen.getByText("2.00 : 1")).toBeTruthy();
    expect(screen.getByText("Expected Edge")).toBeTruthy();
    expect(screen.getByText("0.42%")).toBeTruthy();
    expect(screen.getByText("Authority Status")).toBeTruthy();
    expect(screen.getByText("Eligible")).toBeTruthy();
    expect(screen.getByText("Risk Status")).toBeTruthy();
    expect(screen.getByText("Passed")).toBeTruthy();
  });

  it("still shows evidence status for an actionable decision, not just NO_TRADE", () => {
    renderTiles(longPrediction);
    expect(screen.getByText("Evidence")).toBeTruthy();
    expect(screen.getByText("31 / 20")).toBeTruthy();
  });
});

describe("DecisionSummaryTiles without a payload", () => {
  it("fails closed: NO_TRADE, no fake values, explicit unavailable states", () => {
    renderTiles(null);
    expect(screen.getByText("NO TRADE")).toBeTruthy();
    expect(screen.getByText("Not established")).toBeTruthy();
    expect(screen.queryByText("Price Target")).toBeNull();
    expect(screen.queryByText("Stop Loss")).toBeNull();
    expect(screen.queryByText("$0.00")).toBeNull();
  });
});
