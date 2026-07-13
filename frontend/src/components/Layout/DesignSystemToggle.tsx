import { Check, Layers, Sparkles } from "lucide-react";
import type { UseDesignSystemReturn } from "../../hooks/useDesignSystem";

type Props = {
  designSystem: UseDesignSystemReturn;
  /** Compact rendering for the Premium topbar's own appearance menu, vs the
   * fuller card treatment inside Classic's ThemeSwitcher popup. */
  compact?: boolean;
};

/** The one shared control that flips data-design between "classic" and
 * "premium". Used from both ThemeSwitcher.tsx (Classic mode) and
 * PremiumAppearanceMenu.tsx (Premium mode) so there is exactly one place
 * that owns this persistence, per the plan. */
export default function DesignSystemToggle({ designSystem, compact = false }: Props) {
  const { mode, setMode } = designSystem;

  return (
    <div className={compact ? "design-toggle design-toggle-compact" : "design-toggle"}>
      <button
        type="button"
        className={mode === "classic" ? "design-toggle-opt active" : "design-toggle-opt"}
        onClick={() => setMode("classic")}
        aria-pressed={mode === "classic"}
      >
        <Layers size={15} />
        <span>QuantX Classic</span>
        {mode === "classic" && <Check size={13} className="design-toggle-check" />}
      </button>
      <button
        type="button"
        className={mode === "premium" ? "design-toggle-opt active" : "design-toggle-opt"}
        onClick={() => setMode("premium")}
        aria-pressed={mode === "premium"}
      >
        <Sparkles size={15} />
        <span>Premium Dark</span>
        {mode === "premium" && <Check size={13} className="design-toggle-check" />}
      </button>
    </div>
  );
}
