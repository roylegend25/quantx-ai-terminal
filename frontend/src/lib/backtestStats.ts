/**
 * Client-side derived analytics over an advanced-backtest run's REAL trade
 * list and equity curve (app/research/advanced_backtest.py). Everything here
 * is a pure transformation of backend data — no synthetic numbers. Functions
 * degrade to empty results when the inputs are missing so panels can render
 * honest empty states.
 */

export type BtTrade = {
  pnl: number;
  entry_time: string | null;
  exit_time: string | null;
  confidence: number | null;
  correct: boolean | null;
  side: "LONG" | "SHORT";
  entry_price: number;
  exit_price: number;
  r_multiple: number | null;
  exit_reason: string;
  commission?: number;
  funding?: number;
  regime?: string;
};

export type DirectionLens = "both" | "long" | "short";

export function filterTradesByDirection(trades: BtTrade[], lens: DirectionLens): BtTrade[] {
  if (lens === "long") return trades.filter((t) => t.side === "LONG");
  if (lens === "short") return trades.filter((t) => t.side === "SHORT");
  return trades;
}

export type TradeAverages = {
  netProfit: number;
  grossProfit: number;
  grossLoss: number;
  avgTrade: number | null;
  avgWinner: number | null;
  avgLoser: number | null;
};

export function tradeAverages(trades: BtTrade[]): TradeAverages {
  const pnls = trades.map((t) => Number(t.pnl) || 0);
  const winners = pnls.filter((p) => p >= 0);
  const losers = pnls.filter((p) => p < 0);
  const grossProfit = winners.reduce((a, b) => a + b, 0);
  const grossLoss = losers.reduce((a, b) => a + b, 0);
  return {
    netProfit: grossProfit + grossLoss,
    grossProfit,
    grossLoss,
    avgTrade: pnls.length ? (grossProfit + grossLoss) / pnls.length : null,
    avgWinner: winners.length ? grossProfit / winners.length : null,
    avgLoser: losers.length ? grossLoss / losers.length : null,
  };
}

/** Net profit divided by the deepest equity dip in currency terms. */
export function recoveryFactor(equityCurve: number[] | undefined): number | null {
  if (!equityCurve || equityCurve.length < 2) return null;
  let peak = equityCurve[0];
  let maxDdAbs = 0;
  for (const e of equityCurve) {
    peak = Math.max(peak, e);
    maxDdAbs = Math.max(maxDdAbs, peak - e);
  }
  const net = equityCurve[equityCurve.length - 1] - equityCurve[0];
  if (maxDdAbs <= 0) return null;
  return net / maxDdAbs;
}

/** Same ranking score the backend optimizer uses: Sharpe, falling back to
 *  total return when Sharpe can't be computed. */
export function strategyScore(metrics: any): number | null {
  if (!metrics) return null;
  if (typeof metrics.sharpe_ratio === "number") return metrics.sharpe_ratio;
  if (typeof metrics.total_return_pct === "number") return metrics.total_return_pct / 100;
  return null;
}

export type HistogramBin = { label: string; count: number; mid: number };

export function pnlHistogram(trades: BtTrade[], binCount = 15): HistogramBin[] {
  const pnls = trades.map((t) => Number(t.pnl) || 0);
  if (pnls.length === 0) return [];
  const min = Math.min(...pnls);
  const max = Math.max(...pnls);
  if (min === max) {
    return [{ label: min.toFixed(1), count: pnls.length, mid: min }];
  }
  const width = (max - min) / binCount;
  const bins: HistogramBin[] = Array.from({ length: binCount }, (_, i) => ({
    label: (min + width * (i + 0.5)).toFixed(1),
    mid: min + width * (i + 0.5),
    count: 0,
  }));
  for (const p of pnls) {
    const idx = Math.min(binCount - 1, Math.floor((p - min) / width));
    bins[idx].count += 1;
  }
  return bins;
}

export type MonthlyCell = { year: number; month: number; pct: number };

/** Sequential monthly returns: walks trades in exit order compounding the
 *  starting balance, so each month's % is relative to the balance that
 *  actually entered the month. */
export function monthlyReturns(trades: BtTrade[], startingBalance: number): MonthlyCell[] {
  const dated = trades
    .filter((t) => t.exit_time)
    .map((t) => ({ pnl: Number(t.pnl) || 0, at: new Date(t.exit_time as string) }))
    .filter((t) => !Number.isNaN(t.at.getTime()))
    .sort((a, b) => a.at.getTime() - b.at.getTime());
  if (!dated.length || !startingBalance) return [];

  const cells: MonthlyCell[] = [];
  let balance = startingBalance;
  let curKey = "";
  let monthStartBalance = balance;
  let monthPnl = 0;
  let curYear = 0;
  let curMonth = 0;

  const flush = () => {
    if (curKey && monthStartBalance > 0) {
      cells.push({ year: curYear, month: curMonth, pct: (monthPnl / monthStartBalance) * 100 });
    }
  };

  for (const t of dated) {
    const key = `${t.at.getUTCFullYear()}-${t.at.getUTCMonth()}`;
    if (key !== curKey) {
      flush();
      curKey = key;
      curYear = t.at.getUTCFullYear();
      curMonth = t.at.getUTCMonth();
      monthStartBalance = balance;
      monthPnl = 0;
    }
    monthPnl += t.pnl;
    balance += t.pnl;
  }
  flush();
  return cells;
}

