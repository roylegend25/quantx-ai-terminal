import { useCallback, useEffect, useState } from "react";

const DESIGN_KEY = "quantx_design_system";

export type DesignSystemId = "classic" | "premium";

/** Never flip this without explicit sign-off - Premium Dark ships opt-in
 * until it's been used against real trading flows for a while. */
export const DEFAULT_DESIGN_SYSTEM: DesignSystemId = "classic";

function readStored(): DesignSystemId {
  if (typeof window === "undefined") return DEFAULT_DESIGN_SYSTEM;
  const v = window.localStorage.getItem(DESIGN_KEY);
  return v === "premium" || v === "classic" ? v : DEFAULT_DESIGN_SYSTEM;
}

/** Independent of useTheme()/data-theme: this hook only decides which shell
 * (Classic vs Premium) mounts and applies a separate `data-design` attribute
 * that premium/tokens.css and premium/overrides.css key off of. useTheme()
 * keeps running unconditionally so its accent selection survives switching
 * back to Classic. */
export function useDesignSystem() {
  const [mode, setModeState] = useState<DesignSystemId>(readStored);

  const setMode = useCallback((id: DesignSystemId) => {
    setModeState(id);
    window.localStorage.setItem(DESIGN_KEY, id);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-design", mode);
  }, [mode]);

  return { mode, setMode };
}

export type UseDesignSystemReturn = ReturnType<typeof useDesignSystem>;
