import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BotSettingsPage from "./BotSettingsPage";
import { api } from "../services/api";

const baseStatus = {
  active_mode: "PAPER",
  paper_available: true,
  binance_live_available: true,
  binance_configured: true,
  binance_connected: true,
  binance_api_key_configured: true,
  binance_api_secret_configured: true,
  binance_public_connected: true,
  binance_account_connected: true,
  binance_signed_read_ok: true,
  binance_account_error: null,
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

vi.mock("../services/api", () => ({
  api: {
    tradingMode: vi.fn(),
    liveReadiness: vi.fn().mockResolvedValue({ ok: false, blocked_reason: "x", steps: [] }),
    unlockBinanceLive: vi.fn().mockResolvedValue({}),
    lockBinanceLive: vi.fn().mockResolvedValue({}),
    adminServerConfig: vi.fn().mockRejectedValue(new Error("Admin access required")),
    adminReloadConfig: vi.fn(),
    adminSetBinanceLive: vi.fn(),
    adminUpdateRiskLimits: vi.fn(),
    killSwitch: vi.fn(),
    riskSettingsGet: vi.fn().mockResolvedValue({
      min_confidence_to_trade: 0.6,
      max_open_positions: 3,
      max_daily_loss_pct: 5,
      max_weekly_loss_pct: 10,
      max_drawdown_pct: 15,
      max_risk_per_trade_pct: 1,
      cooldown_minutes: 5,
      max_consecutive_losses: 3,
      max_position_size_usd: 1000,
      allow_long: true,
      allow_short: true,
      paper_trading_enabled: true,
      updated_at: null,
    }),
    riskSettingsUpdate: vi.fn(),
    riskSettingsReset: vi.fn(),
  },
}));

const appDataProps = {
  showToast: vi.fn(),
  load: vi.fn(),
  botAction: vi.fn(),
  botStatus: { status: "running", mode: "paper", live_trading_enabled: false, last_action: "—" },
  dashboard: {},
};

async function openBinanceTab() {
  await userEvent.click(screen.getByRole("tab", { name: "Binance Live Trading" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.tradingMode as ReturnType<typeof vi.fn>).mockResolvedValue(baseStatus);
});

describe("BotSettingsPage - Binance Live Trading tab", () => {
  it("shows the User Live Confirmation card with a button when locked", async () => {
    render(<BotSettingsPage {...(appDataProps as any)} />);
    await openBinanceTab();
    await screen.findByText("User Live Confirmation");
    expect(screen.getByRole("button", { name: "Complete Live Risk Confirmation" })).toBeInTheDocument();
  });

  it("disables the button with the exact reason when server live lock is off", async () => {
    render(<BotSettingsPage {...(appDataProps as any)} />);
    await openBinanceTab();
    const btn = await screen.findByRole("button", { name: "Complete Live Risk Confirmation" });
    expect(btn).toBeDisabled();
    expect(screen.getByText("Enable Server Live Lock first.")).toBeInTheDocument();
  });

  it("enables the button when server live lock is on and signed read is connected", async () => {
    (api.tradingMode as ReturnType<typeof vi.fn>).mockResolvedValue({ ...baseStatus, binance_live_enabled_by_server: true });
    render(<BotSettingsPage {...(appDataProps as any)} />);
    await openBinanceTab();
    const btn = await screen.findByRole("button", { name: "Complete Live Risk Confirmation" });
    expect(btn).toBeEnabled();
  });

  it("shows the lock button once user live unlock is active", async () => {
    (api.tradingMode as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...baseStatus,
      binance_live_enabled_by_server: true,
      binance_live_unlocked_by_user: true,
    });
    render(<BotSettingsPage {...(appDataProps as any)} />);
    await openBinanceTab();
    await screen.findByRole("button", { name: "User Live Unlock: Active" });
    expect(screen.getByRole("button", { name: "Lock User Live Access" })).toBeInTheDocument();
  });

  it("shows signed account read separately from public/account connected in the API Key Status card", async () => {
    (api.tradingMode as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...baseStatus,
      binance_account_connected: false,
      binance_signed_read_ok: false,
      binance_public_connected: true,
      binance_account_error: "Futures permission or IP whitelist issue",
    });
    render(<BotSettingsPage {...(appDataProps as any)} />);
    await openBinanceTab();
    await screen.findByText("API Key Status");
    expect(screen.getByText("Public Market Data")).toBeInTheDocument();
    expect(screen.getByText("Signed Account Read")).toBeInTheDocument();
    expect(screen.getByText("Account Connected")).toBeInTheDocument();
    expect(screen.getByText("Futures permission or IP whitelist issue")).toBeInTheDocument();
  });

  it("reflects the same account-connected status the Binance Real page would show", async () => {
    // Both pages read the identical field off the same shared
    // TradingStatus (GET /api/trading/mode) - this asserts Bot Settings
    // doesn't derive "connected" from a different/stale field.
    (api.tradingMode as ReturnType<typeof vi.fn>).mockResolvedValue({ ...baseStatus, binance_account_connected: true });
    render(<BotSettingsPage {...(appDataProps as any)} />);
    await openBinanceTab();
    await screen.findByText("API Key Status");
    const accountConnectedRow = screen.getByText("Account Connected").parentElement;
    expect(accountConnectedRow?.textContent).toContain("Yes");
  });

  it("never renders Binance API key/secret values anywhere on the tab", async () => {
    const { container } = render(<BotSettingsPage {...(appDataProps as any)} />);
    await openBinanceTab();
    await screen.findByText("API Key Status");
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });
});
