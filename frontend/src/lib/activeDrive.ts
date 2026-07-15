export type Direction = "LONG" | "SHORT" | "NEUTRAL" | "NO_TRADE";
export type MarketRegime = { trend?: string; volatility?: string; liquidity?: string; derivatives?: string; label?: string; legacy?: unknown };
export type SignalCandidate = { source_type: "ml"|"strategy"|"quant"|string; family: string; name: string; version: string; status: string; direction: Direction; calibrated_confidence: number|null; final_points: number; resolved_samples: number; historical_accuracy: number|null; recent_accuracy: number|null; realized_edge: number|null; evidence_tier: string; reason: string; rejection_reason?: string|null; evidence?: Record<string, unknown> };

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
