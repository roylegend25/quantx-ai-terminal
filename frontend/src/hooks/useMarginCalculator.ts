import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";

const LIVE_POLL_MS = 2000;
const IDLE_POLL_MS = 15000;

/** GET /api/trading/binance/margin-calculator (Phase 26) - refreshes every
 *  2s while Binance Live Trading is the active mode (per spec), and backs
 *  off to a slow idle poll otherwise so the card still updates but doesn't
 *  spend signed-request budget on an account nobody's about to trade on.
 *  busyRef prevents overlapping fetches if a request is ever slower than
 *  the poll interval. */
export function useMarginCalculator(symbol: string, liveActive: boolean) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);
  const busyRef = useRef(false);

  const reload = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      const r = await api.binanceMarginCalculator(symbol);
      setData(r);
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
    const id = window.setInterval(reload, liveActive ? LIVE_POLL_MS : IDLE_POLL_MS);
    return () => window.clearInterval(id);
  }, [reload, liveActive]);

  return { data, loading, errored, reload };
}
