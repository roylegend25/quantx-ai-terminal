import { formatLocalDateTime, parseTimestamp } from "../utils/dateTime";

export function fmtUsd(n?: number | null, digits = 2): string {
  if (typeof n !== "number" || Number.isNaN(n)) return "—";
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function fmtNum(n?: number | null, digits = 2): string {
  if (typeof n !== "number" || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

export function fmtPct(n?: number | null, digits = 2): string {
  if (typeof n !== "number" || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

/** Formats a chart-axis number with sensible precision — avoids raw
 *  high-precision floats overflowing the axis gutter and getting clipped. */
export function fmtAxisNumber(n: number): string {
  if (typeof n !== "number" || Number.isNaN(n)) return "";
  const abs = Math.abs(n);
  const digits = abs >= 1000 ? 0 : abs >= 1 ? 1 : 4;
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function fmtCompact(n?: number | null): string {
  if (typeof n !== "number" || Number.isNaN(n)) return "—";
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(n);
}

export type Tone = "pos" | "neg" | "neutral";

export function toneOf(n?: number | null): Tone {
  if (typeof n !== "number" || Number.isNaN(n) || n === 0) return "neutral";
  return n > 0 ? "pos" : "neg";
}

export function toneClass(t: Tone): string {
  if (t === "pos") return "green";
  if (t === "neg") return "red";
  return "";
}

export function fmtDuration(seconds?: number | null): string {
  if (typeof seconds !== "number" || Number.isNaN(seconds)) return "—";
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return `${total}s`;
}

/** Compact date + time in the viewer's local timezone (honoring the
 *  Local/UTC time-display preference). Delegates to utils/dateTime so
 *  naive-UTC backend strings, unix s/ms, and invalid values are all
 *  handled in one place. Kept as "—" for null to preserve this helper's
 *  long-standing table-cell contract. */
export function fmtLocalDateTime(iso?: string | number | null): string {
  if (iso === null || iso === undefined) return "—";
  return formatLocalDateTime(iso);
}

/** Duration between two ISO timestamps, in the same clean minutes/hours
 *  format as fmtDuration. A null `closedIso` means the trade is still open. */
export function fmtTradeDuration(openedIso?: string | null, closedIso?: string | null): string {
  if (!openedIso) return "—";
  if (!closedIso) return "Open";
  const start = parseTimestamp(openedIso)?.getTime();
  const end = parseTimestamp(closedIso)?.getTime();
  if (start === undefined || end === undefined) return "—";
  return fmtDuration(Math.max(0, (end - start) / 1000));
}
