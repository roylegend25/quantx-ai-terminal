import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import HyperliquidPage from "./HyperliquidPage";
import { api } from "../services/api";

vi.mock("../services/api", () => ({ api: { hyperliquidLargeTrades: vi.fn() } }));

const baseProps: any = {};

describe("HyperliquidPage", () => {
  it("renders large trades with source badges and never shows a trading button", async () => {
    vi.mocked(api.hyperliquidLargeTrades).mockResolvedValue({
      trades: [
        { coin: "BTC", side: "BUY", price: 60000, size: 2, notional: 120000, time: Date.now(), trade_id: 1, hash: "0x1" },
        { coin: "ETH", side: "SELL", price: 3000, size: 40, notional: 120000, time: Date.now(), trade_id: 2, hash: "0x2" },
      ],
      coins: ["BTC", "ETH"],
      min_notional: 50000,
      sample_window_seconds: 2.5,
      fetched_at: Date.now() / 1000,
      data_source: "hyperliquid_ws",
    });

    await act(async () => { render(<HyperliquidPage {...baseProps} />); });

    expect(await screen.findByText("BTC")).toBeInTheDocument();
    expect(screen.getByText("ETH")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /buy|sell|trade|order/i })).not.toBeInTheDocument();
  });

  it("shows an honest unavailable state instead of fabricating trades on a feed failure", async () => {
    vi.mocked(api.hyperliquidLargeTrades).mockResolvedValue({
      trades: [], coins: ["BTC", "ETH"], min_notional: 50000, sample_window_seconds: 2.5,
      fetched_at: Date.now() / 1000, data_source: "unavailable", error: "handshake failed",
    });

    await act(async () => { render(<HyperliquidPage {...baseProps} />); });

    expect(await screen.findByText("Hyperliquid feed unavailable")).toBeInTheDocument();
    expect(screen.getByText("handshake failed")).toBeInTheDocument();
  });
});
