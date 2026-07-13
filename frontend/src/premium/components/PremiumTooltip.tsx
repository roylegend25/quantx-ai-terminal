import { useId, useState, type ReactNode } from "react";
import { Info } from "lucide-react";

type Props = {
  label: string;
  children?: ReactNode;
};

/** Small icon-triggered tooltip for chart/metric info affordances (e.g. the
 * "i" next to "BTCUSDT Prediction Chart"). Keyboard-accessible: focus shows
 * the tooltip the same as hover, Escape/blur hides it. */
export default function PremiumTooltip({ label, children }: Props) {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <span
      className="qp-tooltip-wrap"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <button
        type="button"
        className="qp-tooltip-trigger"
        aria-describedby={id}
        aria-label={label}
        onClick={() => setOpen((v) => !v)}
      >
        {children || <Info size={13} />}
      </button>
      {open && (
        <span role="tooltip" id={id} className="qp-tooltip-bubble">
          {label}
        </span>
      )}
    </span>
  );
}
