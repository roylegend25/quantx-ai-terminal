import { describe, expect, it } from "vitest";
import { engineName, formatMarketRegime, pct01 } from "./activeDrive";

describe("Active Drive V2 display contract",()=>{
  it("formats structured regimes instead of object text",()=>expect(formatMarketRegime({trend:"bearish",volatility:"high",label:"High-volatility bearish trend"})).toBe("High-volatility bearish trend"));
  it("keeps missing confidence unavailable and converts once",()=>{expect(pct01(null)).toBe("Not available");expect(pct01(.61)).toBe("61%");});
  it("separates engine identity from ensemble method",()=>expect(engineName({engine:"active_drive_v2",decision_method:"weighted_ensemble"})).toBe("Active Drive V2"));
});
