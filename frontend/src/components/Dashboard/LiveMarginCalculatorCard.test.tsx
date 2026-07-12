import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LiveMarginCalculatorCard from "./LiveMarginCalculatorCard";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({
  api: {
    adminUpdateRiskLimits: vi.fn().mockResolvedValue({}),
    adminServerConfig: vi.fn().mockResolvedValue({ active_mode: "BINANCE_LIVE" }),
  },
}));

const showToast = vi.fn();
const onRefresh = vi.fn().mockResolvedValue(undefined);

function makeData(overrides: Partial<any> = {}) {
  return {
    available: true,
    symbol: "BTCUSDT",
    account: {
      wallet_balance: 10, available_balance: 10, margin_used: 0, free_margin: 10,
      unrealized_pnl: 0, maintenance_margin_used: 0, margin_mode: "N/A",
      current_leverage: 1, configured_leverage: 1, maximum_buying_power: 30,
      liquidation_buffer_pct: null,
    },
    market: { current_price: 63900, min_qty: 0.001, step_size: 0.001, quantity_precision: 3, min_notional: 100 },
    position_sizing: {
      maximum_tradable_quantity: 0, maximum_tradable_notional: 0, required_margin_at_max: 0,
      estimated_fees_at_max: 0, safety_buffer: 1, maximum_safe_position_notional: 8.5,
      maximum_safe_position_quantity: 0, maximum_aggressive_position_notional: 0,
    },
    recommendation: { recommended_max_notional: 8.5, current_setting_notional: 80, current_setting_leverage: 1, status: "TOO_HIGH", adjust_by: -71.5 },
    breakdown_at_current_setting: {
      notional: 80, leverage: 1, initial_margin: 80, estimated_fees: 0.06, maintenance_margin: 0.32,
      safety_buffer: 1, total_required: 81.38, available_margin: 10, status: "FAIL", missing: 71.38,
    },
    risk_capacity: { score: 30, level: "ORANGE", components: {} },
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("LiveMarginCalculatorCard", () => {
  it("shows a neutral message when Binance Live Trading is not active", () => {
    render(<LiveMarginCalculatorCard symbol="BTCUSDT" data={null} liveActive={false} onRefresh={onRefresh} showToast={showToast} />);
    expect(screen.getByText(/Switch to Binance Live Trading/)).toBeInTheDocument();
  });

  it("never hides the calculation breakdown - shows every line item and the exact missing amount", () => {
    render(<LiveMarginCalculatorCard symbol="BTCUSDT" data={makeData()} liveActive onRefresh={onRefresh} showToast={showToast} />);
    expect(screen.getByText("✗ FAIL")).toBeInTheDocument();
    expect(screen.getAllByText("$71.38").length).toBeGreaterThan(0); // missing amount
    expect(screen.getByText("Exchange Initial Margin")).toBeInTheDocument();
    expect(screen.getAllByText("Maintenance Margin").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Safety Buffer").length).toBeGreaterThan(0);
  });

  it("shows PASS when the current setting's margin is covered", () => {
    const data = makeData({
      breakdown_at_current_setting: {
        notional: 8, leverage: 1, initial_margin: 8, estimated_fees: 0.006, maintenance_margin: 0.032,
        safety_buffer: 1, total_required: 9.04, available_margin: 10, status: "PASS", missing: 0,
      },
      recommendation: { recommended_max_notional: 8.5, current_setting_notional: 8, current_setting_leverage: 1, status: "OK", adjust_by: 0.5 },
    });
    render(<LiveMarginCalculatorCard symbol="BTCUSDT" data={data} liveActive onRefresh={onRefresh} showToast={showToast} />);
    expect(screen.getByText("✓ PASS")).toBeInTheDocument();
  });

  it("Auto Configure Risk button asks for confirmation and applies the recommended notional", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<LiveMarginCalculatorCard symbol="BTCUSDT" data={makeData()} liveActive onRefresh={onRefresh} showToast={showToast} />);
    await userEvent.click(screen.getByRole("button", { name: /Auto Configure Risk/ }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(api.adminUpdateRiskLimits).toHaveBeenCalledWith({ BINANCE_MAX_NOTIONAL_PER_TRADE: 8.5 });
    confirmSpy.mockRestore();
  });

  it("does not call the API when confirmation is declined", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<LiveMarginCalculatorCard symbol="BTCUSDT" data={makeData()} liveActive onRefresh={onRefresh} showToast={showToast} />);
    await userEvent.click(screen.getByRole("button", { name: /Auto Configure Risk/ }));
    expect(api.adminUpdateRiskLimits).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("never renders Binance API key/secret values", () => {
    const { container } = render(<LiveMarginCalculatorCard symbol="BTCUSDT" data={makeData()} liveActive onRefresh={onRefresh} showToast={showToast} />);
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });
});
