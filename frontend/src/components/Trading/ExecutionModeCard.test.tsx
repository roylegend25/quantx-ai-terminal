import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecutionModeCard from "./ExecutionModeCard";
import type { TradingStatus } from "./TradingShared";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({
  api: {
    setTradingMode: vi.fn().mockResolvedValue({}),
    lockBinanceLive: vi.fn().mockResolvedValue({}),
  },
}));

function makeStatus(overrides: Partial<TradingStatus> = {}): TradingStatus {
  return {
    active_mode: "PAPER",
    paper_available: true,
    binance_live_available: true,
    binance_configured: true,
    binance_connected: true,
    binance_live_enabled_by_server: false,
    binance_live_unlocked_by_user: false,
    can_trade_binance_live: false,
    reason: "",
    kill_switch_active: false,
    allowed_symbols: ["BTCUSDT"],
    max_leverage: 3,
    max_notional_per_trade: 10,
    max_daily_loss_usdt: 5,
    ...overrides,
  };
}

const showToast = vi.fn();
const onChanged = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ExecutionModeCard", () => {
  it("shows the exact blocked reason and disables the switch when the server lock is off", () => {
    render(<ExecutionModeCard status={makeStatus()} onChanged={onChanged} showToast={showToast} />);
    expect(screen.getByRole("button", { name: "Switch to Binance Live" })).toBeDisabled();
    expect(screen.getByText("Server Live Lock is OFF — enable it in Server Trading Control first.")).toBeInTheDocument();
  });

  it("shows the exact blocked reason when server lock is on but user unlock is missing", () => {
    render(
      <ExecutionModeCard
        status={makeStatus({ binance_live_enabled_by_server: true })}
        onChanged={onChanged}
        showToast={showToast}
      />
    );
    expect(screen.getByRole("button", { name: "Switch to Binance Live" })).toBeDisabled();
    expect(screen.getByText("Complete User Live Confirmation first.")).toBeInTheDocument();
  });

  it("shows the exact blocked reason when the kill switch is active", () => {
    render(
      <ExecutionModeCard
        status={makeStatus({ binance_live_enabled_by_server: true, binance_live_unlocked_by_user: true, kill_switch_active: true })}
        onChanged={onChanged}
        showToast={showToast}
      />
    );
    expect(screen.getByRole("button", { name: "Switch to Binance Live" })).toBeDisabled();
    expect(screen.getByText("Kill switch is active.")).toBeInTheDocument();
  });

  describe("with every gate open", () => {
    let confirmSpy: ReturnType<typeof vi.spyOn>;
    beforeEach(() => {
      confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    });
    afterEach(() => {
      confirmSpy.mockRestore();
    });

    it("enables the switch and calls the mode endpoint after confirmation", async () => {
      render(
        <ExecutionModeCard
          status={makeStatus({ binance_live_enabled_by_server: true, binance_live_unlocked_by_user: true })}
          onChanged={onChanged}
          showToast={showToast}
        />
      );
      const btn = screen.getByRole("button", { name: "Switch to Binance Live" });
      expect(btn).toBeEnabled();
      await userEvent.click(btn);
      expect(confirmSpy).toHaveBeenCalled();
      expect(api.setTradingMode).toHaveBeenCalledWith("BINANCE_LIVE");
      expect(onChanged).toHaveBeenCalled();
    });

    it("does not call the mode endpoint if the confirmation is declined", async () => {
      confirmSpy.mockReturnValue(false);
      render(
        <ExecutionModeCard
          status={makeStatus({ binance_live_enabled_by_server: true, binance_live_unlocked_by_user: true })}
          onChanged={onChanged}
          showToast={showToast}
        />
      );
      await userEvent.click(screen.getByRole("button", { name: "Switch to Binance Live" }));
      expect(api.setTradingMode).not.toHaveBeenCalled();
    });
  });

  it("disables 'Switch back to Paper' while already in Paper mode", () => {
    render(<ExecutionModeCard status={makeStatus({ active_mode: "PAPER" })} onChanged={onChanged} showToast={showToast} />);
    expect(screen.getByRole("button", { name: "Switch back to Paper" })).toBeDisabled();
  });

  it("switches back to paper via the lock-live endpoint", async () => {
    render(<ExecutionModeCard status={makeStatus({ active_mode: "BINANCE_LIVE" })} onChanged={onChanged} showToast={showToast} />);
    await userEvent.click(screen.getByRole("button", { name: "Switch back to Paper" }));
    expect(api.lockBinanceLive).toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalled();
  });

  it("never renders Binance API key/secret values", () => {
    const { container } = render(
      <ExecutionModeCard status={makeStatus({ active_mode: "BINANCE_LIVE" })} onChanged={onChanged} showToast={showToast} />
    );
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });
});
