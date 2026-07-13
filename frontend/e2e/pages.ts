import { NAV_ITEMS } from "../src/lib/nav";

/** Derived from the app's single source of truth for routes (lib/nav.ts) so
 * a page added there is automatically covered here - no separate list to
 * keep in sync. */
export const PAGES = NAV_ITEMS;

export const PRIMARY_MOBILE_LABELS: Record<string, string> = {
  "Paper Trading": "Paper",
  "Binance Real": "Binance",
};
