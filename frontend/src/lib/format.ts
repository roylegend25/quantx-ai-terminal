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
