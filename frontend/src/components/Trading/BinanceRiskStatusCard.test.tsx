import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BinanceRiskStatusCard, { type BinanceRiskStatus } from "./BinanceRiskStatusCard";

function makeData(overrides: Partial<BinanceRiskStatus> = {}): BinanceRiskStatus {
  return {
    active_mode: "BINANCE_LIVE",
    account_connected: true,
    available: true,
    risk_level: "LOW",
    risk_score: 18.4,
    blocked_reasons: [],
    binance_live_enabled_by_server: true,
    binance_live_unlocked_by_user: true,
    kill_switch_active: false,
    wallet_balance: 500,
    available_balance: 400,
    margin_balance: 505,
    free_margin: 400,
    margin_used: 50,
    margin_usage_pct: 9.9,
    daily_loss_used_usdt: 3.5,
    daily_loss_used_pct: 70,
    max_daily_loss_usdt: 5,
    total_notional_exposure: 149.5,
    max_notional_per_trade: 10,
    nearest_liquidation_distance_pct: 47.2,
    current_effective_leverage: 2,
    max_leverage: 3,
    open_positions: 1,
    max_open_positions: 5,
    ...overrides,
  };
}

describe("BinanceRiskStatusCard", () => {
  it("renders live risk metrics from Binance-specific data, not paper values", () => {
    render(<BinanceRiskStatusCard data={makeData()} />);
    expect(screen.getByText("Binance Live Risk Status")).toBeInTheDocument();
    expect(screen.getByText("LOW")).toBeInTheDocument();
    expect(screen.getByText("$500.00")).toBeInTheDocument(); // wallet balance
    expect(screen.getByText("2x")).toBeInTheDocument(); // current leverage
    expect(screen.queryByText(/max drawdown/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/VaR/i)).not.toBeInTheDocument();
  });

  it("shows the primary blocked reason prominently and secondary reasons in a list", () => {
    render(
      <BinanceRiskStatusCard
        data={makeData({
          blocked_reasons: ["Active mode is PAPER", "User Live Unlock is LOCKED", "Kill switch is active"],
        })}
      />
    );
    expect(screen.getByText("Active mode is PAPER")).toBeInTheDocument();
    expect(screen.getByText("User Live Unlock is LOCKED")).toBeInTheDocument();
    expect(screen.getByText("Kill switch is active")).toBeInTheDocument();
  });

  it("shows 'Not available' for missing metrics instead of substituting a paper value", () => {
    render(
      <BinanceRiskStatusCard
        data={makeData({ current_effective_leverage: null, nearest_liquidation_distance_pct: null })}
      />
    );
    const notAvailable = screen.getAllByText("Not available");
    expect(notAvailable.length).toBeGreaterThanOrEqual(2);
  });

  it("shows 'Risk score unavailable' when the score could not be computed", () => {
    render(<BinanceRiskStatusCard data={makeData({ risk_level: "UNKNOWN", risk_score: null })} />);
    expect(screen.getByText("Risk score unavailable")).toBeInTheDocument();
  });

  it("renders an unavailable empty state with a refresh action on account error", async () => {
    const onRefresh = vi.fn();
    render(<BinanceRiskStatusCard data={null} errored onRefresh={onRefresh} />);
    expect(screen.getByText("Binance Live Risk Status unavailable")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh Binance Status" }));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("never renders Binance API key/secret values", () => {
    const { container } = render(<BinanceRiskStatusCard data={makeData()} />);
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });
});
