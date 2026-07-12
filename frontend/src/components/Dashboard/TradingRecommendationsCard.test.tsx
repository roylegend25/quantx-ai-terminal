import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import TradingRecommendationsCard from "./TradingRecommendationsCard";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({
  api: {
    binanceSymbolRecommendations: vi.fn(),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe("TradingRecommendationsCard", () => {
  it("shows a neutral message when Binance Live Trading is not active", () => {
    render(<TradingRecommendationsCard symbol="BTCUSDT" marginData={null} liveActive={false} />);
    expect(screen.getByText(/Switch to Binance Live Trading/)).toBeInTheDocument();
    expect(api.binanceSymbolRecommendations).not.toHaveBeenCalled();
  });

  it("renders server-computed recommendation messages verbatim", () => {
    const marginData = {
      recommendations: [
        { type: "deposit_funds", message: "Deposit $71.38 more to unlock your current $80.00 position size setting.", impact_usdt: 71.38 },
      ],
    };
    render(<TradingRecommendationsCard symbol="BTCUSDT" marginData={marginData} liveActive />);
    expect(screen.getByText(/Deposit \$71.38 more/)).toBeInTheDocument();
  });

  it("shows no-adjustments message when there are no recommendations", () => {
    render(<TradingRecommendationsCard symbol="BTCUSDT" marginData={{ recommendations: [] }} liveActive />);
    expect(screen.getByText(/healthy/)).toBeInTheDocument();
  });

  it("fetches and shows the best alternative symbol when the current symbol is infeasible", async () => {
    (api.binanceSymbolRecommendations as any).mockResolvedValue({
      available: true,
      best_symbol: "XRPUSDT",
      reason: "Lowest minimum margin ($1.67) among your allowed symbols",
      candidates: [
        { symbol: "XRPUSDT", allowed: true, minimum_margin_required: 1.67, recommended_max_notional: 8.5, feasible: true },
      ],
    });
    const marginData = {
      recommendations: [
        { type: "symbol_infeasible", message: "Current BTCUSDT minimum notional ($100.00) exceeds...", impact_usdt: null },
      ],
    };
    render(<TradingRecommendationsCard symbol="BTCUSDT" marginData={marginData} liveActive />);

    await waitFor(() => expect(api.binanceSymbolRecommendations).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/Switch to XRPUSDT/)).toBeInTheDocument());
  });

  it("never renders Binance API key/secret values", () => {
    const { container } = render(
      <TradingRecommendationsCard symbol="BTCUSDT" marginData={{ recommendations: [] }} liveActive />
    );
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });
});
