import { memo, useEffect, useState, type ComponentType } from "react";
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
  Bot,
  Activity,
  Siren,
  Zap,
  Boxes,
  Microscope,
  Wallet,
  NotebookPen,
  CircleDollarSign,
  History,
  Stethoscope,
} from "lucide-react";
import { NAV_ITEMS, NAV_SECTIONS, type NavKey } from "../../lib/nav";

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
};

type Props = {
  active: NavKey;
  onNavigate: (key: NavKey) => void;
  botStatus: any;
  dashboard: any;
  onStopBot: () => void;
  onLogout: () => void;
};

const SESSION_START = Date.now();

function formatUptime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function Sidebar({ active, onNavigate, botStatus, dashboard, onStopBot, onLogout }: Props) {
  const status = (botStatus?.status || dashboard?.bot?.status || "loading").toUpperCase();
  const mode = (botStatus?.mode || dashboard?.mode || "paper").toUpperCase();
  const isLive = status === "RUNNING";

  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-icon">QX</div>
        <div>
          <h1>QuantX AI</h1>
          <p>Futures Terminal</p>
        </div>
      </div>

      <nav className="nav-list">
        {NAV_SECTIONS.map((section) => (
          <div className="nav-section" key={section.label}>
            <span className="nav-section-label">{section.label}</span>
            {section.keys.map((key) => {
              const item = NAV_ITEMS.find((i) => i.key === key);
              if (!item) return null;
              const Icon = ICONS[key];
              return (
                <button
                  key={key}
                  className={active === key ? "nav active" : "nav"}
                  onClick={() => onNavigate(key)}
                >
                  <Icon size={18} />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="status-box">
        <div className="status-box-head">
          <span className="eyebrow">Bot Status</span>
          <span className={`badge ${isLive ? "badge-green" : ""}`}>{status}</span>
        </div>

        <div className={`bot-orb ${isLive ? "live" : "idle"}`}>
          <Bot size={26} />
        </div>

        <div className="status-stats">
          <div>
            <span>Session</span>
            <b>{formatUptime(now - SESSION_START)}</b>
          </div>
          <div className="align-right">
            <span>Mode</span>
            <b>{mode}</b>
          </div>
        </div>

        <div className="status-box-actions">
          <button className="icon-btn danger" onClick={onStopBot} title="Stop bot">
            <StopCircle size={16} /> Stop Bot
          </button>
          <button className="icon-btn" onClick={onLogout} title="Log out">
            <LogOut size={16} />
          </button>
        </div>
      </div>
    </aside>
  );
}

export default memo(Sidebar);
