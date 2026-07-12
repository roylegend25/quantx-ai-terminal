import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";

const POLL_MS = 8000;

/** GET /api/trading/binance/execution-pipeline (Phase 25) - the decision
 *  engine's current verdict overlaid with the ACTUAL outcome of the most
 *  recent real Binance order attempt, so "signal approved" and "order
 *  executed" are never conflated. busyRef prevents a slow poll tick from
 *  overlapping the next one. */
export function useExecutionPipeline(symbol?: string, pollMs = POLL_MS) {
  const [pipeline, setPipeline] = useState<any>(null);
  const [errored, setErrored] = useState(false);
  const [loading, setLoading] = useState(true);
  const busyRef = useRef(false);

  const reload = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      const r = await api.binanceExecutionPipeline(symbol);
      setPipeline(r);
      setErrored(false);
    } catch {
      setErrored(true);
    } finally {
      busyRef.current = false;
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    reload();
    const id = window.setInterval(reload, pollMs);
    return () => window.clearInterval(id);
  }, [reload, pollMs]);

  return { pipeline, loading, errored, reload };
}
