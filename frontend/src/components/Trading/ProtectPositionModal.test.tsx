import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ProtectPositionModal from "./ProtectPositionModal";

function shortPosition(overrides: Partial<any> = {}) {
  return {
    symbol: "ETHUSDT", side: "SHORT", quantity: 0.038, entry_price: 1800.49,
    mark_price: 1799.30, liquidation_price: 1971.95,
    ...overrides,
  };
}

function longPosition(overrides: Partial<any> = {}) {
  return {
    symbol: "BTCUSDT", side: "LONG", quantity: 0.002, entry_price: 50000.0,
    mark_price: 50100.0, liquidation_price: 40000.0,
    ...overrides,
  };
}

const onClose = vi.fn();
const onConfirm = vi.fn();

describe("ProtectPositionModal", () => {
  it("prefills a valid recommended TP/SL bracket for a SHORT position", () => {
    render(<ProtectPositionModal position={shortPosition()} busy={false} onClose={onClose} onConfirm={onConfirm} />);
    // Confirm button should be enabled with the prefilled defaults (no validation error)
    expect(screen.getByRole("button", { name: /Confirm & Protect/ })).toBeEnabled();
  });

  it("rejects a SHORT take-profit placed above the mark price", async () => {
    render(<ProtectPositionModal position={shortPosition()} busy={false} onClose={onClose} onConfirm={onConfirm} />);
    const tpInput = screen.getByLabelText(/Take Profit/);
    await userEvent.clear(tpInput);
    await userEvent.type(tpInput, "1850");
    expect(screen.getByText(/Take profit must be below the current price for a SHORT/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirm & Protect/ })).toBeDisabled();
  });

  it("rejects a SHORT stop-loss placed below the mark price", async () => {
    render(<ProtectPositionModal position={shortPosition()} busy={false} onClose={onClose} onConfirm={onConfirm} />);
    const slInput = screen.getByLabelText(/Stop Loss/);
    await userEvent.clear(slInput);
    await userEvent.type(slInput, "1750");
    expect(screen.getByText(/Stop loss must be above the current price for a SHORT/)).toBeInTheDocument();
  });

  it("rejects a LONG take-profit placed below the mark price", async () => {
    render(<ProtectPositionModal position={longPosition()} busy={false} onClose={onClose} onConfirm={onConfirm} />);
    const tpInput = screen.getByLabelText(/Take Profit/);
    await userEvent.clear(tpInput);
    await userEvent.type(tpInput, "40000");
    expect(screen.getByText(/Take profit must be above the current price for a LONG/)).toBeInTheDocument();
  });

  it("rejects a LONG stop-loss placed above the mark price", async () => {
    render(<ProtectPositionModal position={longPosition()} busy={false} onClose={onClose} onConfirm={onConfirm} />);
    const slInput = screen.getByLabelText(/Stop Loss/);
    await userEvent.clear(slInput);
    await userEvent.type(slInput, "60000");
    expect(screen.getByText(/Stop loss must be below the current price for a LONG/)).toBeInTheDocument();
  });

  it("calls onConfirm with the entered TP/SL when valid", async () => {
    render(<ProtectPositionModal position={shortPosition()} busy={false} onClose={onClose} onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: /Confirm & Protect/ }));
    expect(onConfirm).toHaveBeenCalled();
    const [tp, sl] = onConfirm.mock.calls[0];
    expect(tp).toBeLessThan(shortPosition().mark_price); // SHORT TP below mark
    expect(sl).toBeGreaterThan(shortPosition().mark_price); // SHORT SL above mark
  });

  it("never renders Binance API key/secret values", () => {
    const { container } = render(<ProtectPositionModal position={shortPosition()} busy={false} onClose={onClose} onConfirm={onConfirm} />);
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });
});
