import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { usePolledResource, __resetPolledResourceRegistryForTests } from "./usePolledResource";

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("usePolledResource", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
  });

  afterEach(() => {
    __resetPolledResourceRegistryForTests();
    vi.useRealTimers();
  });

  it("shares one fetch loop across multiple mounted consumers of the same key", async () => {
    let calls = 0;
    const fetchFn = vi.fn(async () => {
      calls += 1;
      return { value: calls };
    });

    const a = renderHook(() => usePolledResource("shared-key", fetchFn, { normalPollMs: 1000 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const b = renderHook(() => usePolledResource("shared-key", fetchFn, { normalPollMs: 1000 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    // Both consumers mounted the same key - only one fetch should have run,
    // and the second consumer should immediately see the first's result.
    expect(calls).toBe(1);
    expect(a.result.current.data).toEqual({ value: 1 });
    expect(b.result.current.data).toEqual({ value: 1 });

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(calls).toBe(2);
    expect(a.result.current.data).toEqual({ value: 2 });
    expect(b.result.current.data).toEqual({ value: 2 });
  });

  it("pauses to the slow hidden-tab interval and wakes immediately on becoming visible", async () => {
    let calls = 0;
    const fetchFn = vi.fn(async () => { calls += 1; return calls; });

    renderHook(() => usePolledResource("visibility-key", fetchFn, { normalPollMs: 1000, hiddenPollMs: 30000 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(calls).toBe(1);

    setVisibility("hidden");
    // Well past the normal 1000ms interval, but short of the hidden 30000ms one.
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(calls).toBe(1);

    act(() => { setVisibility("visible"); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(calls).toBe(2);
  });

  it("backs off exponentially on consecutive failures and resets after a success", async () => {
    let shouldFail = true;
    const fetchFn = vi.fn(async () => {
      if (shouldFail) throw new Error("boom");
      return "ok";
    });

    const { result } = renderHook(() => usePolledResource("backoff-key", fetchFn, { normalPollMs: 1000, maxBackoffMs: 60000 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.errored).toBe(true);
    expect(fetchFn).toHaveBeenCalledTimes(1);

    // First retry backs off to 2x the normal interval - not immediately at 1000ms.
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchFn).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchFn).toHaveBeenCalledTimes(2);

    shouldFail = false;
    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(fetchFn).toHaveBeenCalledTimes(3);
    expect(result.current.errored).toBe(false);
    expect(result.current.data).toBe("ok");

    // Backoff has reset - the next tick happens after exactly the normal interval again.
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchFn).toHaveBeenCalledTimes(4);
  });

  it("coalesces a manual reload() with an already in-flight scheduled fetch", async () => {
    let resolveFetch: (v: number) => void = () => {};
    let calls = 0;
    const fetchFn = vi.fn(() => {
      calls += 1;
      return new Promise<number>((resolve) => { resolveFetch = resolve; });
    });

    const { result } = renderHook(() => usePolledResource("inflight-key", fetchFn, { normalPollMs: 1000 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(calls).toBe(1);

    // A manual reload while the first request is still in flight must not
    // trigger a second network call.
    act(() => { result.current.reload(); });
    expect(calls).toBe(1);

    await act(async () => {
      resolveFetch(42);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.data).toBe(42);
  });
});
