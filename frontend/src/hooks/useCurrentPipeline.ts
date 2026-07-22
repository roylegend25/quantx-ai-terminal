import { api } from "../services/api";
import { usePolledResource } from "./usePolledResource";

const POLL_MS = 8000;

export interface CurrentPipeline {
  decision_id: string | null;
  cycle_id: string | null;
  authority_id: string | null;
  execution_request_id: number | null;
  order_id: number | null;
  position_id: number | null;
  engine: string;
  symbol: string;
  timeframe: string;
  timeframe_source: string;
  signal: string;
  candidate_direction: string | null;
  actionable: boolean;
  confidence: number | null;
  confidence_threshold: number | null;
  confidence_passed: boolean | null;
  trade_levels_valid: boolean | null;
  edge_supported: boolean | null;
  expected_edge: number | null;
  edge_block_reason: string | null;
  authority_status: "not_issued" | "granted" | "blocked" | "expired";
  authority_block_reason: string | null;
  risk_allowed: boolean | null;
  risk_reason: string | null;
  execution_mode: string | null;
  execution_status: string;
  current_stage: string;
  completed_stages: string[];
  pending_stage: string | null;
  final_block_reason: string | null;
  created_at: string | null;
  updated_at: string | null;
  next_evaluation_at: string | null;
  stale: boolean;
}

/** GET /api/trading/pipeline/current - the one authoritative view of the
 *  current decision/execution pipeline, mode-agnostic and always keyed to
 *  the same authoritative execution timeframe the scheduler itself uses
 *  (never the dashboard's freely-chosen chart interval, never a hardcoded
 *  timeframe). This is what the Dashboard's Execution Pipeline card reads;
 *  useExecutionPipeline stays scoped to the Binance Live tab's real-order
 *  attempt transparency.
 *
 *  Backed by usePolledResource: one shared fetch loop per symbol (not per
 *  mounted component), visibility-aware, with exponential error backoff. */
export function useCurrentPipeline(symbol: string, pollMs = POLL_MS) {
  const key = symbol ? `current-pipeline:${symbol}` : null;
  const { data, loading, errored, reload } = usePolledResource<CurrentPipeline>(
    key,
    () => api.currentPipeline(symbol),
    { normalPollMs: pollMs }
  );
  return { pipeline: data, loading, errored, reload };
}
