import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import TradingDiagnosticsPage from "./TradingDiagnosticsPage";
import { api } from "../services/api";

vi.mock("../services/api", () => ({
  api: {
    binanceTradingDiagnostics: vi.fn(),
    binanceTestStopOrder: vi.fn(),
  },
}));

function makeDiagnostics(overrides: Partial<any> = {}) {
  return {
    available: true,
    account: {
      market: "USDS-M Futures", environment: "PRODUCTION", position_mode: "ONE-WAY",
      multi_assets_margin: false,
      account_fields: { can_trade: true, fee_tier: 0, trade_group_id: -1, trade_group_id_interpretation: "-1 = standard retail account, not a broker sub-account" },
      margin_mode_per_position: [{ symbol: "ETHUSDT", margin_type: "isolated" }],
      api_permissions: { enableFutures: true },
    },
    order_types: {
      symbol: "ETHUSDT",
      order_types_per_exchange_info: ["LIMIT", "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"],
      entry_endpoint_used_by_this_app: "POST /fapi/v1/order (type=MARKET) - confirmed working",
      protective_endpoint_used_by_this_app: "POST /fapi/v1/order (type=STOP_MARKET / TAKE_PROFIT_MARKET) - confirmed REJECTED, code -4120",
      documented_algo_endpoint: "POST /fapi/v1/algoOrder (algoType=CONDITIONAL) - per developers.binance.com, not yet used by this app",
      algo_api_reachable: true,
      algo_api_evidence: "Binance validated the request and rejected it for a business reason (code=-1102), not a 404 - the endpoint exists and is reachable",
    },
    last_protective_attempt: {
      available: true, symbol: "ETHUSDT", side: "SHORT", created_at: "2026-07-12T09:13:18Z", final_status: "PROTECTION_FAILED",
      protective_tp_stage: { status: "failed", reason: "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead." },
      protective_sl_stage: { status: "failed", reason: "Order type not supported for this endpoint. Please use the Algo Order API endpoints instead." },
    },
    config: { require_tp_sl: true, close_if_protection_fails: false, watchdog_enabled: true },
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("TradingDiagnosticsPage", () => {
  it("renders account type, mode and endpoint findings", async () => {
    (api.binanceTradingDiagnostics as any).mockResolvedValue(makeDiagnostics());
    render(<TradingDiagnosticsPage showToast={vi.fn()} {...({} as any)} />);
    await waitFor(() => expect(screen.getByText("USDS-M Futures")).toBeInTheDocument());
    expect(screen.getByText("ONE-WAY")).toBeInTheDocument();
    expect(screen.getByText(/confirmed REJECTED, code -4120/)).toBeInTheDocument();
    expect(screen.getAllByText(/algoOrder/).length).toBeGreaterThan(0);
  });

  it("shows the last protective attempt's real TP/SL rejection reasons", async () => {
    (api.binanceTradingDiagnostics as any).mockResolvedValue(makeDiagnostics());
    render(<TradingDiagnosticsPage showToast={vi.fn()} {...({} as any)} />);
    await waitFor(() => expect(screen.getAllByText(/Algo Order API endpoints instead/).length).toBeGreaterThan(0));
  });

  it("shows an unavailable state without crashing when Binance can't be reached", async () => {
    (api.binanceTradingDiagnostics as any).mockResolvedValue({ available: false, reason: "Binance account is not connected" });
    render(<TradingDiagnosticsPage showToast={vi.fn()} {...({} as any)} />);
    await waitFor(() => expect(screen.getByText("Binance account is not connected")).toBeInTheDocument());
  });

  it("never renders Binance API key/secret values", async () => {
    (api.binanceTradingDiagnostics as any).mockResolvedValue(makeDiagnostics());
    const { container } = render(<TradingDiagnosticsPage showToast={vi.fn()} {...({} as any)} />);
    await waitFor(() => expect(screen.getByText("USDS-M Futures")).toBeInTheDocument());
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });
});
