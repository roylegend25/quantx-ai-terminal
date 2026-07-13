import { useEffect, useState } from "react";
import { LogOut, StopCircle, Menu } from "lucide-react";
import { NAV_ITEMS, NAV_SECTIONS, NAV_ICONS, type NavKey } from "../../lib/nav";
import SafeBottomSheet from "./SafeBottomSheet";

const PRIMARY_KEYS: NavKey[] = ["dashboard", "portfolio", "paper-trading", "binance-real"];

type Props = {
  active: NavKey;
  onNavigate: (key: NavKey) => void;
  onStopBot: () => void;
  onLogout: () => void;
  isBotLive: boolean;
};

/** Phone-only (<=767px) Premium nav: Dashboard/Portfolio/Paper/Binance +
 * More (spec section 17), built on the shared SafeBottomSheet rather than
 * re-implementing sheet mechanics a third time. */
export default function PremiumBottomNav({ active, onNavigate, onStopBot, onLogout, isBotLive }: Props) {
  const [moreOpen, setMoreOpen] = useState(false);

  useEffect(() => {
    setMoreOpen(false);
  }, [active]);

  const moreActive = !PRIMARY_KEYS.includes(active);

  return (
    <div>
      <nav className="qp-bottom-nav safe-area-bottom" aria-label="Primary">
        {PRIMARY_KEYS.map((key) => {
          const item = NAV_ITEMS.find((i) => i.key === key);
          if (!item) return null;
          const Icon = NAV_ICONS[key];
          return (
            <button
              key={key}
              className={active === key ? "qp-bottom-nav-item active" : "qp-bottom-nav-item"}
              onClick={() => onNavigate(key)}
            >
              <Icon size={20} />
              <span>{item.label === "Paper Trading" ? "Paper" : item.label === "Binance Real" ? "Binance" : item.label}</span>
            </button>
          );
        })}
        <button
          className={moreActive ? "qp-bottom-nav-item active" : "qp-bottom-nav-item"}
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
        >
          <Menu size={20} />
          <span>More</span>
        </button>
      </nav>

      <SafeBottomSheet open={moreOpen} onClose={() => setMoreOpen(false)} title="More">
        <div className="qp-more-body">
          {NAV_SECTIONS.map((section) => {
            const keys = section.keys.filter((k) => !PRIMARY_KEYS.includes(k));
            if (keys.length === 0) return null;
            return (
              <div className="qp-more-section" key={section.label}>
                <span className="qp-nav-section-label">{section.label}</span>
                <div className="qp-more-grid">
                  {keys.map((key) => {
                    const item = NAV_ITEMS.find((i) => i.key === key);
                    if (!item) return null;
                    const Icon = NAV_ICONS[key];
                    return (
                      <button
                        key={key}
                        className={active === key ? "qp-more-item active" : "qp-more-item"}
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

          <div className="qp-more-section">
            <span className="qp-nav-section-label">Session</span>
            <div className="qp-more-grid">
              <button
                className="qp-more-item qp-more-item-danger"
                onClick={() => {
                  setMoreOpen(false);
                  onStopBot();
                }}
              >
                <StopCircle size={18} />
                <span>{isBotLive ? "Stop Bot" : "Bot Stopped"}</span>
              </button>
              <button
                className="qp-more-item"
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
      </SafeBottomSheet>
    </div>
  );
}
