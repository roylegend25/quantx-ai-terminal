export type Portfolio = {
  balance?: number;
  equity?: number;
  unrealized_pnl?: number;
  daily_pnl?: number;
  total_pnl?: number;
  win_rate?: number;
  wins?: number;
  losses?: number;
  open_positions?: number;
  sharpe_ratio?: number;
  max_open_positions?: number;
  total_margin_used?: number;
  available_margin?: number;
  margin_usage_pct?: number | null;
  total_notional_exposure?: number;
  nearest_liquidation_distance_pct?: number | null;
};

export type Position = {
  id: number;
  symbol: string;
  side: string;
  entry: number;
  mark: number;
  qty: number;
  pnl: number;
  // leverage/margin/risk fields - null on positions opened before leverage
  // tracking existed (field_notes says why)
  sl?: number | null;
  tp?: number | null;
  leverage?: number | null;
  margin_mode?: string | null;
  margin_used?: number | null;
  notional_value?: number | null;
  maintenance_margin?: number | null;
  liquidation_price?: number | null;
  distance_to_liquidation_pct?: number | null;
  risk_reward_ratio?: number | null;
  unrealized_pnl_pct?: number | null;
  trailing_stop?: number | null;
  field_notes?: Record<string, string> | null;
};

export type Trade = {
  id: number;
  symbol: string;
  side: string;
  status: string;
  pnl?: number;
  entry?: number | null;
  exit?: number | null;
  opened_at?: string | null;
  closed_at?: string | null;
};

export type EquityPoint = { label: string; equity: number };
export type DrawdownPoint = { label: string; drawdown: number };
export type DailyPnlPoint = { day: string; pnl: number };

export function closedTradesOf(history: Trade[]): (Trade & { closed_at: string; pnl: number })[] {
  return (history || [])
    .filter((t): t is Trade & { closed_at: string; pnl: number } =>
      t.status === "CLOSED" && !!t.closed_at && typeof t.pnl === "number"
    )
    .slice()
    .sort((a, b) => new Date(a.closed_at).getTime() - new Date(b.closed_at).getTime());
}

export function initialBalanceOf(portfolio: Portfolio | null): number {
  if (portfolio && typeof portfolio.balance === "number" && typeof portfolio.total_pnl === "number") {
    const base = portfolio.balance - portfolio.total_pnl;
    return base > 0 ? base : portfolio.balance;
  }
  return 10000;
}

export function equityCurveOf(portfolio: Portfolio | null, history: Trade[]): EquityPoint[] {
  const trades = closedTradesOf(history);
  const initialBalance = initialBalanceOf(portfolio);
  let running = initialBalance;
  const points: EquityPoint[] = [{ label: "Start", equity: running }];

  trades.forEach((t) => {
    running += t.pnl;
    points.push({
      label: new Date(t.closed_at).toLocaleDateString([], { month: "short", day: "numeric" }),
      equity: Math.round(running * 100) / 100,
    });
  });

  const current = typeof portfolio?.equity === "number" ? portfolio.equity : running;
  points.push({ label: "Now", equity: Math.round(current * 100) / 100 });

  return points;
}

export function drawdownCurveOf(equityCurve: EquityPoint[]): DrawdownPoint[] {
  let peak = -Infinity;
  return equityCurve.map((p) => {
    peak = Math.max(peak, p.equity);
    const dd = peak > 0 ? ((p.equity - peak) / peak) * 100 : 0;
    return { label: p.label, drawdown: Math.round(dd * 100) / 100 };
  });
}

export function maxDrawdownOf(drawdownCurve: DrawdownPoint[]): number {
  return drawdownCurve.reduce((min, p) => Math.min(min, p.drawdown), 0);
}

export function dailyPnlOf(history: Trade[]): DailyPnlPoint[] {
  const trades = closedTradesOf(history);
  const byDay = new Map<string, number>();
  trades.forEach((t) => {
    const day = t.closed_at.slice(0, 10);
    byDay.set(day, (byDay.get(day) || 0) + t.pnl);
  });
  return Array.from(byDay.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, pnl]) => ({ day: day.slice(5), pnl: Math.round(pnl * 100) / 100 }));
}

/** Historical VaR(95%): the loss threshold that 95% of closed-trade PnLs do not exceed. */
export function historicalVaR95(history: Trade[]): number | null {
  const trades = closedTradesOf(history);
  if (trades.length < 5) return null;
  const pnls = trades.map((t) => t.pnl).sort((a, b) => a - b);
  const idx = Math.floor(pnls.length * 0.05);
  const value = pnls[idx];
  return value < 0 ? Math.abs(value) : 0;
}

/** Margin usage %. Prefers the server-computed figure (real per-position
 *  margin incl. leverage); falls back to the old notional/equity estimate
 *  for responses that predate it. */
export function marginUsagePct(positions: Position[], portfolio: Portfolio | null): number {
  if (typeof portfolio?.margin_usage_pct === "number") {
    return Math.max(0, Math.min(100, portfolio.margin_usage_pct));
  }
  const equity = portfolio?.equity;
  if (!equity || equity <= 0 || !positions.length) return 0;
  const notional = positions.reduce((sum, p) => sum + Math.abs((p.mark || p.entry || 0) * p.qty), 0);
  return Math.max(0, Math.min(100, (notional / equity) * 100));
}

export function riskLevelOf(marginPct: number): { label: string; tone: "green" | "yellow" | "red" } {
  if (marginPct < 30) return { label: "LOW RISK", tone: "green" };
  if (marginPct < 60) return { label: "MEDIUM RISK", tone: "yellow" };
  return { label: "HIGH RISK", tone: "red" };
}

export function winLossStats(history: Trade[], portfolio: Portfolio | null) {
  const trades = closedTradesOf(history);
  const wins = trades.filter((t) => t.pnl > 0).map((t) => t.pnl);
  const losses = trades.filter((t) => t.pnl < 0).map((t) => Math.abs(t.pnl));
  const grossProfit = wins.reduce((a, b) => a + b, 0);
  const grossLoss = losses.reduce((a, b) => a + b, 0);

  const avgWin = wins.length ? grossProfit / wins.length : 0;
  const avgLoss = losses.length ? grossLoss / losses.length : 0;
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

  const winRate =
    typeof portfolio?.win_rate === "number"
      ? portfolio.win_rate
      : trades.length
      ? (wins.length / trades.length) * 100
      : 0;

  const winRateFraction = winRate / 100;
  const expectancy = winRateFraction * avgWin - (1 - winRateFraction) * avgLoss;

  return { avgWin, avgLoss, profitFactor, expectancy, winRate, totalTrades: trades.length };
}