export type RollingPoint = { i: number; sharpe: number | null; winRate: number | null };

/** Rolling trade-level Sharpe (per-trade return mean/std, not annualized —
 *  meant for shape, not absolute comparison) and rolling win rate. */
export function rollingSeries(trades: BtTrade[], startingBalance: number, window = 20): RollingPoint[] {
  const ordered = [...trades].sort((a, b) => {
    const ta = new Date(a.exit_time || a.entry_time || 0).getTime();
    const tb = new Date(b.exit_time || b.entry_time || 0).getTime();
    return ta - tb;
  });
  if (ordered.length < 5) return [];
  const w = Math.min(window, Math.max(5, Math.floor(ordered.length / 3)));

  // per-trade returns against the compounding balance
  let balance = startingBalance;
  const returns: number[] = [];
  const winsFlags: number[] = [];
  for (const t of ordered) {
    const pnl = Number(t.pnl) || 0;
    returns.push(balance > 0 ? pnl / balance : 0);
    winsFlags.push(pnl >= 0 ? 1 : 0);
    balance += pnl;
  }

  const out: RollingPoint[] = [];
  for (let i = w - 1; i < returns.length; i++) {
    const slice = returns.slice(i - w + 1, i + 1);
    const mean = slice.reduce((a, b) => a + b, 0) / w;
    const variance = slice.reduce((a, b) => a + (b - mean) * (b - mean), 0) / w;
    const std = Math.sqrt(variance);
    const wins = winsFlags.slice(i - w + 1, i + 1).reduce((a, b) => a + b, 0);
    out.push({
      i: i + 1,
      sharpe: std > 0 ? mean / std : null,
      winRate: (wins / w) * 100,
    });
  }
  return out;
}

export type ConfusionCells = {
  longWin: number;
  longLoss: number;
  shortWin: number;
  shortLoss: number;
};

/** Predicted direction (the trade's side) vs realized outcome (pnl sign). */
export function confusionMatrix(trades: BtTrade[]): ConfusionCells {
  const cells: ConfusionCells = { longWin: 0, longLoss: 0, shortWin: 0, shortLoss: 0 };
  for (const t of trades) {
    const win = (Number(t.pnl) || 0) >= 0;
    if (t.side === "LONG") win ? cells.longWin++ : cells.longLoss++;
    else win ? cells.shortWin++ : cells.shortLoss++;
  }
  return cells;
}

export function drawdownFromEquity(equityCurve: number[] | undefined): number[] {
  if (!equityCurve?.length) return [];
  let peak = equityCurve[0];
  return equityCurve.map((e) => {
    peak = Math.max(peak, e);
    return peak > 0 ? ((peak - e) / peak) * 100 : 0;
  });
}

export function fmtHolding(entry?: string | null, exit?: string | null): string {
  if (!entry || !exit) return "—";
  const ms = new Date(exit).getTime() - new Date(entry).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  const mins = Math.floor(ms / 60000);
  const days = Math.floor(mins / 1440);
  const hours = Math.floor((mins % 1440) / 60);
  const rem = mins % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${rem}m`;
  return `${rem}m`;
}

export function tradesToCsv(trades: BtTrade[], symbol: string, strategy: string): string {
  if (!trades.length) return "";
  const cols = [
    "date",
    "time",
    "symbol",
    "direction",
    "entry",
    "exit",
    "pnl",
    "return_pct",
    "r_multiple",
    "holding",
    "confidence",
    "regime",
    "reason",
    "strategy",
    "commission",
    "funding",
  ];
  const lines = [cols.join(",")];
  for (const t of trades) {
    const d = t.entry_time ? new Date(t.entry_time) : null;
    const retPct = t.entry_price && t.exit_price
      ? (t.side === "LONG"
          ? ((t.exit_price - t.entry_price) / t.entry_price) * 100
          : ((t.entry_price - t.exit_price) / t.entry_price) * 100)
      : null;
    const row = [
      d ? d.toISOString().slice(0, 10) : "",
      d ? d.toISOString().slice(11, 19) : "",
      symbol,
      t.side,
      t.entry_price,
      t.exit_price,
      t.pnl,
      retPct != null ? retPct.toFixed(4) : "",
      t.r_multiple ?? "",
      fmtHolding(t.entry_time, t.exit_time),
      t.confidence ?? "",
      t.regime ?? "",
      t.exit_reason,
      strategy,
      t.commission ?? "",
      t.funding ?? "",
    ];
    lines.push(row.map((v) => String(v ?? "").replace(/,/g, ";")).join(","));
  }
  return lines.join("\n");
}

export function downloadBlob(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** % move from entry to exit signed by trade direction. */
export function tradeReturnPct(t: BtTrade): number | null {
  if (!t.entry_price || !t.exit_price) return null;
  return t.side === "LONG"
    ? ((t.exit_price - t.entry_price) / t.entry_price) * 100
    : ((t.entry_price - t.exit_price) / t.entry_price) * 100;
}
