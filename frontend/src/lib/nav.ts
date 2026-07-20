import type { ComponentType } from "react";
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
  Target,
} from "lucide-react";

export type NavKey =
  | "dashboard"
  | "portfolio"
  | "paper-trading"
  | "binance-real"
  | "bot-trades"
  | "predictions"
  | "prediction-results"
  | "positions"
  | "performance"
  | "market"
  | "bot-settings"
  | "risk"
  | "logs"
  | "backtesting"
  | "system-status"
  | "stress-test"
  | "execution"
  | "model-center"
  | "research-lab"
  | "trading-diagnostics"
  | "hyperliquid";

export const NAV_ITEMS: { key: NavKey; label: string }[] = [
  { key: "dashboard", label: "Dashboard" },
  { key: "portfolio", label: "Portfolio" },
  { key: "paper-trading", label: "Paper Trading" },
  { key: "binance-real", label: "Binance Real" },
  { key: "bot-trades", label: "Bot Trades" },
  { key: "predictions", label: "Predictions" },
  { key: "prediction-results", label: "Prediction Results" },
  { key: "positions", label: "Positions" },
  { key: "performance", label: "Performance" },
  { key: "market", label: "Market Analysis" },
  { key: "bot-settings", label: "Bot Settings" },
  { key: "risk", label: "Risk Management" },
  { key: "logs", label: "Logs" },
  { key: "backtesting", label: "Backtesting" },
  { key: "system-status", label: "System Status" },
  { key: "stress-test", label: "Stress Test" },
  { key: "execution", label: "Execution" },
  { key: "model-center", label: "AI Model Center" },
  { key: "research-lab", label: "Research Lab" },
  { key: "trading-diagnostics", label: "Trading Diagnostics" },
  { key: "hyperliquid", label: "Hyperliquid Large Trades" },
];

/** Purely a sidebar rendering grouping - NAV_ITEMS above stays the flat
 * source of truth (labels, icon lookups) for anything else that needs it. */
export const NAV_SECTIONS: { label: string; keys: NavKey[] }[] = [
  { label: "Main", keys: ["dashboard", "portfolio", "predictions", "prediction-results", "positions", "performance"] },
  { label: "Trading", keys: ["paper-trading", "binance-real", "bot-trades", "market", "bot-settings", "risk", "execution", "trading-diagnostics"] },
  { label: "Analysis", keys: ["backtesting", "model-center", "research-lab", "stress-test", "hyperliquid"] },
  { label: "System", keys: ["logs", "system-status"] },
];

/** Single source of truth for the NavKey -> icon mapping. Sidebar.tsx and
 * MobileBottomNav.tsx historically each kept their own byte-identical copy
 * of this map; new (Premium) nav surfaces should import this instead of
 * adding a third copy. Classic's two existing copies are left as-is to
 * keep this an additive change. */
export const NAV_ICONS: Record<NavKey, ComponentType<{ size?: number }>> = {
  dashboard: LayoutDashboard,
  portfolio: Wallet,
  "paper-trading": NotebookPen,
  "binance-real": CircleDollarSign,
  "bot-trades": History,
  predictions: Brain,
  "prediction-results": Target,
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
