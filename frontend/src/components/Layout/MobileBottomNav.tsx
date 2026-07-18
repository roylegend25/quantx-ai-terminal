import { useEffect, useRef, useState, type ComponentType } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion, useReducedMotion, type PanInfo } from "framer-motion";
import {
  LayoutDashboard,
  Brain,
  Rows3,
  ChartLine,
  Radio,
  Settings,
  ShieldAlert,
  FileText,
  FlaskConical,
  LogOut,
  StopCircle,
  Activity,
  Siren,
  Zap,
  Boxes,
  Microscope,
  Wallet,
  NotebookPen,
  CircleDollarSign,
  History,
  Menu,
  X,
  Stethoscope,
  Waves,
} from "lucide-react";
import { NAV_ITEMS, NAV_SECTIONS, type NavKey } from "../../lib/nav";

/** Phone-only (<=767px) replacement for the desktop/tablet Sidebar: a fixed
 *  bottom tab bar with the 4 most-used pages plus a "More" bottom sheet for
 *  everything else, grouped the same way Sidebar groups NAV_SECTIONS.
 *
 *  Sheet mechanics (drag-to-dismiss, scroll-lock, backdrop) intentionally
 *  mirror NotificationBell.tsx - see that file for the reference pattern -
 *  and reuse its ".notif-backdrop/.notif-sheet/.notif-sheet-handle" CSS so
 *  the two sheets look and feel identical instead of drifting apart. */

const ICONS: Record<NavKey, ComponentType<{ size?: number }>> = {
  dashboard: LayoutDashboard,
  portfolio: Wallet,
  "paper-trading": NotebookPen,
  "binance-real": CircleDollarSign,
  "bot-trades": History,
  predictions: Brain,
  positions: Rows3,
  performance: ChartLine,
  market: Radio,
  "bot-settings": Settings,
  risk: ShieldAlert,
  logs: FileText,
  backtesting: FlaskConical,
  "system-status": Activity,
  "stress-test": Siren,
  execution: Zap,
  "model-center": Boxes,
  "research-lab": Microscope,
  "trading-diagnostics": Stethoscope,
  hyperliquid: Waves,
};

const PRIMARY_KEYS: NavKey[] = ["dashboard", "portfolio", "paper-trading", "binance-real"];

const DISMISS_OFFSET_PX = 120;
const DISMISS_VELOCITY = 800;

type Props = {
  active: NavKey;
  onNavigate: (key: NavKey) => void;
  onStopBot: () => void;
  onLogout: () => void;
  isBotLive: boolean;
};

export default function MobileBottomNav({ active, onNavigate, onStopBot, onLogout, isBotLive }: Props) {
  const [moreOpen, setMoreOpen] = useState(false);
  const prefersReducedMotion = useReducedMotion();
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setMoreOpen(false);
  }, [active]);

  useEffect(() => {
    if (!moreOpen) return;
    document.body.classList.add("mobilenav-scroll-lock");
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setMoreOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("mobilenav-scroll-lock");
      window.removeEventListener("keydown", onKey);
    };
  }, [moreOpen]);

  function handleDragEnd(_: unknown, info: PanInfo) {
    if (info.offset.y > DISMISS_OFFSET_PX || info.velocity.y > DISMISS_VELOCITY) setMoreOpen(false);
  }

  const moreActive = !PRIMARY_KEYS.includes(active);
  const spring = prefersReducedMotion ? { duration: 0.01 } : { type: "spring" as const, stiffness: 380, damping: 34 };

  return (
    <div ref={rootRef}>
      <nav className="mobile-bottom-nav safe-area-bottom" aria-label="Primary">
        {PRIMARY_KEYS.map((key) => {
          const item = NAV_ITEMS.find((i) => i.key === key);
          if (!item) return null;
          const Icon = ICONS[key];
          return (
            <button
              key={key}
              className={active === key ? "mobile-bottom-nav-item active" : "mobile-bottom-nav-item"}
              onClick={() => onNavigate(key)}
            >
              <Icon size={20} />
              <span>{item.label === "Paper Trading" ? "Paper" : item.label === "Binance Real" ? "Binance" : item.label}</span>
            </button>
          );
        })}
        <button
          className={moreActive ? "mobile-bottom-nav-item active" : "mobile-bottom-nav-item"}
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
        >
          <Menu size={20} />
          <span>More</span>
        </button>
      </nav>

      {createPortal(
        <AnimatePresence>
          {moreOpen && (
            <>
              <motion.div
                className="notif-backdrop"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: prefersReducedMotion ? 0.01 : 0.18 }}
                onClick={() => setMoreOpen(false)}
              />
              <motion.div
                className="notif-sheet mobile-more-sheet"
                role="dialog"
                aria-modal
                aria-label="More navigation"
                initial={{ y: "100%" }}
                animate={{ y: 0 }}
                exit={{ y: "100%" }}
                transition={spring}
                drag="y"
                dragConstraints={{ top: 0, bottom: 0 }}
                dragElastic={{ top: 0, bottom: 0.5 }}
                onDragEnd={handleDragEnd}
              >
                <span className="notif-sheet-handle" aria-hidden="true" />

                <div className="notif-panel-head">
                  <div className="notif-panel-title">
                    <b>More</b>
                  </div>
                  <button className="icon-btn notif-close" onClick={() => setMoreOpen(false)} title="Close" aria-label="Close">
                    <X size={18} />
                  </button>
                </div>

                <div className="notif-panel-body mobile-more-body">
                  {NAV_SECTIONS.map((section) => {
                    const keys = section.keys.filter((k) => !PRIMARY_KEYS.includes(k));
                    if (keys.length === 0) return null;
                    return (
                      <div className="mobile-more-section" key={section.label}>
                        <span className="nav-section-label">{section.label}</span>
                        <div className="mobile-more-grid">
                          {keys.map((key) => {
                            const item = NAV_ITEMS.find((i) => i.key === key);
                            if (!item) return null;
                            const Icon = ICONS[key];
                            return (
                              <button
                                key={key}
                                className={active === key ? "mobile-more-item active" : "mobile-more-item"}
                                onClick={() => onNavigate(key)}
                              >
                                <Icon size={18} />
                                <span>{item.label}</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}

                  <div className="mobile-more-section">
                    <span className="nav-section-label">Session</span>
                    <div className="mobile-more-grid">
                      <button
                        className="mobile-more-item danger"
                        onClick={() => {
                          setMoreOpen(false);
                          onStopBot();
                        }}
                      >
                        <StopCircle size={18} />
                        <span>{isBotLive ? "Stop Bot" : "Bot Stopped"}</span>
                      </button>
                      <button
                        className="mobile-more-item"
                        onClick={() => {
                          setMoreOpen(false);
                          onLogout();
                        }}
                      >
                        <LogOut size={18} />
                        <span>Log Out</span>
                      </button>
                    </div>
                  </div>
                </div>
              </motion.div>
            </>
          )}
        </AnimatePresence>,
        document.body
      )}
    </div>
  );
}
