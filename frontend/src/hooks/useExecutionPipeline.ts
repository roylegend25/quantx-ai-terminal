import { api } from "../services/api";
import { usePolledResource } from "./usePolledResource";

const POLL_MS = 8000;

/** GET /api/trading/binance/execution-pipeline (Phase 25) - the decision
 *  engine's current verdict overlaid with the ACTUAL outcome of the most
 *  recent real Binance order attempt, so "signal approved" and "order
 *  executed" are never conflated.
 *
 *  Backed by usePolledResource: one shared fetch loop per (symbol) key,
 *  visibility-aware, with exponential error backoff. */
export function useExecutionPipeline(symbol?: string, pollMs = POLL_MS) {
  const key = `execution-pipeline:${symbol ?? "__all__"}`;
  const { data, loading, errored, reload } = usePolledResource<any>(
    key,
    () => api.binanceExecutionPipeline(symbol),
    { normalPollMs: pollMs }
  );
  return { pipeline: data, loading, errored, reload };
}
