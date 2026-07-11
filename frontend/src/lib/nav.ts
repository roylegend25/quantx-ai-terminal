export type NavKey =
  | "dashboard"
  | "portfolio"
  | "paper-trading"
  | "binance-real"
  | "bot-trades"
  | "predictions"
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
  | "research-lab";

export const NAV_ITEMS: { key: NavKey; label: string }[] = [
  { key: "dashboard", label: "Dashboard" },
  { key: "portfolio", label: "Portfolio" },
  { key: "paper-trading", label: "Paper Trading" },
  { key: "binance-real", label: "Binance Real" },
  { key: "bot-trades", label: "Bot Trades" },
  { key: "predictions", label: "Predictions" },
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
];

/** Purely a sidebar rendering grouping - NAV_ITEMS above stays the flat
 * source of truth (labels, icon lookups) for anything else that needs it. */
export const NAV_SECTIONS: { label: string; keys: NavKey[] }[] = [
  { label: "Main", keys: ["dashboard", "portfolio", "predictions", "positions", "performance"] },
  { label: "Trading", keys: ["paper-trading", "binance-real", "bot-trades", "market", "bot-settings", "risk", "execution"] },
  { label: "Analysis", keys: ["backtesting", "model-center", "research-lab", "stress-test"] },
  { label: "System", keys: ["logs", "system-status"] },
];
