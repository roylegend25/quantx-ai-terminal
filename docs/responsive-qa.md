# Responsive QA Checklist

Manual checklist for verifying the mobile/tablet/desktop responsive overhaul. No automated Playwright
suite exists in this repo (see note at the bottom) — run this by hand with `npm run dev` and browser
devtools' responsive mode (or a real device) at each viewport below.

## Viewports to test

| Label | Size | Represents |
|---|---|---|
| Small phone | 360×780 | Compact Android |
| iPhone 12/13/14 | 390×844 | Common iPhone |
| iPhone 17 Pro Max | 430×932 | Large iPhone |
| Large phone | 440×956 | Largest common phone |
| iPad portrait | 768×1024 | iPad (classic) |
| iPad Pro 11" portrait | 834×1194 | iPad Pro M4 portrait |
| iPad Pro 13" landscape | 1024×1366 (rotate) | iPad Pro M4 landscape |
| iPad landscape | 1194×834 | iPad landscape |
| Small desktop | 1366×1024 | Laptop |
| Desktop | 1440×900 | Standard desktop |

## Global checks (every page, every viewport)

- [ ] No horizontal page scroll (check `document.body.scrollWidth <= window.innerWidth` or just try
      swiping sideways on the page background)
- [ ] At ≤767px: bottom nav bar is visible, doesn't overlap the last card on the page, and shows 5 items
      (Dashboard / Portfolio / Paper / Binance / More)
- [ ] At 768–1024px and 1025–1366px: sidebar renders as a sticky top pill-strip, not the phone bottom nav
- [ ] At ≥1025px (outside the iPad landscape range) and on true desktop widths: full left sidebar renders
- [ ] Every table on the page is either a horizontally-scrollable `.data-table` (desktop/tablet) or stacked
      cards (≤767px) — no table clips text without any way to reach it
- [ ] Modals (Edit Risk, Live Unlock, Server Trading Control's Enable Lock/Risk Limits/Confirm, reset
      confirmations) open as centered dialogs at ≥641px and as bottom sheets at ≤640px, with a visible
      close affordance and safe-area bottom padding on iOS
- [ ] No text is clipped or overlapping — check P&L figures, symbols, badges, and long messages/reasons
- [ ] Buttons/touch targets are at least ~44px tall on phone

## Per-page checks

- [ ] **Dashboard** — mode/status cards stack cleanly on phone; Open Positions and Recent Trades render as
      cards below 767px; AI Prediction chart resizes and its timeframe pills scroll horizontally
- [ ] **Portfolio** — Paper/Binance panels stack sequentially on phone (not side by side); each panel's
      mini position table renders as cards on phone
- [ ] **Paper Trading** — Open Positions and Trade History render as cards on phone with visible
      TP/SL + Close actions; Reset Paper Account modal is a bottom sheet on phone
- [ ] **Binance Real** — all 5 tables (Balances, Positions, Orders, Income, Trade History) render as cards
      on phone; disabled/locked action states are visible (not just cut off)
- [ ] **Bot Trades** — Paper and Binance journals render as cards with a working expand/detail toggle for
      the secondary fields (TP/SL, engine, confidence, reason, order IDs)
- [ ] **AI Model Center** — the 10-item tab bar scrolls horizontally as pills on phone instead of wrapping
      to several rows; Model Registry renders as cards with working Download/Rollback/Archive actions;
      the Champion-vs-Challengers comparison table intentionally stays a horizontal-scroll table (it's a
      metrics × models matrix, not one-row-per-entity, so it doesn't convert to cards)
- [ ] **Research Lab** — all data/job/backtest/walk-forward/Monte Carlo tables render as cards on phone;
      charts stay full width and readable
- [ ] **Backtesting** — the three-panel row (Config / Chart / KPIs) reflows to Chart → Config → KPIs on
      phone and tablet portrait; fullscreen chart mode fills the viewport correctly and stays correct
      after rotating the device; Trade History table renders as cards with pagination controls intact
- [ ] **Positions** — Trade Journal renders as cards with the expandable decision-detail panel working
- [ ] **Risk / Bot Settings / Market / Predictions / Performance / Execution / System Status / Logs** — no
      horizontal overflow, tables (where present) render as cards on phone, form controls stack full-width
- [ ] **Login** — form is usable and centered at every width, no overflow

## Notes

- Playwright is not installed in this repo, so there is no automated responsive smoke-test suite — this
  checklist is the manual substitute. If Playwright is added later, port these checks into smoke tests for
  Dashboard, Portfolio, Paper Trading, Binance Real, Bot Trades, AI Model Center, and Research Lab per the
  original spec.
- `/signup`, `/billing`, `/account`, and a dedicated `/admin` page do not exist in this app (no router —
  see `frontend/src/App.tsx` / `frontend/src/lib/nav.ts` for the real 18 pages). Server Trading Control
  ("admin") lives on the Dashboard, not a separate route — check it there instead.
