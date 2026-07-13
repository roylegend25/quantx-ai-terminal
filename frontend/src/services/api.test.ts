import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

/** Live-verified root cause of "row Cancel fails, Cancel All works": this
 * account's real Binance order ids run up to 19 digits (e.g.
 * 8389766233056201670), past Number.MAX_SAFE_INTEGER (2^53-1, 16 digits).
 * Sending order_id as a JSON *number* - either because the browser's own
 * JSON.parse silently rounded it while reading the orders list, or because
 * a caller re-coerced it via Number(...) before sending it back - corrupts
 * the last few digits, and Binance correctly rejects the corrupted id with
 * "Unknown order sent." These tests guard the exact fix: the cancel
 * request body must carry these ids as JSON strings end to end. */

const BIG_ORDER_ID = "8389766233056201670";
const BIG_ALGO_ID = "9007199254740993123"; // also past MAX_SAFE_INTEGER

function mockFetchOk(body: unknown = { ok: true }) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  });
}

beforeEach(() => {
  localStorage.setItem("quantx_token", "test-token");
});

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("api.binanceCancelOrder request body", () => {
  it("sends order_id as a JSON string, never a number, for a big classic id", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);

    await api.binanceCancelOrder({ symbol: "ETHUSDT", orderId: BIG_ORDER_ID, provider: "classic" });

    const [, init] = fetchMock.mock.calls[0];
    const rawBody = init.body as string;
    // The raw JSON text itself must quote the id - if this ever regresses
    // to `Number(orderId)`, the raw text would contain an unquoted numeral
    // that JSON.parse (and Binance) would read back as a different value.
    expect(rawBody).toContain(`"order_id":"${BIG_ORDER_ID}"`);
    expect(rawBody).not.toMatch(/"order_id":8389766233056201/);

    const parsed = JSON.parse(rawBody);
    expect(parsed.order_id).toBe(BIG_ORDER_ID);
    expect(typeof parsed.order_id).toBe("string");
  });

  it("sends algo_id as a JSON string, never a number, for a big algo id", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);

    await api.binanceCancelOrder({ symbol: "BTCUSDT", algoId: BIG_ALGO_ID, provider: "algo" });

    const [, init] = fetchMock.mock.calls[0];
    const parsed = JSON.parse(init.body as string);
    expect(parsed.algo_id).toBe(BIG_ALGO_ID);
    expect(typeof parsed.algo_id).toBe("string");
  });

  it("sends null (not undefined) for absent identifiers", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);

    await api.binanceCancelOrder({ symbol: "ETHUSDT", orderId: BIG_ORDER_ID });

    const [, init] = fetchMock.mock.calls[0];
    const rawBody = init.body as string;
    expect(rawBody).not.toContain("undefined");
    const parsed = JSON.parse(rawBody);
    expect(parsed.client_order_id).toBeNull();
    expect(parsed.algo_id).toBeNull();
    expect(parsed.client_algo_id).toBeNull();
    expect(parsed.provider).toBeNull();
  });

  it("posts to the single shared cancel-order endpoint", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);

    await api.binanceCancelOrder({ symbol: "ETHUSDT", orderId: BIG_ORDER_ID });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/trading/binance/cancel-order");
    expect(init.method).toBe("POST");
  });
});
