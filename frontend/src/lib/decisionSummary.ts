/** One normalized frontend model for the authoritative Active Drive V2
 *  decision. Every dashboard surface that summarizes the decision must
 *  derive from this - never from ad-hoc field picks - so Evidence,
 *  Confidence, Signal, Source Timeframe, and Prediction Horizon cannot
 *  drift apart between cards. Nothing here fabricates data: absent or
 *  failed inputs map to explicit "unavailable"/"not established" states. */

export type Signal = "LONG" | "SHORT" | "NO_TRADE";

export type EvidenceStatus =
  | "sufficient"
  | "warming_up"
  | "insufficient_samples"
  | "unavailable";

export type ConfidenceStatus =
  | "established"
  | "below_threshold"
  | "not_established"
  | "unavailable";

export type DecisionSummary = {
  decisionId: string | null;
  engine: string | null;
  signal: Signal;
  actionable: boolean;
  /** Informational forecast direction (BULLISH/BEARISH) - never a trade signal. */
  forecastDirection: string | null;
  /** Canonical model candle interval, e.g. "15m". Distinct from the horizon. */
  sourceTimeframe: string | null;
  /** How far into the future the prediction is evaluated. */
  horizonMs: number | null;
  evidence: {
    status: EvidenceStatus;
    samples: number | null;
    required: number | null;
    remaining: number | null;
    scope: string | null;
    /** Eligible source with the fewest resolved samples - the one gating readiness. */
    limitingSource: string | null;
    /** Backend ETA (ISO) for when the evidence bucket reaches the required count. */
    estimatedReadyAt: string | null;
  };
  confidence: {
    status: ConfidenceStatus;
    /** Calibrated directional confidence 0-100, only when legitimately established. */
    calibratedPct: number | null;
    requiredPct: number | null;
    calibrationSamples: number | null;
    minCalibrationSamples: number | null;
    blockReason: string | null;
    rawProbabilityUp: number | null;
    rawProbabilityDown: number | null;
  };
  pointMargin: { value: number | null; required: number | null };
  primaryBlockReason: string | null;
  secondaryBlockReasons: string[];
  /** Edge is never evaluated for an abstention (edge_block_reason: no_trade_direction). */
  edgeStatus: "evaluated" | "not_evaluated";
  edgeBlockReason: string | null;
  /** Net expected edge (0-1 fraction) once the edge gate has actually run; null while not_evaluated. */
  expectedEdge: number | null;
  entryPrice: number | null;
  target: number | null;
  stop: number | null;
  rewardRisk: number | null;
  /** Whether Active Drive V2 itself grants execution eligibility for this decision. */
  authorityStatus: "eligible" | "blocked";
  authorityReason: string | null;
  /** Whether the engine's own risk gate passed for this decision. */
  riskStatus: "passed" | "not_passed";
  updatedAtMs: number | null;
  nextEvaluationAt: string | null;
};

/** Canonical long-form timeframe labels. Lowercase `m` is minutes; uppercase
 *  `M` is a calendar month. Never derive a label via toUpperCase() - that is
 *  exactly how "30m" (30 minutes) used to render as "30M" (30 months). */
const TIMEFRAME_LONG: Record<string, string> = {
  "1m": "1 minute",
  "3m": "3 minutes",
  "5m": "5 minutes",
  "15m": "15 minutes",
  "30m": "30 minutes",
  "1h": "1 hour",
  "4h": "4 hours",
  "1d": "1 day",
  "1w": "1 week",
  "1M": "1 calendar month",
};

export function formatTimeframeLong(tf: string | null | undefined): string {
  if (!tf) return "—";
  return TIMEFRAME_LONG[tf] ?? tf;
}

