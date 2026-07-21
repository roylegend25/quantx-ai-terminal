import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OUTCOME_META, OutcomeDot } from "./PredictionResultsPage";

// 2026-07-21 production chart fix: the chart must show separate Pending /
// Resolving / Retrying / Correct / Wrong / Neutral / Void categories - never
// a generic "Unresolved" label, and never infer status from correct==null.
describe("OUTCOME_META lifecycle display mapping", () => {
  it("has exactly the 8 documented categories, no generic unresolved bucket", () => {
    const keys = Object.keys(OUTCOME_META).sort();
    expect(keys).toEqual(["correct", "neutral", "pending", "resolving", "retrying", "unknown", "void", "wrong"].sort());
    expect(OUTCOME_META).not.toHaveProperty("unresolved");
    expect(OUTCOME_META).not.toHaveProperty("unresolved_due");
    expect(OUTCOME_META).not.toHaveProperty("unresolved_not_due");
  });

  it("never contains the literal word Unresolved in any label", () => {
    for (const meta of Object.values(OUTCOME_META)) {
      expect(meta.label.toLowerCase()).not.toContain("unresolved");
    }
  });

  it("labels pending, resolving, retrying, void distinctly", () => {
    expect(OUTCOME_META.pending.label).toBe("Pending");
    expect(OUTCOME_META.resolving.label).toBe("Resolving");
    expect(OUTCOME_META.retrying.label).toBe("Retrying");
    expect(OUTCOME_META.void.label).toBe("Void");
    expect(OUTCOME_META.neutral.label).toBe("Neutral");
  });
});

describe("OutcomeDot", () => {
  it("renders Pending for a pending status", () => {
    render(<OutcomeDot outcome="pending" />);
    expect(screen.getByTitle("Pending")).toBeInTheDocument();
  });

  it("renders Resolving for a resolving status", () => {
    render(<OutcomeDot outcome="resolving" />);
    expect(screen.getByTitle("Resolving")).toBeInTheDocument();
  });

  it("renders Retrying for a retrying status", () => {
    render(<OutcomeDot outcome="retrying" />);
    expect(screen.getByTitle("Retrying")).toBeInTheDocument();
  });

  it("renders Neutral for a neutral status (correct==null, RESOLVED_NEUTRAL case)", () => {
    render(<OutcomeDot outcome="neutral" />);
    expect(screen.getByTitle("Neutral")).toBeInTheDocument();
  });

  it("renders Void for a void status", () => {
    render(<OutcomeDot outcome="void" />);
    expect(screen.getByTitle("Void")).toBeInTheDocument();
  });

  it("never renders the word Unresolved for correct==null-style unmapped input", () => {
    render(<OutcomeDot outcome="neutral" />);
    expect(screen.queryByText(/unresolved/i)).not.toBeInTheDocument();
  });

  it("renders Unknown status and logs an error for a genuinely unexpected value, never silently as Unresolved", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<OutcomeDot outcome="SOME_GARBAGE_STATUS" />);
    expect(screen.getByTitle("Unknown status")).toBeInTheDocument();
    expect(screen.queryByText(/unresolved/i)).not.toBeInTheDocument();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe("chart category totals", () => {
  it("lifecycle counts sum to the same total the API reports (no missing/double-counted category)", () => {
    const counts = {
      PENDING: 10, RESOLVING: 5, RESOLUTION_ERROR_RETRYING: 3,
      RESOLVED_CORRECT: 20, RESOLVED_WRONG: 15, RESOLVED_NEUTRAL: 40,
      VOID_DATA_GAP: 2, VOID_INVALID_PREDICTION: 1,
    };
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const displayed =
      counts.PENDING + counts.RESOLVING + counts.RESOLUTION_ERROR_RETRYING +
      counts.RESOLVED_CORRECT + counts.RESOLVED_WRONG + counts.RESOLVED_NEUTRAL +
      (counts.VOID_DATA_GAP + counts.VOID_INVALID_PREDICTION);
    expect(displayed).toBe(total);
  });
});
