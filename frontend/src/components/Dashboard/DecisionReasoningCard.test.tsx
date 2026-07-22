import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import DecisionReasoningCard from "./DecisionReasoningCard";

function makeDecision(overrides: Partial<any> = {}) {
  return {
    trade_allowed: true,
    final_direction: "SHORT",
    final_confidence: 82.5,
    required_confidence: 70,
    risk_reason: "Risk checks passed",
    mode: "champion_ml",
    active_model: { model_type: "xgboost", version: "v34" },
    strategy_used: "champion",
    market_regime: "trending",
    top_reasons: ["Champion xgboost v34 predicts SHORT"],
    trade_blockers: [],
    ...overrides,
  };
}

describe("DecisionReasoningCard", () => {
  it("shows 'approved for paper execution' when no execution outcome is supplied (Paper tab)", () => {
    render(<DecisionReasoningCard decision={makeDecision()} />);
    expect(screen.getByText(/approved for paper execution/i)).toBeInTheDocument();
  });

  it("shows 'approved for paper execution' when the live execution hasn't been attempted yet", () => {
    render(
      <DecisionReasoningCard decision={makeDecision()} executionOutcome={{ attempted: false, ok: null, reason: null }} />
    );
    expect(screen.getByText(/approved for paper execution/i)).toBeInTheDocument();
  });

  it("shows 'approved for paper execution' when the live execution actually succeeded", () => {
    render(
      <DecisionReasoningCard decision={makeDecision()} executionOutcome={{ attempted: true, ok: true, reason: null }} />
    );
    expect(screen.getByText(/approved for paper execution/i)).toBeInTheDocument();
  });

  it("never shows 'approved for paper execution' when the signal was approved but live execution failed", () => {
    render(
      <DecisionReasoningCard
        decision={makeDecision()}
        executionOutcome={{ attempted: true, ok: false, reason: "Insufficient margin" }}
      />
    );
    expect(screen.queryByText(/approved for paper execution/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Signal Approved/)).toBeInTheDocument();
    expect(screen.getByText(/Execution Failed/)).toBeInTheDocument();
    expect(screen.getByText(/Reason: Insufficient margin/)).toBeInTheDocument();
  });

  it("does not correct a blocked signal (execution outcome is irrelevant when the signal itself was blocked)", () => {
    render(
      <DecisionReasoningCard
        decision={makeDecision({ trade_allowed: false })}
        executionOutcome={{ attempted: true, ok: false, reason: "Insufficient margin" }}
      />
    );
    expect(screen.getByText(/blocked:/i)).toBeInTheDocument();
    expect(screen.queryByText("Execution Failed")).not.toBeInTheDocument();
  });

  it("shows the point-margin gate and configuration scope/version", () => {
    render(
      <DecisionReasoningCard
        decision={makeDecision({ point_margin: 7, required_point_margin: 4, point_margin_pass: true, configuration_scope: "paper", configuration_version: 3 })}
      />
    );
    expect(screen.getByText(/7\.00 \/ required 4\.00/)).toBeInTheDocument();
    expect(screen.getByText(/paper · v3/)).toBeInTheDocument();
  });

  it("shows contributing, shadow-excluded, and manually-disabled indicators under 'Why Bot Decided This'", () => {
    render(
      <DecisionReasoningCard
        decision={makeDecision({
          active_indicators: ["ema_20_50_continuation"],
          shadow_indicators: ["rsi_momentum"],
          disabled_indicators: ["macd_momentum"],
          exclusion_reasons: { rsi_momentum: "SHADOW_ONLY_POOR_PERFORMANCE", macd_momentum: "MANUALLY_DISABLED" },
        })}
      />
    );
    expect(screen.getByText("Why Bot Decided This")).toBeInTheDocument();
    expect(screen.getByText("ema 20 50 continuation")).toBeInTheDocument();
    expect(screen.getByText("rsi momentum")).toBeInTheDocument();
    expect(screen.getByText("macd momentum")).toBeInTheDocument();
  });

  it("shows a star badge next to a shadow indicator recommended for reactivation", () => {
    render(
      <DecisionReasoningCard
        decision={makeDecision({
          shadow_indicators: ["rsi_momentum"],
          exclusion_reasons: { rsi_momentum: "RECOMMENDED_FOR_REACTIVATION" },
        })}
      />
    );
    expect(screen.getByText(/rsi momentum ⭐/)).toBeInTheDocument();
  });
});
