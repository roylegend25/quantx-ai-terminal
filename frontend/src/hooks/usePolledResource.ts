import { useCallback, useEffect, useState } from "react";

/** Generic keyed polling coordinator, generalizing the shared-loop/
 *  in-flight-dedup/visibility-aware pattern already proven in
 *  useBinanceSnapshot.ts to any per-key resource (current pipeline,
 *  execution pipeline, margin calculator, ...).
 *
 *  For a given key there is exactly ONE fetch loop and ONE in-flight
 *  request no matter how many components call usePolledResource(key, ...)
 *  at once - they all share the same result and the same timer. The loop
 *  pauses to a slow background rate while the tab is hidden and wakes
 *  immediately on becoming visible again, and backs off exponentially
 *  (capped) on consecutive failures, resetting the instant a request
 *  succeeds - so a real outage doesn't turn into a tight failing-request
 *  loop, but recovery is still fast once the backend is healthy again. */

type FetchFn<T> = () => Promise<T>;

interface ResourceState<T> {
  data: T | null;
  loading: boolean;
  errored: boolean;
}

interface Entry<T> {
  state: ResourceState<T>;
  fetchFn: FetchFn<T>;
  normalPollMs: number;
  hiddenPollMs: number;
  maxBackoffMs: number;
  timerId: number | null;
  inFlight: Promise<void> | null;
  consecutiveErrors: number;
  subscribers: Set<(s: ResourceState<T>) => void>;
  visibilityHandler: (() => void) | null;
}

const registry = new Map<string, Entry<any>>();

export interface PolledResourceOptions {
  /** Poll interval while the tab is visible and the resource is healthy. */
  normalPollMs: number;
  /** Poll interval while the tab is hidden. Defaults to 6x normalPollMs. */
  hiddenPollMs?: number;
  /** Ceiling for the exponential error backoff. Defaults to 8x normalPollMs. */
  maxBackoffMs?: number;
}

function getEntry<T>(key: string, fetchFn: FetchFn<T>, options: PolledResourceOptions): Entry<T> {
  let entry = registry.get(key);
  if (!entry) {
    entry = {
      state: { data: null, loading: true, errored: false },
      fetchFn,
      normalPollMs: options.normalPollMs,
      hiddenPollMs: options.hiddenPollMs ?? options.normalPollMs * 6,
      maxBackoffMs: options.maxBackoffMs ?? options.normalPollMs * 8,
      timerId: null,
      inFlight: null,
      consecutiveErrors: 0,
      subscribers: new Set(),
      visibilityHandler: null,
    };
    registry.set(key, entry);
  } else {
    // Keep the entry's fetch target/poll rates current - a later render may
    // pass a different fetchFn closure (same endpoint, fresher params) or a
    // different rate (e.g. useMarginCalculator's liveActive toggling between
    // its fast/slow interval) without tearing down and restarting the loop.
    entry.fetchFn = fetchFn;
    entry.normalPollMs = options.normalPollMs;
    entry.hiddenPollMs = options.hiddenPollMs ?? options.normalPollMs * 6;
    entry.maxBackoffMs = options.maxBackoffMs ?? options.normalPollMs * 8;
  }
  return entry;
}

function notify<T>(entry: Entry<T>) {
  entry.subscribers.forEach((fn) => fn(entry.state));
}

function nextDelayMs<T>(entry: Entry<T>): number {
  if (typeof document !== "undefined" && document.visibilityState === "hidden") {
    return entry.hiddenPollMs;
  }
  if (entry.consecutiveErrors > 0) {
    const backoff = entry.normalPollMs * Math.pow(2, Math.min(entry.consecutiveErrors, 6));
    return Math.min(backoff, entry.maxBackoffMs);
  }
  return entry.normalPollMs;
}

function fetchOnce<T>(entry: Entry<T>): Promise<void> {
  if (entry.inFlight) return entry.inFlight; // coalesce concurrent triggers
  entry.inFlight = (async () => {
    try {
      const result = await entry.fetchFn();
      entry.state = { data: result, loading: false, errored: false };
      entry.consecutiveErrors = 0;
    } catch {
      // keep the last known-good data on a failure - never wipe the panel
      entry.state = { ...entry.state, loading: false, errored: true };
      entry.consecutiveErrors += 1;
    } finally {
      entry.inFlight = null;
      notify(entry);
    }
  })();
  return entry.inFlight;
}

function scheduleNext<T>(entry: Entry<T>) {
  if (entry.timerId != null) window.clearTimeout(entry.timerId);
  entry.timerId = window.setTimeout(async () => {
    await fetchOnce(entry);
    scheduleNext(entry);
  }, nextDelayMs(entry));
}

function ensureStarted<T>(entry: Entry<T>) {
  if (entry.visibilityHandler) return; // already running for this key
  fetchOnce(entry).then(() => scheduleNext(entry));
  if (typeof document !== "undefined") {
    entry.visibilityHandler = () => {
      if (entry.inFlight) return; // let the in-flight request land, it will reschedule itself
      if (document.visibilityState === "visible") {
        // wake up immediately instead of waiting out the hidden-tab interval
        if (entry.timerId != null) window.clearTimeout(entry.timerId);
        fetchOnce(entry).then(() => scheduleNext(entry));
      } else {
        // becoming hidden: re-arm the pending timer against the (now
        // longer) hidden interval instead of leaving the short visible-rate
        // timer to fire once more before the slowdown takes effect.
        scheduleNext(entry);
      }
    };
    document.addEventListener("visibilitychange", entry.visibilityHandler);
  }
}

export function usePolledResource<T>(
  key: string | null,
  fetchFn: FetchFn<T>,
  options: PolledResourceOptions
) {
  const [state, setState] = useState<ResourceState<T>>(
    key ? getEntry<T>(key, fetchFn, options).state : { data: null, loading: true, errored: false }
  );

  useEffect(() => {
    if (!key) return;
    const entry = getEntry<T>(key, fetchFn, options);
    ensureStarted(entry);
    const handler = (s: ResourceState<T>) => setState(s);
    entry.subscribers.add(handler);
    setState(entry.state); // pick up a result that landed before this mount
    return () => {
      entry.subscribers.delete(handler);
    };
    // Re-subscribing only needs to happen when the key changes - fetchFn/
    // options updates are absorbed into the existing entry above without
    // restarting the loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const reload = useCallback(() => {
    if (!key) return Promise.resolve();
    return fetchOnce(getEntry<T>(key, fetchFn, options));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { data: state.data, loading: state.loading, errored: state.errored, reload };
}

/** Test-only escape hatch: drop all shared polling state between tests. */
export function __resetPolledResourceRegistryForTests() {
  for (const entry of registry.values()) {
    if (entry.timerId != null) window.clearTimeout(entry.timerId);
    if (entry.visibilityHandler && typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", entry.visibilityHandler);
    }
  }
  registry.clear();
}
