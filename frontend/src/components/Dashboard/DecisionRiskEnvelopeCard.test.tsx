import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DecisionRiskEnvelopeCard from "./DecisionRiskEnvelopeCard";

const actionablePrediction = {
  direction: "LONG",
  target: 65000,
  stop: 58000,
  decision_engine: { entry_price: 60000, final_signal: "LONG" },
};

const marginData = {
  available: true,
  market: { min_notional: 100 },
  recommendation: { current_setting_notional: 500 },
  breakdown_at_current_setting: { initial_margin: 250 },
  account: { available_balance: 1000, configured_leverage: 2 },
};

describe("DecisionRiskEnvelopeCard", () => {
  it("renders separate PAPER and BINANCE REAL envelopes that are never merged into one figure", () => {
    render(
      <DecisionRiskEnvelopeCard
        prediction={actionablePrediction}
        paperAvailableMargin={9000}
        marginData={marginData}
        marginErrored={false}
      />
    );
    expect(screen.getByText("PAPER")).toBeInTheDocument();
    expect(screen.getByText("BINANCE REAL")).toBeInTheDocument();
    // Paper's fixed $1,000 notional must appear as both bounds (min == max)
    expect(screen.getAllByText("$1,000.00").length).toBeGreaterThanOrEqual(2);
    // Binance's server-computed notional bounds
    expect(screen.getByText("$100.00")).toBeInTheDocument();
    expect(screen.getByText("$500.00")).toBeInTheDocument();
  });

  it("shows the current decision's target/stop as the paper price bounds when actionable", () => {
    render(
      <DecisionRiskEnvelopeCard
        prediction={actionablePrediction}
        paperAvailableMargin={9000}
        marginData={marginData}
        marginErrored={false}
      />
    );
    expect(screen.getByText("Decision Price Bounds")).toBeInTheDocument();
    expect(screen.getByText("$58,000.00")).toBeInTheDocument();
    expect(screen.getByText("$65,000.00")).toBeInTheDocument();
  });

  it("shows an honest unavailable state for Binance Real instead of fabricating a risk envelope on fetch failure", () => {
    render(
      <DecisionRiskEnvelopeCard
        prediction={{ direction: "NO_TRADE" }}
        paperAvailableMargin={9000}
        marginData={null}
        marginErrored
      />
    );
    expect(screen.getByText(/Could not reach the Binance Real account/)).toBeInTheDocument();
    expect(screen.queryByText("Decision Price Bounds")).not.toBeInTheDocument();
  });
});
