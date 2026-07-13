import { memo, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Check, Sparkles, X } from "lucide-react";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import type { UseThemeReturn } from "../../hooks/useTheme";
import type { UseDesignSystemReturn } from "../../hooks/useDesignSystem";
import { CONTEXT_PRESETS, ROTATION_OPTIONS, THEMES, THEME_MAP, type ThemeId } from "../../lib/themes";
import DesignSystemToggle from "./DesignSystemToggle";

type Props = { theme: UseThemeReturn; designSystem: UseDesignSystemReturn };

function ThemeSwitcher({ theme, designSystem }: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const isMobile = useMediaQuery("(max-width: 640px)");
  const prefersReducedMotion = useReducedMotion();

  const { themeId, setThemeId, rotation, setRotation, recommended } = theme;
  const current = THEME_MAP[themeId];
  const recommendedDef = THEME_MAP[recommended.theme];

  useEffect(() => {
    if (!open) return;

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  function applyTheme(id: ThemeId) {
    setThemeId(id);
  }

  const spring = prefersReducedMotion
    ? { duration: 0.01 }
    : { type: "spring" as const, stiffness: 380, damping: 32 };

  return (
    <div className="theme-switcher" ref={containerRef}>
      <button
        className="theme-trigger"
        style={{ ["--trigger-a" as any]: current.colors[0], ["--trigger-b" as any]: current.colors[1] }}
        onClick={() => setOpen((v) => !v)}
        title="Appearance"
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <current.icon size={16} />
      </button>

      <AnimatePresence>
        {open && (
          <>
            {isMobile && (
              <motion.div
                className="theme-sheet-backdrop"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: prefersReducedMotion ? 0.01 : 0.18 }}
                onClick={() => setOpen(false)}
              />
            )}

            <motion.div
              className={isMobile ? "theme-sheet" : "theme-popup"}
              role="dialog"
              aria-label="Appearance settings"
              initial={isMobile ? { y: "100%" } : { opacity: 0, scale: 0.94, y: -8 }}
              animate={isMobile ? { y: 0 } : { opacity: 1, scale: 1, y: 0 }}
              exit={isMobile ? { y: "100%" } : { opacity: 0, scale: 0.96, y: -6 }}
              transition={spring}
            >
              <div className="theme-panel-header">
                <div>
                  <b>Appearance</b>
                  <p>Pick a look, or let it adapt automatically.</p>
                </div>
                <button className="icon-btn" onClick={() => setOpen(false)} title="Close" aria-label="Close">
                  <X size={16} />
                </button>
              </div>

              <div className="theme-panel-body">
                <section className="theme-section">
                  <span className="theme-section-title">Design System</span>
                  <DesignSystemToggle designSystem={designSystem} />
                </section>

                <section className="theme-section">
                  <span className="theme-section-title">Recommended For You</span>
                  <button
                    className={`theme-reco-card ${themeId === recommended.theme ? "active" : ""}`}
                    onClick={() => applyTheme(recommended.theme)}
                  >
                    <span className="theme-reco-badge">
                      <Sparkles size={11} /> Recommended
                    </span>
                    <span className="theme-reco-main">
                      <span
                        className="theme-swatch"
                        style={{ background: `linear-gradient(135deg, ${recommendedDef.colors[0]}, ${recommendedDef.colors[1]})` }}
                      >
                        <recommendedDef.icon size={16} />
                      </span>
                      <span className="theme-reco-copy">
                        <b>{recommendedDef.name}</b>
                        <span>{recommended.reason}</span>
                      </span>
                      {themeId === recommended.theme ? (
                        <Check size={16} className="green" />
                      ) : (
                        <span className="theme-apply-pill">Apply ✨</span>
                      )}
                    </span>
                  </button>
                </section>

                <section className="theme-section">
                  <span className="theme-section-title">Themes For You</span>
                  <div className="theme-context-list">
                    {CONTEXT_PRESETS.map((preset) => {
                      const def = THEME_MAP[preset.theme];
                      const active = themeId === preset.theme;
                      return (
                        <button
                          key={preset.id}
                          className={`theme-context-card ${active ? "active" : ""}`}
                          onClick={() => applyTheme(preset.theme)}
                          aria-pressed={active}
                        >
                          <span className="theme-context-icon" style={{ color: def.colors[0] }}>
                            <preset.icon size={16} />
                          </span>
                          <span className="theme-context-copy">
                            <b>{preset.name}</b>
                            <span>{preset.description}</span>
                          </span>
                          <span className={`theme-radio ${active ? "active" : ""}`}>{active && <Check size={12} />}</span>
                        </button>
                      );
                    })}
                  </div>
                </section>

                <section className="theme-section">
                  <span className="theme-section-title">All Themes</span>
                  <div className="theme-grid">
                    {THEMES.map((t) => {
                      const active = themeId === t.id;
                      return (
                        <button
                          key={t.id}
                          className={`theme-card ${active ? "active" : ""}`}
                          onClick={() => applyTheme(t.id)}
                          aria-pressed={active}
                          title={t.tagline}
                        >
                          <span
                            className="theme-card-preview"
                            style={{ background: `linear-gradient(135deg, ${t.colors[0]}, ${t.colors[1]} 55%, ${t.colors[2]})` }}
                          >
                            {active && (
                              <span className="theme-card-check">
                                <Check size={13} />
                              </span>
                            )}
                          </span>
                          <span className="theme-card-name">
                            <span className="theme-card-dot" style={{ background: t.colors[0] }} />
                            {t.name}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </section>

                <section className="theme-section">
                  <div className="theme-rotation-head">
                    <span className="theme-section-title">Automatic Rotation</span>
                    {rotation !== "off" && <span className="theme-live-badge">● Live</span>}
                  </div>
                  <div className="theme-rotation-options">
                    {ROTATION_OPTIONS.map((opt) => (
                      <button
                        key={opt.id}
                        className={rotation === opt.id ? "tf-btn active" : "tf-btn"}
                        onClick={() => setRotation(opt.id)}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </section>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

export default memo(ThemeSwitcher);
