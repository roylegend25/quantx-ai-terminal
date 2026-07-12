import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UserLiveConfirmationCard from "./UserLiveConfirmationCard";
import type { TradingStatus } from "./TradingShared";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({
  api: {
    lockBinanceLive: vi.fn().mockResolvedValue({}),
    unlockBinanceLive: vi.fn().mockResolvedValue({}),
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

describe("UserLiveConfirmationCard", () => {
  it("disables the confirmation button with the exact reason when the server live lock is off", () => {
    render(<UserLiveConfirmationCard status={makeStatus({ binance_live_enabled_by_server: false })} onChanged={onChanged} showToast={showToast} />);
    const btn = screen.getByRole("button", { name: "Complete Live Risk Confirmation" });
    expect(btn).toBeDisabled();
    expect(screen.getByText("Enable Server Live Lock first.")).toBeInTheDocument();
  });

  it("disables the button when Binance API is not configured", () => {
    render(
      <UserLiveConfirmationCard
        status={makeStatus({ binance_live_enabled_by_server: true, binance_configured: false })}
        onChanged={onChanged}
        showToast={showToast}
      />
    );
    expect(screen.getByRole("button", { name: "Complete Live Risk Confirmation" })).toBeDisabled();
    expect(screen.getByText("Binance API is not configured.")).toBeInTheDocument();
  });

  it("disables the button when the Binance account signed read fails", () => {
    render(
      <UserLiveConfirmationCard
        status={makeStatus({
          binance_live_enabled_by_server: true,
          binance_configured: true,
          binance_connected: false,
          binance_account_connected: false,
        })}
        onChanged={onChanged}
        showToast={showToast}
      />
    );
    expect(screen.getByRole("button", { name: "Complete Live Risk Confirmation" })).toBeDisabled();
    expect(screen.getByText("Binance account signed read failed.")).toBeInTheDocument();
  });

  it("disables the button when the kill switch is active", () => {
    render(
      <UserLiveConfirmationCard
        status={makeStatus({ binance_live_enabled_by_server: true, binance_connected: true, kill_switch_active: true })}
        onChanged={onChanged}
        showToast={showToast}
      />
    );
    expect(screen.getByRole("button", { name: "Complete Live Risk Confirmation" })).toBeDisabled();
    expect(screen.getByText("Kill switch is active.")).toBeInTheDocument();
  });

  it("enables the button once the server lock is on and the account is connected", () => {
    render(
      <UserLiveConfirmationCard
        status={makeStatus({ binance_live_enabled_by_server: true, binance_configured: true, binance_connected: true, kill_switch_active: false })}
        onChanged={onChanged}
        showToast={showToast}
      />
    );
    expect(screen.getByRole("button", { name: "Complete Live Risk Confirmation" })).toBeEnabled();
  });

  it("opens the live-risk ceremony modal when the enabled button is clicked", async () => {
    render(
      <UserLiveConfirmationCard
        status={makeStatus({ binance_live_enabled_by_server: true, binance_configured: true, binance_connected: true })}
        onChanged={onChanged}
        showToast={showToast}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Complete Live Risk Confirmation" }));
    expect(screen.getByText("Enable Binance Real Money Trading?")).toBeInTheDocument();
  });

  it("shows the active state and a lock button once user live unlock is complete", async () => {
    render(
      <UserLiveConfirmationCard
        status={makeStatus({ binance_live_enabled_by_server: true, binance_live_unlocked_by_user: true })}
        onChanged={onChanged}
        showToast={showToast}
      />
    );
    expect(screen.getByRole("button", { name: "User Live Unlock: Active" })).toBeInTheDocument();
    const lockBtn = screen.getByRole("button", { name: "Lock User Live Access" });
    await userEvent.click(lockBtn);
    expect(api.lockBinanceLive).toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalled();
  });

  it("shows the server lock, active mode and kill switch status alongside the unlock state", () => {
    render(
      <UserLiveConfirmationCard
        status={makeStatus({ binance_live_enabled_by_server: true, active_mode: "BINANCE_LIVE", kill_switch_active: true })}
        onChanged={onChanged}
        showToast={showToast}
      />
    );
    expect(screen.getByText("Server Live Lock: Enabled")).toBeInTheDocument();
    expect(screen.getByText("Active Mode: BINANCE_LIVE")).toBeInTheDocument();
    expect(screen.getByText("Kill Switch: Active")).toBeInTheDocument();
  });

  it("never renders Binance API key/secret values", () => {
    const status = makeStatus({ binance_live_enabled_by_server: true, binance_live_unlocked_by_user: true });
    const { container } = render(<UserLiveConfirmationCard status={status} onChanged={onChanged} showToast={showToast} />);
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });
});
