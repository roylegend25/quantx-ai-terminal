export type NavKey =
  | "dashboard"
  | "predictions"
  | "positions"
  | "performance"
  | "market"
  | "bot-settings"
  | "risk"
  | "logs"
  | "backtesting";

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
];
