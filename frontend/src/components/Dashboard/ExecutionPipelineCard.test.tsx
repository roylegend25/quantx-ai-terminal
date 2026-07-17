import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecutionPipelineCard from "./ExecutionPipelineCard";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({
  api: {
    binanceExecutionLogs: vi.fn().mockResolvedValue({ attempts: [] }),
    binanceTestOrder: vi.fn(),
  },
}));

function makePipeline(overrides: Partial<any> = {}) {
  return {
    symbol: "BTCUSDT",
    active_mode: "BINANCE_LIVE",
    signal_approved: true,
    execution_attempted: true,
    execution_ok: true,
    final_reason: null,
    last_attempt_at: "2026-07-12T03:38:00Z",
    stages: [
      { stage: "signal_generated", status: "success", reason: null, at: "t" },
      { stage: "champion_model", status: "success", reason: null, at: "t" },
      { stage: "risk_gate", status: "success", reason: null, at: "t" },
      { stage: "position_sizing", status: "success", reason: null, at: "t" },
      { stage: "quantity_calculation", status: "success", reason: null, at: "t" },
      { stage: "binance_validation", status: "success", reason: null, at: "t" },
      { stage: "entry_order_submitted", status: "success", reason: null, at: "t" },
      { stage: "entry_order_accepted", status: "success", reason: null, at: "t" },
      { stage: "protective_tp_submitted", status: "success", reason: null, at: "t" },
      { stage: "protective_sl_submitted", status: "success", reason: null, at: "t" },
      { stage: "protection_confirmed", status: "success", reason: null, at: "t" },
    ],
    ...overrides,
  };
}

const showToast = vi.fn();
const onRefresh = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
  vi.clearAllMocks();
  (api.binanceExecutionLogs as any).mockResolvedValue({ attempts: [] });
});

