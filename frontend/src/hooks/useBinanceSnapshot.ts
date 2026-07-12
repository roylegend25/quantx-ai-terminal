import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";

/** Phase 29: one shared Binance snapshot instead of every page/card running
 *  its own 10s poll loop against several separate endpoints. This hook is a
 *  module-level singleton - no matter how many components call
 *  useBinanceSnapshot() at once (Dashboard, Binance Real, Positions, Risk,
 *  Execution, Diagnostics all mounted in different tabs), there is exactly
 *  ONE fetch loop and ONE in-flight request at a time, all subscribers just
 *  read the same result. The backend's own shared cache
 *  (app.exchanges.binance_snapshot_service) already coalesces this across
 *  browser tabs/processes; this hook coalesces it across components within
 *  one page too. */

export type BinanceSnapshot = {
  updated_at: string;
  stale: boolean;
  rate_limited: boolean;
  retry_after_seconds: number | null;
  account: any;
  balances: any[];
  positions: any[];
  orders: any[];
  income: any[];
  protection: any;
  readiness: any;
  errors: string[];
} | null;

const NORMAL_POLL_MS = 5000;
const HIDDEN_POLL_MS = 45000;

let snapshot: BinanceSnapshot = null;
let loading = true;
let timerId: number | null = null;
let inFlight: Promise<void> | null = null;
let started = false;
const subscribers = new Set<(s: BinanceSnapshot, l: boolean) => void>();

function notify() {
  subscribers.forEach((fn) => fn(snapshot, loading));
}

function nextDelayMs(): number {
  if (typeof document !== "undefined" && document.visibilityState === "hidden") {
    return HIDDEN_POLL_MS;
  }
  if (snapshot?.rate_limited && snapshot.retry_after_seconds != null) {
    // wait out the server's own cooldown, plus jitter so many open tabs
    // don't all retry on the exact same tick.
    return Math.max((snapshot.retry_after_seconds + Math.random() * 2) * 1000, NORMAL_POLL_MS);
  }
  return NORMAL_POLL_MS;
}

async function fetchOnce(force = false): Promise<void> {
  if (inFlight) return inFlight; // coalesce concurrent triggers (e.g. two components mounting together)
  inFlight = (async () => {
    try {
      const result = force ? await api.binanceSnapshotRefresh() : await api.binanceSnapshot();
      if (result) snapshot = result; // keep the previous good snapshot on a falsy/failed response
    } catch {
      // network hiccup - never wipe the last known good snapshot for this
    } finally {
      loading = false;
      inFlight = null;
      notify();
    }
  })();
  return inFlight;
}

function scheduleNext() {
  if (timerId != null) window.clearTimeout(timerId);
  timerId = window.setTimeout(async () => {
    await fetchOnce();
    scheduleNext();
  }, nextDelayMs());
}

function ensureStarted() {
  if (started) return;
  started = true;
  fetchOnce().then(scheduleNext);
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        // wake up immediately instead of waiting out the long hidden-tab interval
        if (timerId != null) window.clearTimeout(timerId);
        fetchOnce().then(scheduleNext);
      }
    });
  }
}

export function useBinanceSnapshot() {
  const [state, setState] = useState<{ snapshot: BinanceSnapshot; loading: boolean }>({ snapshot, loading });

  useEffect(() => {
    ensureStarted();
    const handler = (s: BinanceSnapshot, l: boolean) => setState({ snapshot: s, loading: l });
    subscribers.add(handler);
    setState({ snapshot, loading }); // pick up a result that landed before this mount
    return () => {
      subscribers.delete(handler);
    };
  }, []);

  const refresh = useCallback(() => fetchOnce(true), []);

  return { snapshot: state.snapshot, loading: state.loading, refresh };
}