export function formatHorizonMs(totalMs: number | null | undefined): string {
  if (typeof totalMs !== "number" || !Number.isFinite(totalMs) || totalMs <= 0) return "—";
  const hours = totalMs / 3_600_000;
  if (hours < 1) return `${Math.round(totalMs / 60_000)} Minutes`;
  if (hours < 48) return `${Math.round(hours)} Hours`;
  const days = hours / 24;
  if (days < 14) return `${Math.round(days)} Days`;
  return `${Math.round(days / 7)} Weeks`;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export function normalizeDecision(prediction: any): DecisionSummary {
  const engine = prediction?.decision_engine ?? null;
  const rawSignal = engine?.signal ?? prediction?.direction;
  const signal: Signal = rawSignal === "LONG" || rawSignal === "SHORT" ? rawSignal : "NO_TRADE";
  const actionable = signal !== "NO_TRADE";

  const history = engine?.decision_metrics?.history ?? null;
  const samples = num(history?.value) ?? num(engine?.history?.relevant_resolved_sample_count);
  const required = num(history?.required);
  const remaining = samples != null && required != null ? Math.max(0, required - samples) : null;
  const evidenceStatus: EvidenceStatus =
    samples == null || required == null
      ? "unavailable"
      : samples >= required
      ? "sufficient"
      : samples > 0
      ? "warming_up"
      : "insufficient_samples";

  const diag = engine?.confidence_diagnostics ?? null;
  const calSamples = num(diag?.calibration_sample_count);
  const minCalSamples = num(diag?.minimum_calibration_samples);
  const calibrated = num(engine?.directional_confidence) ?? num(diag?.calibrated_directional_confidence);
  const requiredConfidence = num(diag?.required_confidence) ?? num(engine?.required_confidence);
  const confidenceStatus: ConfidenceStatus = !diag
    ? "unavailable"
    : (diag.passed || num(engine?.directional_confidence) != null) && calibrated != null
    ? "established"
    : calSamples != null && minCalSamples != null && calSamples < minCalSamples
    ? "not_established"
    : "below_threshold";
  // A calibrated value computed from a full sample set is legitimate even
  // when it fails the gate; only a warming-up calibration must never show one.
  const showCalibrated =
    calibrated != null && (confidenceStatus === "established" || confidenceStatus === "below_threshold");

  const blockers: string[] = Array.isArray(engine?.top_reasons)
    ? engine.top_reasons.filter((r: unknown) => typeof r === "string")
    : Array.isArray(engine?.blocking_reasons)
    ? engine.blocking_reasons.filter((r: unknown) => typeof r === "string")
    : [];

  const horizonSeconds = num(engine?.forecast?.horizon_seconds) ?? num(prediction?.forecast?.horizon_seconds);

  return {
    decisionId: str(engine?.decision_id) ?? str(prediction?.decision_id),
    engine: str(engine?.engine),
    signal,
    actionable,
    forecastDirection: str(engine?.forecast?.direction) ?? str(prediction?.forecast?.direction),
    sourceTimeframe: str(engine?.timeframe) ?? str(prediction?.timeframe),
    horizonMs: horizonSeconds != null ? horizonSeconds * 1000 : null,
    evidence: {
      status: evidenceStatus,
      samples,
      required,
      remaining: num(history?.samples_remaining) ?? remaining,
      scope: str(history?.scope),
      limitingSource: str(history?.limiting_source),
      estimatedReadyAt: str(history?.estimated_ready_at),
    },
    confidence: {
      status: confidenceStatus,
      calibratedPct: showCalibrated ? calibrated * 100 : null,
      requiredPct: requiredConfidence != null ? requiredConfidence * 100 : null,
      calibrationSamples: calSamples,
      minCalibrationSamples: minCalSamples,
      blockReason: str(diag?.failure_reason) ?? str(engine?.decision_metrics?.confidence?.failure?.reason),
      rawProbabilityUp: num(diag?.raw_probability_up),
      rawProbabilityDown: num(diag?.raw_probability_down),
    },
    pointMargin: {
      value: num(engine?.point_margin),
      required: num(engine?.required_point_margin),
    },
    primaryBlockReason: blockers[0] ?? (actionable ? null : str(prediction?.risk?.reason)),
    secondaryBlockReasons: blockers.slice(1),
    edgeStatus: actionable ? "evaluated" : "not_evaluated",
    edgeBlockReason: str(engine?.edge_block_reason),
    expectedEdge: num(engine?.net_expected_edge) ?? num(engine?.expected_edge),
    entryPrice: num(engine?.entry_price),
    target: num(prediction?.target),
    stop: num(prediction?.stop),
    rewardRisk: num(engine?.risk_reward_ratio),
    authorityStatus: engine?.eligible_for_execution ? "eligible" : "blocked",
    authorityReason: str(engine?.execution?.reason),
    riskStatus: engine?.execution?.risk_gate_passed ? "passed" : "not_passed",
    updatedAtMs: num(prediction?.computed_at),
    nextEvaluationAt: str(engine?.expires_at),
  };
}

/** Human explanation of the evidence state, e.g.
 *  "Evidence warming up: 8 of 20 qualifying resolved predictions". */
export function describeEvidence(s: DecisionSummary): string {
  const { status, samples, required, limitingSource } = s.evidence;
  const limiting = limitingSource ? ` (limiting source: ${limitingSource.replace(/_/g, " ")})` : "";
  if (status === "unavailable") return "Evidence unavailable for this decision";
  if (status === "sufficient") return `${samples} of ${required} qualifying resolved predictions`;
  if (status === "warming_up")
    return `Warming up: ${samples} of ${required} qualifying resolved predictions${limiting}`;
  return `No qualifying resolved predictions yet (${required} required)${limiting}`;
}

/** Human explanation of the confidence state; never invents a number. */
export function describeConfidence(s: DecisionSummary): string {
  const c = s.confidence;
  if (c.status === "established" && c.calibratedPct != null) return `${Math.round(c.calibratedPct)}% calibrated`;
  if (c.status === "not_established" && c.calibrationSamples != null && c.minCalibrationSamples != null)
    return `${c.calibrationSamples} of ${c.minCalibrationSamples} calibration samples available`;
  if (c.status === "below_threshold")
    return c.calibratedPct != null && c.requiredPct != null
      ? `Calibrated ${Math.round(c.calibratedPct)}% is below the ${Math.round(c.requiredPct)}% required`
      : c.blockReason ?? "Calibrated confidence below the required threshold";
  return c.blockReason ?? "Calibration diagnostics unavailable";
}