describe("ExecutionPipelineCard", () => {
  it("renders all 11 stages in the documented display order regardless of backend array order", () => {
    const pipeline = makePipeline({
      stages: [
        { stage: "protection_confirmed", status: "success", reason: null, at: "t" },
        { stage: "signal_generated", status: "success", reason: null, at: "t" },
        { stage: "position_sizing", status: "success", reason: null, at: "t" },
        { stage: "risk_gate", status: "success", reason: null, at: "t" },
      ],
    });
    const { container } = render(
      <ExecutionPipelineCard symbol="BTCUSDT" pipeline={pipeline} onRefresh={onRefresh} showToast={showToast} />
    );
    const labels = Array.from(container.querySelectorAll(".pipeline-stage-label"));
    expect(labels.map((l) => l.textContent)).toEqual([
      "Signal Generated", "Champion Model", "Risk Gate", "Position Sizing",
      "Quantity Calculation", "Binance Validation", "Entry Order Submitted", "Entry Order Accepted",
      "Protective TP Submitted", "Protective SL Submitted", "Protection Confirmed",
    ]);
  });

  it("shows a failed stage's exact reason", () => {
    const pipeline = makePipeline({
      execution_ok: false,
      final_reason: "Margin is insufficient",
      stages: [
        { stage: "signal_generated", status: "success", reason: null, at: "t" },
        { stage: "champion_model", status: "success", reason: null, at: "t" },
        { stage: "risk_gate", status: "success", reason: null, at: "t" },
        { stage: "position_sizing", status: "success", reason: null, at: "t" },
        { stage: "quantity_calculation", status: "success", reason: null, at: "t" },
        { stage: "binance_validation", status: "success", reason: null, at: "t" },
        { stage: "entry_order_submitted", status: "failed", reason: "Insufficient margin", at: "t" },
      ],
    });
    render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={pipeline} onRefresh={onRefresh} showToast={showToast} />);
    expect(screen.getByText("Insufficient margin")).toBeInTheDocument();
    // entry_order_accepted was never reached - shown as waiting, not failed
    expect(screen.getByText(/Reason: Margin is insufficient/)).toBeInTheDocument();
  });

  it("shows the Signal Approved / Execution Failed banner when approved but execution failed", () => {
    const pipeline = makePipeline({ execution_ok: false, final_reason: "Quantity below minimum" });
    render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={pipeline} onRefresh={onRefresh} showToast={showToast} />);
    expect(screen.getByText(/Signal Approved/)).toBeInTheDocument();
    expect(screen.getByText(/Execution Failed/)).toBeInTheDocument();
  });

  it("collapses a historical failure by default and never shows it as Execution Failed", () => {
    const pipeline = makePipeline({
      execution_ok: false,
      final_reason: "Position side cannot be changed if there exists open orders.",
      is_historical_attempt: true,
    });
    render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={pipeline} onRefresh={onRefresh} showToast={showToast} />);

    expect(screen.queryByText(/Execution Failed/)).not.toBeInTheDocument();
    expect(screen.getByText(/Historical/)).toBeInTheDocument();
    expect(screen.queryByText(/Position side cannot be changed/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View historical failure details" })).toBeInTheDocument();
  });

  it("expands a historical failure's exact reason on demand, and can be dismissed again", async () => {
    const pipeline = makePipeline({
      execution_ok: false,
      final_reason: "Position side cannot be changed if there exists open orders.",
      is_historical_attempt: true,
    });
    render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={pipeline} onRefresh={onRefresh} showToast={showToast} />);

    await userEvent.click(screen.getByRole("button", { name: "View historical failure details" }));
    expect(screen.getByText(/Position side cannot be changed/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Dismiss historical attempt" }));
    expect(screen.queryByText(/Position side cannot be changed/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View historical failure details" })).toBeInTheDocument();
  });

  it("does not collapse a historical attempt that succeeded", () => {
    const pipeline = makePipeline({ execution_ok: true, is_historical_attempt: true });
    render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={pipeline} onRefresh={onRefresh} showToast={showToast} />);
    expect(screen.getByText("Order Accepted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View historical failure details" })).not.toBeInTheDocument();
  });

  it("does not show the failure banner when no execution has been attempted yet", () => {
    const pipeline = makePipeline({ execution_attempted: false, execution_ok: null });
    render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={pipeline} onRefresh={onRefresh} showToast={showToast} />);
    expect(screen.queryByText(/Execution Failed/)).not.toBeInTheDocument();
    expect(screen.getByText(/No live order attempt yet/)).toBeInTheDocument();
  });

  it("lazily loads execution logs only when expanded", async () => {
    render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={makePipeline()} onRefresh={onRefresh} showToast={showToast} />);
    expect(api.binanceExecutionLogs).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Execution Logs/ }));
    await waitFor(() => expect(api.binanceExecutionLogs).toHaveBeenCalledWith(100));
  });

  it("requires confirmation before submitting a Test Order", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={makePipeline()} onRefresh={onRefresh} showToast={showToast} activeMode="BINANCE_LIVE" />);
    await userEvent.click(screen.getByRole("button", { name: /Test Order/ }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(api.binanceTestOrder).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("shows the exact exchange rejection when a Test Order fails", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    (api.binanceTestOrder as any).mockResolvedValue({
      symbol: "BTCUSDT",
      submit: { ok: false, reason: "Margin is insufficient" },
      close: null,
    });
    render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={makePipeline()} onRefresh={onRefresh} showToast={showToast} activeMode="BINANCE_LIVE" />);
    await userEvent.click(screen.getByRole("button", { name: /Test Order/ }));
    await waitFor(() => expect(screen.getByText(/Rejected — Margin is insufficient/)).toBeInTheDocument());
    confirmSpy.mockRestore();
  });

  it("never renders Binance API key/secret values", () => {
    const { container } = render(
      <ExecutionPipelineCard symbol="BTCUSDT" pipeline={makePipeline()} onRefresh={onRefresh} showToast={showToast} />
    );
    expect(container.textContent).not.toMatch(/BINANCE_API_(KEY|SECRET)=/);
    expect(container.textContent).not.toMatch(/api[_-]?secret/i);
  });

  describe("professional status banner (section 11)", () => {
    it("shows Trade Approved with every checklist item when order_ready", () => {
      const checklist = {
        model_approved: true, risk_gate_approved: true, margin_approved: true, quantity_valid: true,
        exchange_filters_passed: true, binance_validation_passed: null, order_ready: true, overall: "READY",
        blocked_reason: null, missing_amount: null,
      };
      render(
        <ExecutionPipelineCard
          symbol="BTCUSDT" pipeline={makePipeline()} statusChecklist={checklist} onRefresh={onRefresh} showToast={showToast}
        />
      );
      expect(screen.getByText(/Trade Approved/)).toBeInTheDocument();
      expect(screen.getByText(/Model Approved/)).toBeInTheDocument();
      expect(screen.getByText(/Risk Gate Approved/)).toBeInTheDocument();
      expect(screen.getByText(/Order Ready/)).toBeInTheDocument();
    });

    it("shows Execution Blocked with the exact reason and missing amount", () => {
      const checklist = {
        model_approved: true, risk_gate_approved: true, margin_approved: false, quantity_valid: true,
        exchange_filters_passed: true, binance_validation_passed: null, order_ready: false, overall: "BLOCKED",
        blocked_reason: "Insufficient margin - need $2.35 more", missing_amount: 2.35,
      };
      render(
        <ExecutionPipelineCard
          symbol="BTCUSDT" pipeline={makePipeline()} statusChecklist={checklist} onRefresh={onRefresh} showToast={showToast}
        />
      );
      expect(screen.getByText(/Execution Blocked/)).toBeInTheDocument();
      expect(screen.getByText(/Insufficient margin - need \$2.35 more/)).toBeInTheDocument();
      expect(screen.getAllByText(/\$2\.35/).length).toBeGreaterThan(0);
    });
  });

  describe("pre-trade simulation (sections 8+9)", () => {
    it("renders the full simulation when supplied", () => {
      const simulation = {
        symbol: "BTCUSDT", side: "LONG", entry_price: 63900, take_profit: 64500, stop_loss: 63200,
        expected_profit: 1.2, expected_loss: 1.4, fees: 0.06, liquidation_price: 61000,
        required_margin: 8, confidence: 85, active_model: "xgboost", market_regime: "trending",
        optimal_quantity: 0.001, optimal_leverage: 1, optimal_margin: 8,
        expected_risk_pct: 17.5, expected_reward_pct: 15.0,
      };
      render(
        <ExecutionPipelineCard symbol="BTCUSDT" pipeline={makePipeline()} simulation={simulation} onRefresh={onRefresh} showToast={showToast} />
      );
      expect(screen.getByText("Pre-Trade Simulation — if order is placed now")).toBeInTheDocument();
      expect(screen.getByText("$64,500.00")).toBeInTheDocument(); // take profit
      expect(screen.getByText("$63,200.00")).toBeInTheDocument(); // stop loss
      expect(screen.getByText("xgboost")).toBeInTheDocument();
      expect(screen.getByText("trending")).toBeInTheDocument();
    });

    it("does not render the simulation section when none is available", () => {
      render(<ExecutionPipelineCard symbol="BTCUSDT" pipeline={makePipeline()} onRefresh={onRefresh} showToast={showToast} />);
      expect(screen.queryByText(/Pre-Trade Simulation/)).not.toBeInTheDocument();
    });
  });
});
