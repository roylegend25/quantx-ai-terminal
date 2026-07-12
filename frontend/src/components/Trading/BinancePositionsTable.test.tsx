import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import BinancePositionsTable from "./BinancePositionsTable";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({
  api: {
    binanceProtectPosition: vi.fn(),
  },
}));

const gate = {
  canView: true, canTrade: true, canEditRisk: true, canCancelOrders: true, canClosePositions: true,
  disabledReason: null, requiredSteps: [],
};

function unprotectedPosition(overrides: Partial<any> = {}) {
  return {
    symbol: "ETHUSDT", side: "SHORT", quantity: 0.038, entry_price: 1800.49, mark_price: 1799.30,
    liquidation_price: 1971.95, margin_used: 6.87, margin_type: "isolated", leverage: 10,
    unrealized_pnl: 0.05, tp: null, sl: null, tp_order_id: null, sl_order_id: null,
    protection_status: "MISSING_TP_SL", id: 1,
    ...overrides,
  };
}

const noop = () => {};
const showToast = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
});

describe("BinancePositionsTable protection status (Phase 27)", () => {
  it("shows a Missing TP & SL badge for an unprotected position", () => {
    render(
      <BinancePositionsTable
        title="Real Open Positions" positionRows={[unprotectedPosition()]} busy={false} gate={gate as any}
        onEdit={noop} onRequestClose={noop} onPartialClose={noop} onCancelProtective={noop} showToast={showToast}
      />
    );
    expect(screen.getByText("Missing TP & SL")).toBeInTheDocument();
  });

  it("shows the unprotected-position warning banner", () => {
    render(
      <BinancePositionsTable
        title="Real Open Positions" positionRows={[unprotectedPosition()]} busy={false} gate={gate as any}
        onEdit={noop} onRequestClose={noop} onPartialClose={noop} onCancelProtective={noop} showToast={showToast}
      />
    );
    expect(screen.getByText(/Live position is unprotected/)).toBeInTheDocument();
  });

  it("does not show the warning banner when every position is protected", () => {
    render(
      <BinancePositionsTable
        title="Real Open Positions" positionRows={[unprotectedPosition({ protection_status: "PROTECTED" })]}
        busy={false} gate={gate as any}
        onEdit={noop} onRequestClose={noop} onPartialClose={noop} onCancelProtective={noop} showToast={showToast}
      />
    );
    expect(screen.queryByText(/Live position is unprotected/)).not.toBeInTheDocument();
    expect(screen.getByText("Protected")).toBeInTheDocument();
  });

  it("shows an Auto-Protect button only for unprotected positions", () => {
    render(
      <BinancePositionsTable
        title="Real Open Positions"
        positionRows={[unprotectedPosition(), unprotectedPosition({ symbol: "BTCUSDT", protection_status: "PROTECTED" })]}
        busy={false} gate={gate as any}
        onEdit={noop} onRequestClose={noop} onPartialClose={noop} onCancelProtective={noop} showToast={showToast}
      />
    );
    expect(screen.getAllByRole("button", { name: /Auto-Protect/ })).toHaveLength(1);
  });

  it("opens the protect modal and calls the API on confirm", async () => {
    (api.binanceProtectPosition as any).mockResolvedValue({ protection_status: "PROTECTED" });
    const onSync = vi.fn().mockResolvedValue(undefined);
    render(
      <BinancePositionsTable
        title="Real Open Positions" positionRows={[unprotectedPosition()]} busy={false} gate={gate as any}
        onEdit={noop} onRequestClose={noop} onPartialClose={noop} onCancelProtective={noop}
        onSync={onSync} showToast={showToast}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: /Auto-Protect/ }));
    expect(screen.getByText(/Add TP\/SL — ETHUSDT/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Confirm & Protect/ }));
    await waitFor(() => expect(api.binanceProtectPosition).toHaveBeenCalledWith(
      "ETHUSDT", expect.objectContaining({ take_profit: expect.any(Number), stop_loss: expect.any(Number) })
    ));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringContaining("protected"), "success"));
  });
});
