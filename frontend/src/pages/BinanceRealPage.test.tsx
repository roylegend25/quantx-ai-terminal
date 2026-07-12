import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BinanceRealPage from "./BinanceRealPage";
import { api } from "../services/api";

const baseStatus = {
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
};

const readinessLocked = {
  ok: false,
  blocked_reason: "Server live lock enabled",
  steps: [
    { key: "user_live_unlock", label: "User live confirmation completed", passed: false, detail: "Live-risk ceremony not completed" },
  ],
};

const readinessUnlocked = {
  ok: false,
  blocked_reason: "Active mode is Binance Live",
  steps: [
    { key: "user_live_unlock", label: "User live confirmation completed", passed: true, detail: "Live-risk ceremony completed" },
  ],
};

vi.mock("../services/api", () => ({
  api: {
    tradingMode: vi.fn(),
    liveReadiness: vi.fn(),
    tradingSync: vi.fn().mockResolvedValue({}),
    binanceSummary: vi.fn().mockResolvedValue({ available: false, reason: "test" }),
    binanceBalances: vi.fn().mockResolvedValue({ available: false }),
    binancePositions: vi.fn().mockResolvedValue({ available: false, positions: [] }),
    binanceOrders: vi.fn().mockResolvedValue({ available: false, orders: [] }),
    binanceTrades: vi.fn().mockResolvedValue({ available: false, trades: [] }),
    binanceIncome: vi.fn().mockResolvedValue({ available: false, income: [] }),
    binanceCancelAllOrders: vi.fn(),
    binanceCancelOrder: vi.fn(),
    binanceClosePosition: vi.fn(),
    binanceUpdatePositionRisk: vi.fn(),
    unlockBinanceLive: vi.fn().mockResolvedValue({}),
    lockBinanceLive: vi.fn().mockResolvedValue({}),
    setTradingMode: vi.fn().mockResolvedValue({}),
    adminServerConfig: vi.fn().mockRejectedValue(new Error("Admin access required")),
    adminReloadConfig: vi.fn(),
    adminSetBinanceLive: vi.fn(),
    adminUpdateRiskLimits: vi.fn(),
    killSwitch: vi.fn(),
  },
}));

const showToast = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  (api.tradingMode as ReturnType<typeof vi.fn>).mockResolvedValue(baseStatus);
  (api.liveReadiness as ReturnType<typeof vi.fn>).mockResolvedValue(readinessLocked);
});

describe("BinanceRealPage", () => {
  it("never renders a Paper/Binance segmented mode toggle", async () => {
    render(<BinanceRealPage {...({ showToast } as any)} />);
    await screen.findByText("Binance Real Money Terminal");
    expect(screen.queryByRole("button", { name: "Paper Trading" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Active trading mode" })).not.toBeInTheDocument();
  });

  it("renders the User Live Confirmation card and button", async () => {
    render(<BinanceRealPage {...({ showToast } as any)} />);
    await screen.findByText("User Live Confirmation");
    expect(screen.getByRole("button", { name: "Complete Live Risk Confirmation" })).toBeInTheDocument();
  });

  it("refreshes the live readiness checklist after completing user live confirmation", async () => {
    (api.tradingMode as ReturnType<typeof vi.fn>).mockResolvedValue({ ...baseStatus, binance_live_enabled_by_server: true });
    (api.liveReadiness as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(readinessLocked)
      .mockResolvedValue(readinessUnlocked);

    render(<BinanceRealPage {...({ showToast } as any)} />);
    await screen.findByText("Live-risk ceremony not completed");

    await userEvent.click(screen.getByRole("button", { name: "Complete Live Risk Confirmation" }));
    await userEvent.type(screen.getByPlaceholderText("I UNDERSTAND LIVE TRADING RISK"), "I UNDERSTAND LIVE TRADING RISK");
    for (const box of screen.getAllByRole("checkbox")) {
      await userEvent.click(box);
    }
    await userEvent.click(screen.getByRole("button", { name: "Unlock REAL Trading" }));

    expect(api.unlockBinanceLive).toHaveBeenCalled();
    await waitFor(() => expect(api.liveReadiness).toHaveBeenCalledTimes(2));
    await screen.findByText("Live-risk ceremony completed");
  });

  it("shows the exact blocked reason on the execution mode switch", async () => {
    render(<BinanceRealPage {...({ showToast } as any)} />);
    await screen.findByText("Execution Mode");
    expect(screen.getByText("Server Live Lock is OFF — enable it in Server Trading Control first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch to Binance Live" })).toBeDisabled();
  });

  it("never renders raw Binance API key/secret values", async () => {
    const { container } = render(<BinanceRealPage showToast={showToast} />);
    await screen.findByText("Binance Real Money Terminal");
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });
});
