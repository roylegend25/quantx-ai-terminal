export const TIMEFRAME_CONFIG = {
  "1m": { label: "1m", ms: 60_000 }, "3m": { label: "3m", ms: 180_000 },
  "5m": { label: "5m", ms: 300_000 }, "15m": { label: "15m", ms: 900_000 },
  "30m": { label: "30m", ms: 1_800_000 }, "1h": { label: "1H", ms: 3_600_000 },
  "4h": { label: "4H", ms: 14_400_000 }, "1d": { label: "1D", ms: 86_400_000 },
  "1w": { label: "1W", ms: 604_800_000 },
} as const;

export type Timeframe = keyof typeof TIMEFRAME_CONFIG;
export const TIMEFRAME_ORDER = Object.keys(TIMEFRAME_CONFIG) as Timeframe[];
export const TIMEFRAME_STORAGE_KEY = "quantx:selected-timeframe";

export function isTimeframe(value: unknown): value is Timeframe {
  return typeof value === "string" && value in TIMEFRAME_CONFIG;
}

export function loadTimeframe(storage: Pick<Storage, "getItem"> | null = typeof localStorage === "undefined" ? null : localStorage): Timeframe {
  const stored = storage?.getItem(TIMEFRAME_STORAGE_KEY);
  return isTimeframe(stored) ? stored : "1h";
}
