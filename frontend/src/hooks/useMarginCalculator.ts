import { api } from "../services/api";
import { usePolledResource } from "./usePolledResource";

const LIVE_POLL_MS = 2000;
const IDLE_POLL_MS = 15000;

/** GET /api/trading/binance/margin-calculator (Phase 26) - refreshes every
 *  2s while Binance Live Trading is the active mode (per spec), and backs
 *  off to a slow idle poll otherwise so the card still updates but doesn't
 *  spend signed-request budget on an account nobody's about to trade on.
 *
 *  Backed by usePolledResource: one shared fetch loop per symbol,
 *  visibility-aware, with exponential error backoff. liveActive changes the
 *  poll rate in place without restarting the loop. */
export function useMarginCalculator(symbol: string, liveActive: boolean) {
  const key = symbol ? `margin-calculator:${symbol}` : null;
  const { data, loading, errored, reload } = usePolledResource<any>(
    key,
    () => api.binanceMarginCalculator(symbol),
    { normalPollMs: liveActive ? LIVE_POLL_MS : IDLE_POLL_MS }
  );
  return { data, loading, errored, reload };
}
