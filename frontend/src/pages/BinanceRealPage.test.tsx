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
  binance_live_credentials_configured: true,
  binance_live_unlocked_by_user: false,
  can_trade_binance_live: false,
  live_authorization_lease: null,
  final_order_routing_eligible: false,
  automatic_execution_mode: "PAPER" as const,
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
    // The Phase 31 ceremony types into five separate fields plus checkboxes
    // and a select - realistic per-character userEvent.type() timing
    // across all of them comfortably exceeds vitest's default 5s.
    (api.tradingMode as ReturnType<typeof vi.fn>).mockResolvedValue({ ...baseStatus, binance_live_enabled_by_server: true });
    (api.liveReadiness as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(readinessLocked)
      .mockResolvedValue(readinessUnlocked);

    render(<BinanceRealPage {...({ showToast } as any)} />);
    await screen.findByText("Live-risk ceremony not completed");

    await userEvent.click(screen.getByRole("button", { name: "Complete Live Risk Confirmation" }));
    await userEvent.type(screen.getByPlaceholderText("I UNDERSTAND LIVE TRADING RISK"), "I UNDERSTAND LIVE TRADING RISK");
    await userEvent.type(screen.getByPlaceholderText("CONFIRM LIVE EXECUTION NOW"), "CONFIRM LIVE EXECUTION NOW");
    const [passwordInput, accountInput] = [
      document.querySelector('input[type="password"]') as HTMLInputElement,
      screen.getByRole("textbox", { name: "Type your account username to confirm which account this is for" }),
    ];
    await userEvent.type(passwordInput, "hunter2");
    await userEvent.type(accountInput, "admin");
    await userEvent.selectOptions(screen.getByRole("combobox"), "BTCUSDT");
    for (const box of screen.getAllByRole("checkbox")) {
      await userEvent.click(box);
    }
    await userEvent.click(screen.getByRole("button", { name: "Authorize One Live Order" }));

    expect(api.unlockBinanceLive).toHaveBeenCalled();
    await waitFor(() => expect(api.liveReadiness).toHaveBeenCalledTimes(2));
    await screen.findByText("Live-risk ceremony completed");
  }, 15000);

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

// ------------------------------------------------- row-level Cancel (Debug 1)

// order_id/algo_id are strings with 19 digits, matching the real shape the
// backend now sends (see BinanceOrder.to_dict()) - a live-verified bug
// showed this account's real Binance order ids exceed
// Number.MAX_SAFE_INTEGER, so these fixtures deliberately use ids a plain
// JS Number could not round-trip exactly, to guard against ever
// reintroducing a Number(...)/parseInt coercion on this path.
const classicOrder = {
  order_id: "8389766233056201670",
  client_order_id: "qx-1",
  algo_id: null,
  client_algo_id: null,
  provider: "classic",
  source: "binance_real",
  symbol: "ETHUSDT",
  side: "BUY",
  type: "LIMIT",
  status: "NEW",
  price: 1750.0,
  stop_price: 0,
  quantity: 0.049,
  reduce_only: false,
  close_position: false,
};

const algoOrder = {
  order_id: null,
  client_order_id: null,
  algo_id: "9007199254740993123",
  client_algo_id: "qxa-77",
  provider: "algo",
  source: "binance_real",
  symbol: "BTCUSDT",
  side: "SELL",
  type: "STOP_MARKET",
  status: "NEW",
  price: 0,
  stop_price: 61800,
  quantity: 0.01,
  reduce_only: true,
  close_position: false,
};

async function renderWithOrders(orders: any[]) {
  (api.binanceOrders as ReturnType<typeof vi.fn>).mockResolvedValue({ available: true, orders });
  render(<BinanceRealPage {...({ showToast } as any)} />);
  await screen.findByText(`Real Open Orders (${orders.length})`);
}

describe("BinanceRealPage row-level Cancel", () => {
  it("sends symbol + order_id + provider classic for a classic order", async () => {
    (api.binanceCancelOrder as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    await renderWithOrders([classicOrder]);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(api.binanceCancelOrder).toHaveBeenCalledWith({
      symbol: "ETHUSDT",
      orderId: "8389766233056201670",
      clientOrderId: "qx-1",
      algoId: null,
      clientAlgoId: null,
      provider: "classic",
    }));
  });

  it("sends algo_id + provider algo for an algo order", async () => {
    (api.binanceCancelOrder as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    await renderWithOrders([algoOrder]);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(api.binanceCancelOrder).toHaveBeenCalledWith({
      symbol: "BTCUSDT",
      orderId: null,
      clientOrderId: null,
      algoId: "9007199254740993123",
      clientAlgoId: "qxa-77",
      provider: "algo",
    }));
  });

  it("shows a row-scoped loading state while canceling", async () => {
    let resolveCancel: (v: any) => void = () => {};
    (api.binanceCancelOrder as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((resolve) => {
        resolveCancel = resolve;
      })
    );
    await renderWithOrders([classicOrder]);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await screen.findByRole("button", { name: "Canceling…" });
    resolveCancel({ ok: true });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Canceling…" })).not.toBeInTheDocument());
  });

  it("shows a success toast and refreshes orders after a successful cancel", async () => {
    (api.binanceCancelOrder as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    await renderWithOrders([classicOrder]);
    const callsBefore = (api.binanceOrders as ReturnType<typeof vi.fn>).mock.calls.length;

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith("Order canceled", "success"));
    await waitFor(() =>
      expect((api.binanceOrders as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(callsBefore)
    );
  });

  it("shows a safe error and keeps the row visible when cancel fails", async () => {
    (api.binanceCancelOrder as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("Unknown order sent."));
    await renderWithOrders([classicOrder]);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith("Unknown order sent.", "error"));
    expect(screen.getByText("ETHUSDT")).toBeInTheDocument();
  });

  it("disables row Cancel and explains why when no identifier is present", async () => {
    await renderWithOrders([{ ...classicOrder, order_id: null, client_order_id: null, algo_id: null, client_algo_id: null }]);
    const cancelBtn = screen.getByRole("button", { name: "Cancel" });
    expect(cancelBtn).toBeDisabled();
    expect(cancelBtn).toHaveAttribute("title", "Cannot cancel: missing order identifier");
  });

  it("still renders and cancels via Cancel All alongside a working row Cancel", async () => {
    (api.binanceCancelAllOrders as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    (api.tradingMode as ReturnType<typeof vi.fn>).mockResolvedValue({ ...baseStatus, active_mode: "BINANCE_LIVE" });
    await renderWithOrders([classicOrder]);

    const cancelAllBtn = screen.getByRole("button", { name: "Cancel All" });
    expect(cancelAllBtn).toBeInTheDocument();
    await userEvent.click(cancelAllBtn);
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(api.binanceCancelAllOrders).toHaveBeenCalled());
    // row Cancel is untouched by exercising Cancel All
    expect(api.binanceCancelOrder).not.toHaveBeenCalled();
  });

  it("row Cancel button is visible and tappable on a mobile viewport", async () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = (query: string) =>
      ({
        matches: query.includes("max-width: 767px"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList;
    try {
      (api.binanceCancelOrder as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
      await renderWithOrders([classicOrder]);

      const cancelBtn = screen.getByRole("button", { name: "Cancel" });
      expect(cancelBtn).toBeVisible();
      expect(cancelBtn).toBeEnabled();
      await userEvent.click(cancelBtn);
      await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));
      await waitFor(() => expect(api.binanceCancelOrder).toHaveBeenCalled());
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });
});
