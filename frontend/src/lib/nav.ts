export type NavKey =
  | "dashboard"
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
