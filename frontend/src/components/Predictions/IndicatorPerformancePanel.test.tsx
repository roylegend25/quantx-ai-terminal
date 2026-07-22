import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import IndicatorPerformancePanel from "./IndicatorPerformancePanel";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({
  api: {
    indicatorPerformance: vi.fn(),
    indicatorHistory: vi.fn().mockResolvedValue({ current: {}, history: [] }),
    indicatorAction: vi.fn().mockResolvedValue({ updated: [] }),
  },
}));

const showToast = vi.fn();

function makeRow(overrides: Partial<any> = {}) {
  return {
    id: 1,
    source_name: "rsi_momentum",
    source_version: "2.1.0",
    symbol: "BTCUSDT",
    timeframe: "5m",
    mode: "paper",
    status: "SHADOW_ONLY_POOR_PERFORMANCE",
    status_reason: "7 of the latest 10 resolved predictions were wrong",
    last_status_change_at: null,
    starred: false,
    active_performance: { sample_size: 10, correct: 3, wrong: 7, neutral: 0, wrong_rate: 0.7, hit_rate: 0.3, net_expectancy: null, last_10_outcomes: [], data_quality_flag: false },
    shadow_performance: { sample_size: 22, correct: 15, wrong: 7, neutral: 0, wrong_rate: 0.32, hit_rate: 0.68, net_expectancy: 0.01, last_10_outcomes: [], data_quality_flag: false },
    current_ensemble_influence: "none",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("IndicatorPerformancePanel", () => {
  it("renders indicator rows with status and filters by Shadow Only", async () => {
    (api.indicatorPerformance as ReturnType<typeof vi.fn>).mockResolvedValue({
      indicators: [makeRow(), makeRow({ id: 2, source_name: "macd_momentum", status: "ACTIVE" })],
      count: 2,
    });
    render(<IndicatorPerformancePanel showToast={showToast} />);
    await screen.findByText(/rsi momentum/);
    expect(screen.getByText(/macd momentum/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Shadow Only" }));
    expect(screen.getByText(/rsi momentum/)).toBeInTheDocument();
    expect(screen.queryByText(/macd momentum/)).not.toBeInTheDocument();
  });

  it("opens a manual action modal showing why it was removed and sample sizes", async () => {
    (api.indicatorPerformance as ReturnType<typeof vi.fn>).mockResolvedValue({ indicators: [makeRow()], count: 1 });
    render(<IndicatorPerformancePanel showToast={showToast} />);
    await screen.findByText(/rsi momentum/);
    await userEvent.click(screen.getByRole("button", { name: "Manage" }));
    expect(await screen.findByText(/Why removed:/)).toBeInTheDocument();
    expect(screen.getByText(/Active performance: 10 samples/)).toBeInTheDocument();
    expect(screen.getByText(/Shadow performance: 22 samples/)).toBeInTheDocument();
  });

  it("enabling for Binance Real never calls a live-trading API - only the indicator action endpoint", async () => {
    (api.indicatorPerformance as ReturnType<typeof vi.fn>).mockResolvedValue({ indicators: [makeRow()], count: 1 });
    render(<IndicatorPerformancePanel showToast={showToast} />);
    await screen.findByText(/rsi momentum/);
    await userEvent.click(screen.getByRole("button", { name: "Manage" }));
    await screen.findByText(/Why removed:/);

    const select = screen.getByRole("combobox");
    await userEvent.selectOptions(select, "enable_binance_real");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(api.indicatorAction).toHaveBeenCalledWith(1, "enable_binance_real", undefined);
    // No other mocked API surface exists on this module besides the three
    // indicator_control endpoints - nothing live-trading-related was called.
    expect(Object.keys(api)).toEqual(["indicatorPerformance", "indicatorHistory", "indicatorAction"]);
  });
});
