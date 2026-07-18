import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LiquidationHeatmapCard from "./LiquidationHeatmapCard";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({ api: { liquidationHeatmap: vi.fn() } }));

class MockResizeObserver {
  observe() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", MockResizeObserver);
  vi.stubGlobal("requestAnimationFrame", () => 0);
  vi.stubGlobal("cancelAnimationFrame", () => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const baseSnapshot = {
  symbol: "BTCUSDT", generated_at: Date.now() / 1000, current_price: 100, atr: 1,
  price_low: 90, price_high: 110, clusters: [], largest_long_cluster: null, largest_short_cluster: null,
  largest_cluster: null, nearest_long_cluster: null, nearest_short_cluster: null, liquidity_imbalance_pct: 0,
  magnet_price: null, stop_hunt_zone: null, risk_score: 10, recent_liquidations: { count: 0, notional: 0, pressure: "NEUTRAL" },
  data_source: "binance_estimated",
};

describe("LiquidationHeatmapCard", () => {
  it("labels the heatmap as ESTIMATED, never as official CoinGlass data, when no subscription is configured", async () => {
    vi.mocked(api.liquidationHeatmap).mockResolvedValue({
      ...baseSnapshot, provider: "estimated", coinglass_entitled: false,
      provider_note: "No CoinGlass subscription configured - serving a Binance-derived estimate.",
    });
    await act(async () => { render(<LiquidationHeatmapCard symbol="BTCUSDT" />); });
    expect(await screen.findByText(/ESTIMATED/)).toBeInTheDocument();
    expect(screen.getByText(/No CoinGlass subscription configured/)).toBeInTheDocument();
  });

  it("surfaces CoinGlass entitlement without ever claiming official data is being served", async () => {
    vi.mocked(api.liquidationHeatmap).mockResolvedValue({
      ...baseSnapshot, provider: "estimated", coinglass_entitled: true,
      provider_note: "CoinGlass key detected but the official-provider client is not yet wired in - still serving the Binance-derived estimate.",
    });
    await act(async () => { render(<LiquidationHeatmapCard symbol="BTCUSDT" />); });
    expect(await screen.findByText(/ESTIMATED/)).toBeInTheDocument();
    expect(screen.getByText(/not yet wired in/)).toBeInTheDocument();
  });
});
