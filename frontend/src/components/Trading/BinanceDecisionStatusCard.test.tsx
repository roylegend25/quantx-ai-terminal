import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BinanceDecisionStatusCard, { type BinanceDecisionStatus } from "./BinanceDecisionStatusCard";

function makeData(overrides: Partial<BinanceDecisionStatus> = {}): BinanceDecisionStatus {
  return {
    symbol: "BTCUSDT",
    timeframe: "5m",
    model_direction: "SHORT",
    strategy_direction: "SHORT",
    final_direction: "BLOCKED",
    confidence: 62.4,
    required_confidence: 70.0,
    risk_gate_allowed: false,
    risk_gate_reason: "Active mode is PAPER",
    blocked_reasons: ["Active mode is PAPER"],
    active_model: "lightgbm-v34",
    active_strategy: "strategy_ensemble",
    intended_notional: 20.0,
    intended_leverage: 1,
    take_profit: 63100.0,
    stop_loss: 64600.0,
    last_decision_at: "2026-07-12T03:38:00Z",
    last_execution_attempt_at: null,
    last_order_status: null,
    active_mode: "PAPER",
    ...overrides,
  };
}

describe("BinanceDecisionStatusCard", () => {
  it("renders the blocked live decision with its primary reason", () => {
    render(<BinanceDecisionStatusCard data={makeData()} />);
    expect(screen.getByText("Binance Bot Decision Status")).toBeInTheDocument();
    expect(screen.getAllByText("BLOCKED").length).toBeGreaterThan(0);
    expect(screen.getByText(/Active mode is PAPER/)).toBeInTheDocument();
    expect(screen.getByText("62.4%")).toBeInTheDocument();
    expect(screen.getByText("70.0%")).toBeInTheDocument();
  });

  it("renders an allowed live decision", () => {
    render(
      <BinanceDecisionStatusCard
        data={makeData({ final_direction: "SHORT", risk_gate_allowed: true, blocked_reasons: [], active_mode: "BINANCE_LIVE" })}
      />
    );
    expect(screen.getAllByText("SHORT").length).toBeGreaterThan(0);
    expect(screen.getByText("ALLOWED")).toBeInTheDocument();
    expect(screen.getByText("BINANCE_LIVE")).toBeInTheDocument();
  });

  it("renders the NO_DATA empty state without crashing", () => {
    render(<BinanceDecisionStatusCard data={{ status: "NO_DATA", message: "No Binance live decision has been evaluated yet." }} />);
    expect(screen.getByText("No Binance live decision has been evaluated yet.")).toBeInTheDocument();
  });

  it("renders an error state with a refresh action", async () => {
    const onRefresh = vi.fn();
    render(<BinanceDecisionStatusCard data={null} errored onRefresh={onRefresh} />);
    expect(screen.getByText("Binance Bot Decision Status unavailable")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh Binance Status" }));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("does not display any paper-ledger metrics (only the live gate's mode reason)", () => {
    const { container } = render(<BinanceDecisionStatusCard data={makeData()} />);
    expect(container.textContent).not.toMatch(/paper (balance|pnl|equity|drawdown)/i);
  });
});
