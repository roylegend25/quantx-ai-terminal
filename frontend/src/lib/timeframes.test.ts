import { describe, expect, it } from "vitest";
import { loadTimeframe, TIMEFRAME_CONFIG, TIMEFRAME_ORDER } from "./timeframes";

describe("timeframe mapping", () => {
  it("maps every selectable timeframe to the expected candle interval", () => {
    expect(TIMEFRAME_ORDER).toEqual(["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]);
    expect(TIMEFRAME_CONFIG["10m" as keyof typeof TIMEFRAME_CONFIG]).toBeUndefined();
    expect(TIMEFRAME_CONFIG["1w"].ms).toBe(604_800_000);
  });
  it("restores only valid persisted selections", () => {
    expect(loadTimeframe({ getItem: () => "15m" })).toBe("15m");
    expect(loadTimeframe({ getItem: () => "bogus" })).toBe("1h");
  });
});
