import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import type { UseDesignSystemReturn } from "../../hooks/useDesignSystem";
import DesignSystemToggle from "../../components/Layout/DesignSystemToggle";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import SafeBottomSheet from "./SafeBottomSheet";

type Props = { designSystem: UseDesignSystemReturn };

/** Premium's own minimal appearance control - the mirror of Classic's
 * ThemeSwitcher, but scoped to the one decision that matters in Premium
 * mode: staying on Premium Dark, or dropping back to QuantX Classic (which
 * restores the last-selected accent instantly, since useTheme() keeps
 * running the whole time). Reuses DesignSystemToggle so there is exactly
 * one place that owns the classic<->premium persistence. */
export default function PremiumAppearanceMenu({ designSystem }: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const isMobile = useMediaQuery("(max-width: 640px)");

  useEffect(() => {
    if (!open || isMobile) return;
    function onClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open, isMobile]);

  return (
    <div className="qp-appearance-menu" ref={containerRef}>
      <button
        className="qp-icon-btn"
        onClick={() => setOpen((v) => !v)}
        title="Appearance"
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <Sparkles size={16} />
      </button>

      {isMobile ? (
        <SafeBottomSheet open={open} onClose={() => setOpen(false)} title="Appearance">
          <DesignSystemToggle designSystem={designSystem} />
        </SafeBottomSheet>
      ) : (
        open && (
          <div className="qp-appearance-popup" role="dialog" aria-label="Appearance settings">
            <p className="qp-appearance-popup-title">Design System</p>
            <DesignSystemToggle designSystem={designSystem} />
          </div>
        )
      )}
    </div>
  );
}
