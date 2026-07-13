import { memo, useState } from "react";
import { LogOut, StopCircle, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { NAV_ITEMS, NAV_SECTIONS, NAV_ICONS, type NavKey } from "../../lib/nav";
import BrandLogo from "./BrandLogo";

type Props = {
  active: NavKey;
  onNavigate: (key: NavKey) => void;
  botStatus: any;
  dashboard: any;
  onStopBot: () => void;
  onLogout: () => void;
};

/** Tier-A: a genuinely new sidebar (own qp- classNames, own markup), not a
 * reskin of Classic's Sidebar.tsx. Collapsible for iPad landscape/portrait
 * (spec section 18) - collapsed state shows icon-only with native title
 * tooltips. */
function PremiumSidebar({ active, onNavigate, botStatus, dashboard, onStopBot, onLogout }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const status = (botStatus?.status || dashboard?.bot?.status || "loading").toUpperCase();
  const mode = (botStatus?.mode || dashboard?.mode || "paper").toUpperCase();
  const isLive = status === "RUNNING";

  return (
    <aside className={collapsed ? "qp-sidebar qp-sidebar-collapsed" : "qp-sidebar"}>
      <div className="qp-sidebar-brand">
        <BrandLogo brand="QUANTX" size="card" />
        {!collapsed && (
          <div>
            <h1>QuantX AI</h1>
            <p>Futures Terminal</p>
          </div>
        )}
      </div>

      <nav className="qp-nav-list">
        {NAV_SECTIONS.map((section) => (
          <div className="qp-nav-section" key={section.label}>
            {!collapsed && <span className="qp-nav-section-label">{section.label}</span>}
            {section.keys.map((key) => {
              const item = NAV_ITEMS.find((i) => i.key === key);
              if (!item) return null;
              const Icon = NAV_ICONS[key];
              return (
                <button
                  key={key}
                  className={active === key ? "qp-nav-item active" : "qp-nav-item"}
                  onClick={() => onNavigate(key)}
                  title={collapsed ? item.label : undefined}
                >
                  <Icon size={18} />
                  {!collapsed && <span>{item.label}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="qp-sidebar-status">
        {!collapsed && (
          <div className="qp-sidebar-status-head">
            <span className="qp-eyebrow">Bot Status</span>
            <span className={isLive ? "qp-status-badge qp-tone-positive" : "qp-status-badge qp-tone-neutral"}>{status}</span>
          </div>
        )}
        {!collapsed && (
          <div className="qp-sidebar-status-row">
            <span>Mode</span>
            <b>{mode}</b>
          </div>
        )}
        <div className="qp-sidebar-actions">
          <button className="qp-icon-btn qp-icon-btn-danger" onClick={onStopBot} title="Stop bot">
            <StopCircle size={16} />
            {!collapsed && <span>Stop Bot</span>}
          </button>
          <button className="qp-icon-btn" onClick={onLogout} title="Log out">
            <LogOut size={16} />
          </button>
        </div>
      </div>

      <button
        className="qp-sidebar-collapse-btn"
        onClick={() => setCollapsed((v) => !v)}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
      </button>
    </aside>
  );
}

export default memo(PremiumSidebar);
