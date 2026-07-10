import type { ReactNode } from "react";
import { motion } from "framer-motion";

/** Shared presentational atoms for the AI Model Lab. Every "empty" state
 *  must carry the real reason it is empty — no blank boxes. */

export function Skeleton({ h = 16, w = "100%", style }: { h?: number; w?: number | string; style?: React.CSSProperties }) {
  return <span className="mll-skeleton" style={{ height: h, width: w, ...style }} />;
}

export function SkeletonTiles({ count = 6 }: { count?: number }) {
  return (
    <div className="mll-kpi-grid">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="mll-kpi">
          <Skeleton h={10} w="55%" />
          <Skeleton h={20} w="70%" style={{ marginTop: 8 }} />
        </div>
      ))}
    </div>
  );
}

export function EmptySlate({ title, reason }: { title: string; reason: string }) {
  return (
    <div className="mll-empty">
      <b>{title}</b>
      <p>{reason}</p>
    </div>
  );
}

export function KpiTile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "green" | "red" | "yellow" | "cyan" | null;
}) {
  return (
    <motion.div
      className="mll-kpi"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
    >
      <span className="tile-label">{label}</span>
      <b className={`mll-kpi-value ${tone || ""}`}>{value}</b>
      {sub ? <span className="mll-kpi-sub">{sub}</span> : null}
    </motion.div>
  );
}

export function StatusPill({ status }: { status: string | null | undefined }) {
  const s = (status || "").toLowerCase();
  const cls =
    s === "healthy" || s === "succeeded" || s === "champion" || s === "correct"
      ? "green"
      : s === "warning" || s === "queued" || s === "pending" || s === "testing"
      ? "yellow"
      : s === "critical" || s === "failed" || s === "incorrect"
      ? "red"
      : s === "running" || s === "challenger"
      ? "cyan"
      : "";
  return <span className={`mll-pill ${cls}`}>{status || "—"}</span>;
}

export function ProgressBar({ pct, tone }: { pct: number; tone?: "green" | "cyan" }) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="mll-progress">
      <div className={`mll-progress-fill ${tone || "cyan"}`} style={{ width: `${clamped}%` }} />
    </div>
  );
}

export function fmtBytes(n?: number | null): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} KB`;
  return `${n} B`;
}

export function fmtDurationS(s?: number | null): string {
  if (typeof s !== "number" || !Number.isFinite(s)) return "—";
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${(s - m * 60).toFixed(0)}s`;
}
