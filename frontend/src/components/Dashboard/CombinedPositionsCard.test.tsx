import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CombinedPositionsCard from "./CombinedPositionsCard";

const paperPosition: any = { id: 1, symbol: "BTCUSDT", side: "LONG", qty: 0.1, entry: 60000, mark: 61000, pnl: 100 };
const binanceRow = { symbol: "ETHUSDT", side: "SHORT", quantity: 1, entry_price: 3000, mark_price: 2950, unrealized_pnl: 50 };

describe("CombinedPositionsCard", () => {
  it("renders both paper and Binance Real rows with distinct source badges, never merged into one total", () => {
    render(<CombinedPositionsCard paperPositions={[paperPosition]} binancePositionRows={[binanceRow]} />);
    expect(screen.getByText("PAPER")).toBeInTheDocument();
    expect(screen.getByText("BINANCE REAL")).toBeInTheDocument();
    expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.getByText("ETHUSDT")).toBeInTheDocument();
    expect(screen.getByText("PAPER unrealized PnL")).toBeInTheDocument();
    expect(screen.getByText("BINANCE REAL unrealized PnL")).toBeInTheDocument();
  });

  it("shows an honest unavailable state instead of rendering zero Binance positions on fetch failure", () => {
    render(
      <CombinedPositionsCard
        paperPositions={[paperPosition]}
        binancePositionRows={[]}
        binanceUnavailable
        binanceUnavailableReason="Binance API timed out"
      />
    );
    expect(screen.getByText("Binance Real positions unavailable")).toBeInTheDocument();
    expect(screen.getByText("Binance API timed out")).toBeInTheDocument();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    // The paper row must still render even though Binance failed.
    expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
  });

  it("shows an empty state when there are no positions from either source", () => {
    render(<CombinedPositionsCard paperPositions={[]} binancePositionRows={[]} />);
    expect(screen.getByText(/No open positions/)).toBeInTheDocument();
  });
});
