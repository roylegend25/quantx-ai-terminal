/**
 * Timezone-aware timestamp formatting for the whole frontend.
 *
 * Backends store UTC. Some endpoints serialize with an explicit offset
 * ("...+00:00" / "Z"), but the ML endpoints emit *naive* ISO strings
 * ("2026-07-09T21:48:41.439353") - `new Date()` would silently read those
 * as browser-local time, shifting every timestamp by the viewer's UTC
 * offset. parseTimestamp() is the one place that gets this right; nothing
 * else in the app should call `new Date(isoString)` on a backend value.
 *
 * Display timezone is the browser's resolved zone by default
 * (Intl.DateTimeFormat().resolvedOptions().timeZone), switchable to UTC via
 * a persisted "time display" preference (see setTimeDisplayMode).
 */

import { useSyncExternalStore } from "react";

export type TimeDisplayMode = "local" | "utc";

const STORAGE_KEY = "quantx_time_display";

let currentMode: TimeDisplayMode = (() => {
  try {
    return localStorage.getItem(STORAGE_KEY) === "utc" ? "utc" : "local";
  } catch {
    return "local";
  }
})();

const listeners = new Set<() => void>();

export function getTimeDisplayMode(): TimeDisplayMode {
  return currentMode;
}

export function setTimeDisplayMode(mode: TimeDisplayMode): void {
  currentMode = mode;
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* private mode / storage full - preference just won't persist */
  }
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** React hook: re-renders the component when the Local/UTC preference
 *  changes, so no page reload is needed after toggling. */
export function useTimeDisplayMode(): TimeDisplayMode {
  return useSyncExternalStore(subscribe, getTimeDisplayMode, getTimeDisplayMode);
}

export function getBrowserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

// ---------------------------------------------------------------- parsing

const HAS_OFFSET_RE = /(?:Z|z|[+-]\d{2}:?\d{2})$/;
const ISO_DATETIME_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;

/**
 * Accepts ISO 8601 (with or without offset - offsetless is treated as UTC,
 * matching how the backend serializes), unix seconds, unix milliseconds, or
 * a Date. Returns null for null/undefined/empty or anything unparseable -
 * callers never see "Invalid Date".
 */
export function parseTimestamp(value: unknown): Date | null {
  if (value === null || value === undefined || value === "") return null;

  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }

  if (typeof value === "number") {
    if (!Number.isFinite(value) || value <= 0) return null;
    // epoch ms are ~1.7e12 in this era; epoch seconds ~1.7e9
    const ms = value >= 1e12 ? value : value >= 1e9 ? value * 1000 : NaN;
    if (!Number.isFinite(ms)) return null;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  if (typeof value === "string") {
    const s = value.trim();
    if (!s) return null;
    if (/^\d+(\.\d+)?$/.test(s)) return parseTimestamp(Number(s));
    // naive ISO datetime -> UTC ("do not interpret UTC as browser-local")
    const iso = ISO_DATETIME_RE.test(s) && !HAS_OFFSET_RE.test(s) ? `${s}Z` : s;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  return null;
}

export function isValidTimestamp(value: unknown): boolean {
  return parseTimestamp(value) !== null;
}

// ------------------------------------------------------------- formatting

/** Formatter cache: Intl.DateTimeFormat construction is expensive and the
 *  registry/notification tables format hundreds of cells per render. */
const formatterCache = new Map<string, Intl.DateTimeFormat>();

function getFormatter(options: Intl.DateTimeFormatOptions, timeZone?: string): Intl.DateTimeFormat {
  const key = JSON.stringify(options) + (timeZone ?? "local");
  let f = formatterCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-GB", timeZone ? { ...options, timeZone } : options);
    formatterCache.set(key, f);
  }
  return f;
}

function displayZone(mode?: TimeDisplayMode): string | undefined {
  return (mode ?? currentMode) === "utc" ? "UTC" : undefined;
}

/** en-GB gives "7 Jul 2026" ordering but lowercase "pm" - assemble from
 *  parts so day-periods render as "PM" like the rest of the terminal. */
function formatParts(d: Date, options: Intl.DateTimeFormatOptions, timeZone?: string): string {
  return getFormatter(options, timeZone)
    .formatToParts(d)
    .map((p) => (p.type === "dayPeriod" ? p.value.toUpperCase() : p.value))
    .join("");
}

const NULL_TEXT = "Not available";
const INVALID_TEXT = "—";

function fmt(value: unknown, options: Intl.DateTimeFormatOptions, mode?: TimeDisplayMode): string {
  if (value === null || value === undefined || value === "") return NULL_TEXT;
  const d = parseTimestamp(value);
  if (!d) return INVALID_TEXT;
  return formatParts(d, options, displayZone(mode));
}

/** Desktop compact: "7 Jul 2026, 1:42 PM" */
export function formatLocalDateTime(value: unknown, mode?: TimeDisplayMode): string {
  return fmt(value, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true }, mode);
}

/** Mobile compact: "7 Jul, 1:42 PM" */
export function formatCompactLocalDateTime(value: unknown, mode?: TimeDisplayMode): string {
  return fmt(value, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true }, mode);
}

/** Full: "7 July 2026 at 1:42:18 PM IST" (zone name reflects the viewer's
 *  timezone - or UTC when the preference is set to UTC). */
export function formatFullLocalDateTime(value: unknown, mode?: TimeDisplayMode): string {
  return fmt(
    value,
    {
      day: "numeric",
      month: "long",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
      timeZoneName: "short",
    },
    mode
  ).replace(/(\d{4}),?\s/, "$1 at ");
}

/** Always-UTC rendering for tooltips: "7 July 2026 at 8:12:18 AM" */
export function formatUtcDateTime(value: unknown): string {
  if (value === null || value === undefined || value === "") return NULL_TEXT;
  const d = parseTimestamp(value);
  if (!d) return INVALID_TEXT;
  return formatParts(
    d,
    { day: "numeric", month: "long", year: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true },
    "UTC"
  ).replace(/(\d{4}),?\s/, "$1 at ");
}

/** Short timezone name for an instant, e.g. "IST", "EDT", "GMT+5:30" -
 *  instant-specific so daylight saving renders correctly. */
export function formatTimeZoneName(value: unknown, mode?: TimeDisplayMode): string {
  const d = parseTimestamp(value) ?? new Date();
  if ((mode ?? currentMode) === "utc") return "UTC";
  const part = getFormatter({ timeZoneName: "short" })
    .formatToParts(d)
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? getBrowserTimeZone();
}
