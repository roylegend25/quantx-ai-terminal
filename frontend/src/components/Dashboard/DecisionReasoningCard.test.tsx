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
});
