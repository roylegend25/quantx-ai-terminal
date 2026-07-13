import type { ReactNode } from "react";

export type StatusTone = "positive" | "negative" | "warning" | "accent" | "neutral";

type Props = {
  tone?: StatusTone;
  children: ReactNode;
  dot?: boolean;
  className?: string;
};

/** Status is never color-only: the label text always carries the meaning
 * (PROTECTED / MISSING TP / RUNNING / etc.) so this remains legible without
 * relying on hue perception. */
export default function StatusBadge({ tone = "neutral", children, dot = false, className }: Props) {
  return (
    <span className={`qp-status-badge qp-tone-${tone}${className ? ` ${className}` : ""}`}>
      {dot && <span className="qp-status-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
