import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import OrderBookCard from "./OrderBookCard";

const orderbook = {
  bids: [{ price: 100, qty: 1 }, { price: 99, qty: 2 }],
  asks: [{ price: 101, qty: 1.5 }, { price: 102, qty: 0.5 }],
  spread: 1,
};

describe("OrderBookCard", () => {
  it("renders bid/ask levels with a stable price/qty grid", () => {
    render(<OrderBookCard orderbook={orderbook} updatedAt={Date.now()} />);
    expect(screen.getByText("100.0")).toBeInTheDocument();
    expect(screen.getByText("101.0")).toBeInTheDocument();
    expect(screen.queryByText(/Reconnecting/)).not.toBeInTheDocument();
  });

  it("shows a reconnecting indicator instead of silently displaying a stale book as current", () => {
    vi.useFakeTimers();
    const updatedAt = Date.now();
    render(<OrderBookCard orderbook={orderbook} updatedAt={updatedAt} />);
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(screen.getByText(/Reconnecting/)).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("shows a waiting state instead of fabricating an empty order book before the first fetch", () => {
    render(<OrderBookCard orderbook={null} updatedAt={null} />);
    expect(screen.getByText(/Waiting for the order book feed/)).toBeInTheDocument();
  });
});
