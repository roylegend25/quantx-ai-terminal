// Pure indicator math for the pro prediction chart. Everything operates on
// the candle arrays already fetched from the backend - no synthetic data.
// Values that need a warm-up window are NaN until enough bars exist, and the
// renderer simply skips NaN points.

import type { Candle } from "../hooks/useAppData";

export type Series = Float64Array;

const NAN = Number.NaN;

function empty(n: number): Series {
  const a = new Float64Array(n);
  a.fill(NAN);
  return a;
}

export function closes(candles: Candle[]): Series {
  const out = new Float64Array(candles.length);
  for (let i = 0; i < candles.length; i++) out[i] = candles[i].close;
  return out;
}

export function ema(values: ArrayLike<number>, period: number): Series {
  const n = values.length;
  const out = empty(n);
  if (n === 0) return out;
  const k = 2 / (period + 1);
  let prev = values[0];
  out[0] = prev;
  for (let i = 1; i < n; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  // hide the unstable warm-up region
  for (let i = 0; i < Math.min(period - 1, n); i++) out[i] = NAN;
  return out;
}

export function sma(values: ArrayLike<number>, period: number): Series {
  const n = values.length;
  const out = empty(n);
  let sum = 0;
  for (let i = 0; i < n; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

function stddev(values: ArrayLike<number>, period: number): Series {
  const n = values.length;
  const out = empty(n);
  for (let i = period - 1; i < n; i++) {
    let mean = 0;
    for (let j = i - period + 1; j <= i; j++) mean += values[j];
    mean /= period;
    let acc = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = values[j] - mean;
      acc += d * d;
    }
    out[i] = Math.sqrt(acc / period);
  }
  return out;
}

export function vwap(candles: Candle[]): Series {
  // Session-anchored VWAP, reset at each UTC day boundary (standard for
  // 24/7 crypto charts).
  const n = candles.length;
  const out = empty(n);
  let cumPV = 0;
  let cumV = 0;
  let day = -1;
  for (let i = 0; i < n; i++) {
    const c = candles[i];
    const d = Math.floor(c.time / 86_400_000);
    if (d !== day) {
      day = d;
      cumPV = 0;
      cumV = 0;
    }
    const typical = (c.high + c.low + c.close) / 3;
    cumPV += typical * c.volume;
    cumV += c.volume;
    out[i] = cumV > 0 ? cumPV / cumV : c.close;
  }
  return out;
}

export function rsi(candles: Candle[], period = 14): Series {
  const n = candles.length;
  const out = empty(n);
  if (n < period + 1) return out;
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const d = candles[i].close - candles[i - 1].close;
    if (d >= 0) avgGain += d;
    else avgLoss -= d;
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < n; i++) {
    const d = candles[i].close - candles[i - 1].close;
    avgGain = (avgGain * (period - 1) + Math.max(d, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-d, 0)) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

export type MacdResult = { line: Series; signal: Series; hist: Series };

export function macd(candles: Candle[], fast = 12, slow = 26, sig = 9): MacdResult {
  const c = closes(candles);
  const emaFast = ema(c, fast);
  const emaSlow = ema(c, slow);
  const n = candles.length;
  const line = empty(n);
  for (let i = 0; i < n; i++) line[i] = emaFast[i] - emaSlow[i];
  // signal EMA over the macd line, skipping NaN warm-up
  const signal = empty(n);
  const k = 2 / (sig + 1);
  let prev = NAN;
  for (let i = 0; i < n; i++) {
    if (Number.isNaN(line[i])) continue;
    prev = Number.isNaN(prev) ? line[i] : line[i] * k + prev * (1 - k);
    signal[i] = prev;
  }
  const hist = empty(n);
  for (let i = 0; i < n; i++) hist[i] = line[i] - signal[i];
  return { line, signal, hist };
}

export type BollingerResult = { upper: Series; mid: Series; lower: Series };

export function bollinger(candles: Candle[], period = 20, mult = 2): BollingerResult {
  const c = closes(candles);
  const mid = sma(c, period);
  const sd = stddev(c, period);
  const n = candles.length;
  const upper = empty(n);
  const lower = empty(n);
  for (let i = 0; i < n; i++) {
    upper[i] = mid[i] + mult * sd[i];
    lower[i] = mid[i] - mult * sd[i];
  }
  return { upper, mid, lower };
}

function trueRange(candles: Candle[], i: number): number {
  if (i === 0) return candles[0].high - candles[0].low;
  const prevClose = candles[i - 1].close;
  return Math.max(
    candles[i].high - candles[i].low,
    Math.abs(candles[i].high - prevClose),
    Math.abs(candles[i].low - prevClose)
  );
}

export function atr(candles: Candle[], period = 14): Series {
  const n = candles.length;
  const out = empty(n);
  if (n === 0) return out;
  let acc = 0;
  for (let i = 0; i < n; i++) {
    const tr = trueRange(candles, i);
    if (i < period) {
      acc += tr;
      if (i === period - 1) out[i] = acc / period;
    } else {
      out[i] = (out[i - 1] * (period - 1) + tr) / period;
    }
  }
  return out;
}

export type AdxResult = { adx: Series; plusDI: Series; minusDI: Series };

export function adx(candles: Candle[], period = 14): AdxResult {
  const n = candles.length;
  const adxOut = empty(n);
  const plusOut = empty(n);
  const minusOut = empty(n);
  if (n < period + 1) return { adx: adxOut, plusDI: plusOut, minusDI: minusOut };

  let smTR = 0;
  let smPlus = 0;
  let smMinus = 0;
  let prevAdx = NAN;
  let dxSum = 0;
  let dxCount = 0;

  for (let i = 1; i < n; i++) {
    const upMove = candles[i].high - candles[i - 1].high;
    const downMove = candles[i - 1].low - candles[i].low;
    const plusDM = upMove > downMove && upMove > 0 ? upMove : 0;
    const minusDM = downMove > upMove && downMove > 0 ? downMove : 0;
    const tr = trueRange(candles, i);

    if (i <= period) {
      smTR += tr;
      smPlus += plusDM;
      smMinus += minusDM;
      if (i < period) continue;
    } else {
      smTR = smTR - smTR / period + tr;
      smPlus = smPlus - smPlus / period + plusDM;
      smMinus = smMinus - smMinus / period + minusDM;
    }

    const pdi = smTR > 0 ? (smPlus / smTR) * 100 : 0;
    const mdi = smTR > 0 ? (smMinus / smTR) * 100 : 0;
    plusOut[i] = pdi;
    minusOut[i] = mdi;
    const dx = pdi + mdi > 0 ? (Math.abs(pdi - mdi) / (pdi + mdi)) * 100 : 0;

    if (dxCount < period) {
      dxSum += dx;
      dxCount++;
      if (dxCount === period) {
        prevAdx = dxSum / period;
        adxOut[i] = prevAdx;
      }
    } else {
      prevAdx = (prevAdx * (period - 1) + dx) / period;
      adxOut[i] = prevAdx;
    }
  }
  return { adx: adxOut, plusDI: plusOut, minusDI: minusOut };
}

export type IchimokuResult = {
  tenkan: Series;
  kijun: Series;
  senkouA: Series; // already displaced forward by `displacement`
  senkouB: Series;
  displacement: number;
};

function midRange(candles: Candle[], i: number, period: number): number {
  if (i < period - 1) return NAN;
  let hi = -Infinity;
  let lo = Infinity;
  for (let j = i - period + 1; j <= i; j++) {
    if (candles[j].high > hi) hi = candles[j].high;
    if (candles[j].low < lo) lo = candles[j].low;
  }
  return (hi + lo) / 2;
}

export function ichimoku(candles: Candle[], tenkanP = 9, kijunP = 26, senkouBP = 52, displacement = 26): IchimokuResult {
  const n = candles.length;
  const tenkan = empty(n);
  const kijun = empty(n);
  const senkouA = empty(n + displacement);
  const senkouB = empty(n + displacement);
  for (let i = 0; i < n; i++) {
    tenkan[i] = midRange(candles, i, tenkanP);
    kijun[i] = midRange(candles, i, kijunP);
    const a = (tenkan[i] + kijun[i]) / 2;
    senkouA[i + displacement] = a;
    senkouB[i + displacement] = midRange(candles, i, senkouBP);
  }
  return { tenkan, kijun, senkouA, senkouB, displacement };
}

export type SupertrendResult = { line: Series; direction: Int8Array }; // 1 = up (support below), -1 = down

export function supertrend(candles: Candle[], period = 10, mult = 3): SupertrendResult {
  const n = candles.length;
  const line = empty(n);
  const dir = new Int8Array(n);
  const atrS = atr(candles, period);
  let upper = NAN;
  let lower = NAN;
  let trend = 1;
  for (let i = 0; i < n; i++) {
    if (Number.isNaN(atrS[i])) continue;
    const mid = (candles[i].high + candles[i].low) / 2;
    const basicUpper = mid + mult * atrS[i];
    const basicLower = mid - mult * atrS[i];
    upper = Number.isNaN(upper) || basicUpper < upper || candles[i - 1]?.close > upper ? basicUpper : upper;
    lower = Number.isNaN(lower) || basicLower > lower || candles[i - 1]?.close < lower ? basicLower : lower;
    if (trend === 1 && candles[i].close < lower) trend = -1;
    else if (trend === -1 && candles[i].close > upper) trend = 1;
    line[i] = trend === 1 ? lower : upper;
    dir[i] = trend as 1 | -1;
  }
  return { line, direction: dir };
}

export type PivotLevels = { p: number; r1: number; r2: number; r3: number; s1: number; s2: number; s3: number } | null;

export function pivotPoints(candles: Candle[]): PivotLevels {
  // Classic floor-trader pivots from the previous completed UTC day.
  if (candles.length < 2) return null;
  const lastDay = Math.floor(candles[candles.length - 1].time / 86_400_000);
  let hi = -Infinity;
  let lo = Infinity;
  let close = NAN;
  let found = false;
  for (let i = candles.length - 1; i >= 0; i--) {
    const d = Math.floor(candles[i].time / 86_400_000);
    if (d === lastDay) continue;
    if (d < lastDay - 1) break;
    found = true;
    if (candles[i].high > hi) hi = candles[i].high;
    if (candles[i].low < lo) lo = candles[i].low;
    if (Number.isNaN(close)) close = candles[i].close; // last bar of prev day hit first
  }
  if (!found) return null;
  const p = (hi + lo + close) / 3;
  return {
    p,
    r1: 2 * p - lo,
    s1: 2 * p - hi,
    r2: p + (hi - lo),
    s2: p - (hi - lo),
    r3: hi + 2 * (p - lo),
    s3: lo - 2 * (hi - p),
  };
}

export type SRLevel = { price: number; touches: number; kind: "support" | "resistance" };

export function supportResistance(candles: Candle[], lookback = 200, pivotSpan = 5, maxLevels = 6): SRLevel[] {
  const start = Math.max(0, candles.length - lookback);
  const highs: number[] = [];
  const lows: number[] = [];
  for (let i = start + pivotSpan; i < candles.length - pivotSpan; i++) {
    let isHigh = true;
    let isLow = true;
    for (let j = i - pivotSpan; j <= i + pivotSpan; j++) {
      if (candles[j].high > candles[i].high) isHigh = false;
      if (candles[j].low < candles[i].low) isLow = false;
      if (!isHigh && !isLow) break;
    }
    if (isHigh) highs.push(candles[i].high);
    if (isLow) lows.push(candles[i].low);
  }
  const lastClose = candles[candles.length - 1]?.close ?? 0;
  const tolerance = lastClose * 0.004;

  function cluster(values: number[], kind: "support" | "resistance"): SRLevel[] {
    const sorted = [...values].sort((a, b) => a - b);
    const levels: SRLevel[] = [];
    let group: number[] = [];
    const flush = () => {
      if (group.length === 0) return;
      const price = group.reduce((s, v) => s + v, 0) / group.length;
      levels.push({ price, touches: group.length, kind });
      group = [];
    };
    for (const v of sorted) {
      if (group.length === 0 || v - group[group.length - 1] <= tolerance) group.push(v);
      else {
        flush();
        group = [v];
      }
    }
    flush();
    return levels.sort((a, b) => b.touches - a.touches).slice(0, maxLevels);
  }

  return [...cluster(lows, "support"), ...cluster(highs, "resistance")];
}

export type RegressionChannel = {
  startIdx: number;
  endIdx: number;
  valueAt: (idx: number) => number;
  halfWidth: number; // 2 std dev of residuals
} | null;

export function regressionChannel(candles: Candle[], lookback = 120): RegressionChannel {
  const start = Math.max(0, candles.length - lookback);
  const n = candles.length - start;
  if (n < 10) return null;
  let sx = 0;
  let sy = 0;
  let sxx = 0;
  let sxy = 0;
  for (let i = 0; i < n; i++) {
    const y = candles[start + i].close;
    sx += i;
    sy += y;
    sxx += i * i;
    sxy += i * y;
  }
  const denom = n * sxx - sx * sx;
  if (denom === 0) return null;
  const slope = (n * sxy - sx * sy) / denom;
  const intercept = (sy - slope * sx) / n;
  let acc = 0;
  for (let i = 0; i < n; i++) {
    const r = candles[start + i].close - (intercept + slope * i);
    acc += r * r;
  }
  const sd = Math.sqrt(acc / n);
  return {
    startIdx: start,
    endIdx: candles.length - 1,
    valueAt: (idx: number) => intercept + slope * (idx - start),
    halfWidth: 2 * sd,
  };
}

export type VolumeProfileBin = { priceLow: number; priceHigh: number; volume: number };

export function volumeProfile(candles: Candle[], bins = 24, lookback = 220): VolumeProfileBin[] {
  const start = Math.max(0, candles.length - lookback);
  let min = Infinity;
  let max = -Infinity;
  for (let i = start; i < candles.length; i++) {
    if (candles[i].low < min) min = candles[i].low;
    if (candles[i].high > max) max = candles[i].high;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [];
  const step = (max - min) / bins;
  const out: VolumeProfileBin[] = Array.from({ length: bins }, (_, b) => ({
    priceLow: min + b * step,
    priceHigh: min + (b + 1) * step,
    volume: 0,
  }));
  for (let i = start; i < candles.length; i++) {
    const c = candles[i];
    // spread each candle's volume across the bins its range covers
    const lowBin = Math.max(0, Math.min(bins - 1, Math.floor((c.low - min) / step)));
    const highBin = Math.max(0, Math.min(bins - 1, Math.floor((c.high - min) / step)));
    const span = highBin - lowBin + 1;
    for (let b = lowBin; b <= highBin; b++) out[b].volume += c.volume / span;
  }
  return out;
}

export type FvgZone = { startIdx: number; low: number; high: number; side: "bull" | "bear" };

export function fairValueGaps(candles: Candle[], lookback = 120, minGapAtrMult = 0.25): FvgZone[] {
  const zones: FvgZone[] = [];
  const atrS = atr(candles, 14);
  const start = Math.max(2, candles.length - lookback);
  for (let i = start; i < candles.length; i++) {
    const a = candles[i - 2];
    const c = candles[i];
    const gapAtr = Number.isNaN(atrS[i]) ? (c.high - c.low) : atrS[i];
    // bullish FVG: candle i-2 high < candle i low
    if (c.low - a.high > gapAtr * minGapAtrMult) {
      zones.push({ startIdx: i - 1, low: a.high, high: c.low, side: "bull" });
    } else if (a.low - c.high > gapAtr * minGapAtrMult) {
      zones.push({ startIdx: i - 1, low: c.high, high: a.low, side: "bear" });
    }
  }
  // keep only zones not yet filled by a later close
  return zones
    .filter((z) => {
      for (let i = z.startIdx + 1; i < candles.length; i++) {
        const c = candles[i];
        if (z.side === "bull" && c.close < z.low) return false;
        if (z.side === "bear" && c.close > z.high) return false;
      }
      return true;
    })
    .slice(-8);
}

export type OrderBlockZone = { startIdx: number; low: number; high: number; side: "bull" | "bear" };

export function orderBlocks(candles: Candle[], lookback = 150, impulseAtrMult = 1.6): OrderBlockZone[] {
  // A simplified institutional order-block heuristic: the last opposite-color
  // candle immediately before an impulsive move of > impulseAtrMult * ATR.
  const zones: OrderBlockZone[] = [];
  const atrS = atr(candles, 14);
  const start = Math.max(1, candles.length - lookback);
  for (let i = start; i < candles.length - 1; i++) {
    const cur = candles[i];
    const next = candles[i + 1];
    const a = Number.isNaN(atrS[i]) ? cur.high - cur.low : atrS[i];
    if (a <= 0) continue;
    const nextMove = next.close - next.open;
    const curBear = cur.close < cur.open;
    const curBull = cur.close > cur.open;
    if (curBear && nextMove > impulseAtrMult * a) {
      zones.push({ startIdx: i, low: cur.low, high: cur.high, side: "bull" });
    } else if (curBull && nextMove < -impulseAtrMult * a) {
      zones.push({ startIdx: i, low: cur.low, high: cur.high, side: "bear" });
    }
  }
  return zones
    .filter((z) => {
      // drop mitigated blocks (price closed through the far side)
      for (let i = z.startIdx + 2; i < candles.length; i++) {
        const c = candles[i];
        if (z.side === "bull" && c.close < z.low) return false;
        if (z.side === "bear" && c.close > z.high) return false;
      }
      return true;
    })
    .slice(-6);
}
