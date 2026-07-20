import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ConfidenceThresholdModal from "./ConfidenceThresholdModal";
import { api } from "../../services/api";

vi.mock("../../services/api", () => ({ api: { adminPreviewConfidenceThreshold: vi.fn() } }));

const config = { active_drive_min_confidence: 0.6, active_drive_min_confidence_floor: 0.55 };

describe("ConfidenceThresholdModal", () => {
  it("blocks saving below the hard floor without ever calling the preview API", () => {
    const onSave = vi.fn();
    render(<ConfidenceThresholdModal config={config} busy={false} onClose={() => {}} onSave={onSave} />);

    const input = screen.getByRole("spinbutton");
    fireEvent.change(input, { target: { value: "0.3" } });

    expect(screen.getByText(/Cannot be set below the institutional safety floor of 0.55/)).toBeInTheDocument();
    const saveBtn = screen.getByRole("button", { name: /Save Confidence Gate/ });
    expect(saveBtn).toBeDisabled();
    expect(api.adminPreviewConfidenceThreshold).not.toHaveBeenCalled();
  });

  it("shows the risk classification and history preview for a valid value, then saves the numeric threshold", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.adminPreviewConfidenceThreshold).mockResolvedValue({
      proposed_threshold: 0.72,
      floor: 0.55,
      risk_classification: "MODERATE",
      history: {
        lookback_days: 30, total_decisions: 10, current_threshold: 0.6,
        would_pass_at_current_threshold: 8, would_pass_at_current_threshold_pct: 80,
        proposed_threshold: 0.72, would_pass_at_proposed_threshold: 5, would_pass_at_proposed_threshold_pct: 50,
      },
    });
    const onSave = vi.fn();
    render(<ConfidenceThresholdModal config={config} busy={false} onClose={() => {}} onSave={onSave} />);

    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "0.72" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });

    expect(await screen.findByText("Moderate")).toBeInTheDocument();
    expect(screen.getByText(/50% would clear this one/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Save Confidence Gate/ }));
    expect(onSave).toHaveBeenCalledWith(0.72);
    vi.useRealTimers();
  });
});
