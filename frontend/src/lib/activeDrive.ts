export type Direction = "LONG" | "SHORT" | "NEUTRAL" | "NO_TRADE";
export type MarketRegime = { trend?: string; volatility?: string; liquidity?: string; derivatives?: string; label?: string; legacy?: unknown };
export type SignalCandidate = { source_type: "ml"|"strategy"|"quant"|string; family: string; name: string; version: string; status: string; direction: Direction; calibrated_confidence: number|null; final_points: number; resolved_samples: number; historical_accuracy: number|null; recent_accuracy: number|null; realized_edge: number|null; evidence_tier: string; reason: string; rejection_reason?: string|null; rejection_code?: string|null; eligible?: boolean; evidence?: Record<string, unknown> };

export function formatMarketRegime(regime: MarketRegime | string | null | undefined): string {
  if (!regime) return "Not available";
  if (typeof regime === "string") return regime.replaceAll("_", " ");
  if (regime.label) return regime.label;
  const parts = [regime.volatility, regime.trend].filter(Boolean);
  return parts.length ? parts.join(" ").replaceAll("_", " ") : "Not available";
}

export function pct01(value: number | null | undefined, digits=0): string {
  return typeof value === "number" && Number.isFinite(value) ? `${(Math.max(0, Math.min(1, value))*100).toFixed(digits)}%` : "Not available";
}

export function engineName(decision: any): string {
  if (decision?.engine_info?.name) return decision.engine_info.name;
  return decision?.engine === "active_drive_v2" ? "Active Drive V2" : decision?.engine === "active_drive_v1" ? "Active Drive V1 (Legacy)" : "Not available";
}

export type ContributorStatus = "active" | "veto" | "abstaining" | "inactive";

/** Phase 34: classifies a candidate into the "Current Decision Contributors"
 *  taxonomy using only fields the backend already computes
 *  (backend/app/decision_engine/v2.py:_candidate / _rejection_code) - no new
 *  backend concept is invented here, this is purely a frontend grouping of
 *  existing eligible/status/rejection_code semantics:
 *   - active: eligible === true - a directional (LONG/SHORT) vote is
 *     currently contributing final_points to the decision.
 *   - inactive: status === "shadow" - a shadow-mode model that never votes
 *     live (always 0 points, always ineligible), not "this cycle" specific.
 *   - veto: not eligible, not shadow, and the backend's own rejection
 *     classification says a structural precondition blocked it this cycle
 *     (stale data, a missing required indicator, or a regime/confirmation
 *     mismatch) - the source had nothing wrong with its logic, an external
 *     gate stopped it.
 *   - abstaining: not eligible, not shadow, and there was no such
 *     structural block (rejection_code NO_TRIGGER, or none) - the source
 *     evaluated cleanly and simply found no qualifying directional signal.
 */
export function classifyContributor(c: SignalCandidate): ContributorStatus {
  if (c.eligible) return "active";
  if (c.status === "shadow") return "inactive";
  const vetoCodes = new Set(["DATA_STALE", "REQUIRED_INDICATOR_MISSING", "REGIME_MISMATCH", "CONFIRMATION_MISSING"]);
  return c.rejection_code && vetoCodes.has(c.rejection_code) ? "veto" : "abstaining";
}

export const CONTRIBUTOR_STATUS_LABEL: Record<ContributorStatus, string> = {
  active: "Active",
  veto: "Vetoed",
  abstaining: "Abstaining",
  inactive: "Inactive",
};
