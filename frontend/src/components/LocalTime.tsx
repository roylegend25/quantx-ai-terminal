import { memo } from "react";
import { useMediaQuery } from "../hooks/useMediaQuery";
import {
  formatCompactLocalDateTime,
  formatFullLocalDateTime,
  formatLocalDateTime,
  formatUtcDateTime,
  parseTimestamp,
  useTimeDisplayMode,
} from "../utils/dateTime";

type Props = {
  /** Backend timestamp: ISO (with or without offset), unix s/ms, or null. */
  value: unknown;
  /**
   * "auto" (default) renders the desktop format and drops the year on
   * small screens; "compact" always drops the year (tight table cells,
   * dropdowns); "full" always shows the year.
   */
  variant?: "auto" | "compact" | "full";
  /** Accessible context, e.g. "Created" -> "Created 7 July 2026 at ..." */
  label?: string;
};

/**
 * One timestamp, rendered in the viewer's browser timezone (or UTC when the
 * time-display preference says so - the hook re-renders on toggle, no
 * reload). Semantic <time> element; tooltip carries the full local time
 * with zone name plus the original UTC instant. Null -> "Not available",
 * unparseable -> "—", never "Invalid Date".
 */
function LocalTime({ value, variant = "auto", label }: Props) {
  const mode = useTimeDisplayMode();
  const isMobile = useMediaQuery("(max-width: 640px)");

  if (value === null || value === undefined || value === "") {
    return <span className="mll-dim">Not available</span>;
  }
  const d = parseTimestamp(value);
  if (!d) return <>—</>;

  const compact = variant === "compact" || (variant === "auto" && isMobile);
  const text = compact ? formatCompactLocalDateTime(value, mode) : formatLocalDateTime(value, mode);
  const full = formatFullLocalDateTime(value, mode);
  const utc = formatUtcDateTime(value);
  const title = `${full}\nUTC: ${utc}`;
  const ariaLabel = `${label ? `${label} ` : ""}${full}`;

  return (
    <time dateTime={d.toISOString()} title={title} aria-label={ariaLabel}>
      {text}
    </time>
  );
}

export default memo(LocalTime);
