import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  ComposedChart,
  CartesianGrid,
  Label,
  Line,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
  Info,
  Maximize2,
  Minimize2,
  Minus,
  SlidersHorizontal,
  Star,
  TrendingDown,
  TrendingUp,
  X,
} from "lucide-react";
import type { Candle } from "../../hooks/useAppData";
import { fmtAxisNumber, fmtNum, fmtUsd } from "../../lib/format";
import { useMediaQuery } from "../../hooks/useMediaQuery";

type Props = {
  symbol: string;
  onSymbolChange: (s: string) => void;
  interval: string;
  onIntervalChange: (i: string) => void;
  candles: Candle[];
  ticker: any;
  prediction: any;
};

type ChartPoint = {
  idx: number;
  time: number;
  actual: number | null;
  predicted: number | null;
  upper: number | null;
  lower: number | null;
  band: [number, number] | null;
  label: string;
};

const TIMEFRAME_CONFIG: Record<string, { label: string; ms: number }> = {
  "1h": { label: "1H", ms: 3_600_000 },
  "4h": { label: "4H", ms: 4 * 3_600_000 },
  "1d": { label: "1D", ms: 86_400_000 },
  "1w": { label: "1W", ms: 7 * 86_400_000 },
};
const TIMEFRAME_ORDER = ["1h", "4h", "1d", "1w"];

// Forecast bar count is fixed (not tied to timeframe or screen size) so the
// projection always reads as a long, clearly visible cone rather than a
// sliver next to months of history - and so it stays just as long on
// mobile, where only the *historical* window gets thinned for density.
const FORECAST_BARS = 32;
// How much trailing history to plot behind the forecast. Kept proportional
// to FORECAST_BARS (not the full 220-candle fetch) so the forecast segment
// occupies a substantial, readable fraction of the time axis instead of
// being dwarfed by weeks/months of history rendered at the same scale.
const HISTORY_BARS = 60;

const dateFmt = new Intl.DateTimeFormat([], { day: "numeric", month: "short" });
// Intraday timeframes (1H/4H) pack several ticks into the same calendar
// day, so date-only labels look like accidental repeats ("Jul 3 … Jul 3
// … Jul 4") - include the hour for those, and fall back to a bare date
// once a bar covers a full day or more (1D/1W).
const dateHourFmt = new Intl.DateTimeFormat([], { day: "numeric", month: "short", hour: "2-digit" });
const dateTimeFmt = new Intl.DateTimeFormat([], {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function formatHorizon(totalMs: number): string {
  const hours = totalMs / 3_600_000;
  if (hours < 48) return `${Math.round(hours)} Hours`;
  const days = hours / 24;
  if (days < 14) return `${Math.round(days)} Days`;
  return `${Math.round(days / 7)} Weeks`;
}

function fmtSignedPct(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function sampleCandles(candles: Candle[], maxPoints: number): Candle[] {
  if (candles.length <= maxPoints) return candles;
  const step = Math.ceil(candles.length / maxPoints);
  const sampled = candles.filter((_, i) => i % step === 0);
  const last = candles[candles.length - 1];
  if (sampled[sampled.length - 1] !== last) sampled.push(last);
  return sampled;
}

function buildTicks(chartData: ChartPoint[], nowIndex: number, tickCount: number) {
  const n = chartData.length;
  if (n === 0) return { values: [] as number[], labels: new Map<number, string>() };
  const indices = new Set<number>([0, n - 1, nowIndex]);
  for (let k = 1; k < tickCount - 1; k++) {
    indices.add(Math.round((k * (n - 1)) / (tickCount - 1)));
  }
  const sorted = Array.from(indices)
    .filter((i) => i >= 0 && i < n)
    .sort((a, b) => a - b);
  const labels = new Map<number, string>();
  sorted.forEach((i) => {
    const point = chartData[i];
    labels.set(point.time, i === nowIndex ? "Now" : point.label);
  });
  return { values: sorted.map((i) => chartData[i].time), labels };
}

function computeYDomain(chartData: ChartPoint[]): [number, number] {
  let min = Infinity;
  let max = -Infinity;
  chartData.forEach((d) => {
    [d.actual, d.predicted, d.upper, d.lower].forEach((v) => {
      if (typeof v === "number") {
        if (v < min) min = v;
        if (v > max) max = v;
      }
    });
  });
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  const pad = (max - min) * 0.1 || max * 0.01 || 1;
  return [min - pad, max + pad];
}

function CurrentPriceLabel({ viewBox, value }: any) {
  if (!viewBox) return null;
  const cx = viewBox.x + viewBox.width / 2;
  const cy = viewBox.y + viewBox.height / 2;
  const text = fmtAxisNumber(value);
  const w = Math.max(58, text.length * 8 + 22);
  const ty = cy - 24;
  return (
    <g>
      <rect x={cx - w / 2} y={ty - 12} width={w} height={24} rx={12} fill="var(--c-purple)" />
      <text x={cx} y={ty + 4} textAnchor="middle" fontSize={11} fontWeight={700} fill="#ffffff">
        {text}
      </text>
    </g>
  );
}

function ChartTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const point: ChartPoint = payload[0]?.payload;
  if (!point) return null;
  return (
    <div className="pc-tooltip">
      <div className="pc-tooltip-time">{dateTimeFmt.format(new Date(point.time))}</div>
      {typeof point.actual === "number" && (
        <div className="pc-tooltip-row">
          <span className="pc-dot purple" /> Actual <b>{fmtUsd(point.actual)}</b>
        </div>
      )}
      {typeof point.predicted === "number" && (
        <div className="pc-tooltip-row">
          <span className="pc-dot cyan" /> AI Prediction <b>{fmtUsd(point.predicted)}</b>
        </div>
      )}
      {typeof point.upper === "number" && (
        <div className="pc-tooltip-row">
          <span className="pc-dot green" /> Upper Band <b>{fmtUsd(point.upper)}</b>
        </div>
      )}
      {typeof point.lower === "number" && (
        <div className="pc-tooltip-row">
          <span className="pc-dot red" /> Lower Band <b>{fmtUsd(point.lower)}</b>
        </div>
      )}
    </div>
  );
}

function PredictionChart({ symbol, onSymbolChange, interval, onIntervalChange, candles, ticker, prediction }: Props) {
  const isMobile = useMediaQuery("(max-width: 640px)");
  const isTabletPortrait = useMediaQuery("(min-width: 768px) and (max-width: 1024px)");
  const isTabletLandscape = useMediaQuery("(min-width: 1025px) and (max-width: 1366px)");

  const [showIndicators, setShowIndicators] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const indicatorsRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function onOutsideClick(e: MouseEvent) {
      if (indicatorsRef.current && !indicatorsRef.current.contains(e.target as Node)) {
        setShowIndicators(false);
      }
    }
    document.addEventListener("mousedown", onOutsideClick);
    return () => document.removeEventListener("mousedown", onOutsideClick);
  }, []);

  const lastPrice = Number(ticker?.lastPrice || 0);
  const change = Number(ticker?.priceChangePercent || 0);
  const changeAbs = Number(ticker?.priceChange || 0);
  const features = prediction?.features || {};

  const tfConfig = TIMEFRAME_CONFIG[interval] || TIMEFRAME_CONFIG["1h"];

  const chartHeight = fullscreen
    ? undefined
    : isMobile
    ? 260
    : isTabletPortrait
    ? 320
    : isTabletLandscape
    ? 360
    : 400;

  const { chartData, nowIndex, nowTime, lastClose, isNoTrade } = useMemo(() => {
    const windowed = candles.length > HISTORY_BARS ? candles.slice(-HISTORY_BARS) : candles;
    const maxHistPoints = isMobile ? 30 : isTabletPortrait ? 45 : HISTORY_BARS;
    const sampled = sampleCandles(windowed, maxHistPoints);
    const isIntraday = tfConfig.ms < 24 * 3_600_000;
    const tickFmt = isIntraday ? dateHourFmt : dateFmt;

    const hist: ChartPoint[] = sampled.map((c, i) => ({
      idx: i,
      time: c.time,
      actual: c.close,
      predicted: null,
      upper: null,
      lower: null,
      band: null,
      label: tickFmt.format(new Date(c.time)),
    }));

    const lastCandle = sampled[sampled.length - 1];
    const lastCloseVal = lastCandle?.close ?? lastPrice ?? 0;
    const lastTime = lastCandle?.time ?? Date.now();

    const direction = prediction?.direction;
    const noTrade = !prediction || direction === "NO_TRADE" || !direction;
    const target = typeof prediction?.target === "number" ? prediction.target : lastCloseVal;
    const stop = typeof prediction?.stop === "number" ? prediction.stop : lastCloseVal;
    const confidence = Math.max(0, Math.min(100, prediction?.confidence ?? 50));

    const atr = features?.atr;
    const spreadBase = typeof atr === "number" && atr > 0 ? atr : lastCloseVal * 0.006;

    let boundHigh: number;
    let boundLow: number;
    let predictedEnd: number;
    if (noTrade) {
      boundHigh = lastCloseVal + spreadBase * 3;
      boundLow = lastCloseVal - spreadBase * 3;
      predictedEnd = lastCloseVal;
    } else {
      boundHigh = Math.max(target, stop, lastCloseVal) + spreadBase * 2 * (1.5 - confidence / 100);
      boundLow = Math.min(target, stop, lastCloseVal) - spreadBase * 2 * (1.5 - confidence / 100);
      predictedEnd = target;
    }

    // Anchor the forecast lines to the last actual close so they connect
    // visually with the actual-price line at the "Now" boundary.
    if (hist.length > 0) {
      hist[hist.length - 1] = {
        ...hist[hist.length - 1],
        predicted: lastCloseVal,
        upper: lastCloseVal,
        lower: lastCloseVal,
        band: [lastCloseVal, lastCloseVal],
      };
    }

    const forecastBars = FORECAST_BARS;
    const forecast: ChartPoint[] = [];
    for (let i = 1; i <= forecastBars; i++) {
      const t = i / forecastBars;
      const ease = 1 - Math.pow(1 - t, 2);
      const predicted = lastCloseVal + (predictedEnd - lastCloseVal) * ease;
      const upper = lastCloseVal + (boundHigh - lastCloseVal) * ease;
      const lower = lastCloseVal + (boundLow - lastCloseVal) * ease;
      const time = lastTime + tfConfig.ms * i;
      forecast.push({
        idx: hist.length + i - 1,
        time,
        actual: null,
        predicted,
        upper,
        lower,
        band: [lower, upper],
        label: tickFmt.format(new Date(time)),
      });
    }

    return {
      chartData: [...hist, ...forecast],
      nowIndex: hist.length - 1,
      nowTime: lastTime,
      lastClose: lastCloseVal,
      isNoTrade: noTrade,
    };
  }, [candles, prediction, features, isMobile, isTabletPortrait, tfConfig, lastPrice]);

  const yDomain = useMemo(() => computeYDomain(chartData), [chartData]);
  const ticks = useMemo(
    () => buildTicks(chartData, nowIndex, isMobile ? 4 : 7),
    [chartData, nowIndex, isMobile]
  );

  const direction = prediction?.direction;
  const confidence = Math.max(0, Math.min(100, prediction?.confidence ?? 0));
  const target = prediction?.target;
  const stop = prediction?.stop;

  const directionText = direction === "LONG" ? "BULLISH" : direction === "SHORT" ? "BEARISH" : "NO TRADE";
  const directionTone = direction === "LONG" ? "green" : direction === "SHORT" ? "red" : "yellow";
  const DirectionIcon = direction === "LONG" ? ArrowUpRight : direction === "SHORT" ? ArrowDownRight : Minus;
  const directionSub = isNoTrade
    ? prediction?.risk?.reason || "No active setup"
    : confidence >= 80
    ? `Strong ${direction === "LONG" ? "Buy" : "Sell"} Signal`
    : confidence >= 60
    ? `${direction === "LONG" ? "Buy" : "Sell"} Signal`
    : "Weak Signal";

  const confidenceLabel = confidence >= 80 ? "High Confidence" : confidence >= 60 ? "Medium Confidence" : "Low Confidence";
  const horizonText = formatHorizon(FORECAST_BARS * tfConfig.ms);

  const targetPct = lastClose && typeof target === "number" ? ((target - lastClose) / lastClose) * 100 : NaN;
  const stopPct = lastClose && typeof stop === "number" ? ((stop - lastClose) / lastClose) * 100 : NaN;

  const updatedAgo = useMemo(() => {
    if (typeof prediction?.computed_at !== "number") return null;
    const diffSec = Math.max(0, Math.floor((Date.now() - prediction.computed_at) / 1000));
    if (diffSec < 60) return "just now";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    return `${Math.floor(diffSec / 3600)}h ago`;
  }, [prediction?.computed_at]);

  return (
    <div className={`chart-card pc-card${fullscreen ? " pc-fullscreen" : ""}`}>
      <div className="chart-toolbar pc-toolbar">
        <div className="pc-title-group">
          <div className="pc-title-row">
            <h3>{symbol} Prediction Chart</h3>
            <span className="pc-info" title="AI-generated forecast from live model output. Not financial advice.">
              <Info size={14} />
            </span>
          </div>
          <div className="chart-symbol">
            <select value={symbol} onChange={(e) => onSymbolChange(e.target.value)}>
              <option value="BTCUSDT">BTCUSDT PERPETUAL</option>
              <option value="ETHUSDT">ETHUSDT PERPETUAL</option>
            </select>
            <button className="icon-btn" title="Favorite (coming soon)">
              <Star size={16} />
            </button>
          </div>
        </div>

        <div className="pc-controls">
          <div className="tf-group">
            {TIMEFRAME_ORDER.map((tf) => (
              <button
                key={tf}
                className={tf === interval ? "tf-btn active" : "tf-btn"}
                onClick={() => onIntervalChange(tf)}
              >
                {TIMEFRAME_CONFIG[tf].label}
              </button>
            ))}
          </div>

          <div className="pc-indicators-wrap" ref={indicatorsRef}>
            <button className="pc-dropdown-btn" onClick={() => setShowIndicators((v) => !v)}>
              <SlidersHorizontal size={14} /> Indicators <ChevronDown size={14} />
            </button>
            {showIndicators && (
              <div className="pc-indicators-panel">
                <div className="chip-row" style={{ marginTop: 0 }}>
                  <span className="chip">
                    EMA 20 <b>{fmtNum(features.ema20, 1)}</b>
                  </span>
                  <span className="chip">
                    EMA 50 <b>{fmtNum(features.ema50, 1)}</b>
                  </span>
                  <span className="chip">
                    RSI <b>{fmtNum(features.rsi, 1)}</b>
                  </span>
                  <span className={`chip ${(features.macd_hist ?? 0) >= 0 ? "green" : "red"}`}>
                    MACD <b>{(features.macd_hist ?? 0) >= 0 ? "Bullish" : "Bearish"}</b>
                  </span>
                  <span className="chip">
                    BB Width <b>{fmtNum(features.bb_width, 2)}</b>
                  </span>
                </div>
              </div>
            )}
          </div>

          <button
            className="icon-btn"
            title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
            onClick={() => setFullscreen((v) => !v)}
          >
            {fullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
        </div>
      </div>

      <div className="pc-legend-row">
        <div className="pc-legend">
          <span className="pc-legend-item">
            <i className="pc-swatch solid purple" /> Actual Price
          </span>
          <span className="pc-legend-item">
            <i className="pc-swatch solid cyan" /> AI Prediction
          </span>
          <span className="pc-legend-item">
            <i className="pc-swatch dash green" /> Upper Band
          </span>
          <span className="pc-legend-item">
            <i className="pc-swatch dash red" /> Lower Band
          </span>
        </div>

        <div className="pc-price-row">
          <b>${lastPrice.toLocaleString()}</b>
          <span className={change >= 0 ? "green" : "red"}>
            {change >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
            {change.toFixed(2)}% ({changeAbs >= 0 ? "+" : ""}
            {changeAbs.toFixed(1)})
          </span>
        </div>
      </div>

      <div className="pc-chart-wrap" style={{ height: fullscreen ? "calc(100vh - 210px)" : chartHeight }}>
        {fullscreen && (
          <button className="icon-btn pc-fullscreen-close" onClick={() => setFullscreen(false)} title="Close">
            <X size={18} />
          </button>
        )}
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 16, right: isMobile ? 36 : 54, left: 0, bottom: 4 }}>
            <defs>
              <linearGradient id="pcForecastZone" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--c-blue)" stopOpacity={0.38} />
                <stop offset="100%" stopColor="var(--c-blue)" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
            <XAxis
              dataKey="time"
              type="number"
              domain={["dataMin", "dataMax"]}
              ticks={ticks.values}
              tickFormatter={(v: number) => ticks.labels.get(v) ?? ""}
              stroke="#8b90a8"
              fontSize={11}
              tickMargin={8}
            />
            <YAxis
              orientation="right"
              domain={yDomain}
              tickFormatter={fmtAxisNumber}
              stroke="#8b90a8"
              fontSize={11}
              width={isMobile ? 46 : 64}
            />
            <Tooltip content={<ChartTooltip />} />

            <Area
              dataKey="band"
              stroke="none"
              fill="url(#pcForecastZone)"
              isAnimationActive={false}
              connectNulls
              activeDot={false}
            />
            <Line
              dataKey="upper"
              stroke="var(--c-green)"
              strokeWidth={2.25}
              strokeDasharray="9 5"
              dot={false}
              isAnimationActive={false}
              connectNulls
              style={{ filter: "drop-shadow(0 0 4px rgba(0,245,160,.5))" }}
            />
            <Line
              dataKey="lower"
              stroke="var(--c-red)"
              strokeWidth={2.25}
              strokeDasharray="9 5"
              dot={false}
              isAnimationActive={false}
              connectNulls
              style={{ filter: "drop-shadow(0 0 4px rgba(255,93,115,.5))" }}
            />
            <Line
              dataKey="predicted"
              stroke="var(--c-cyan)"
              strokeWidth={3.25}
              dot={false}
              isAnimationActive={false}
              connectNulls
              style={{ filter: "drop-shadow(0 0 7px rgba(var(--c-cyan-rgb),.75))" }}
            />
            <Line
              dataKey="actual"
              stroke="var(--c-purple)"
              strokeWidth={2.75}
              dot={false}
              isAnimationActive={false}
              connectNulls={false}
              style={{ filter: "drop-shadow(0 0 6px rgba(124,92,255,.6))" }}
            />

            <ReferenceLine x={nowTime} stroke="rgba(255,255,255,0.28)" strokeDasharray="4 4" />
            <ReferenceDot x={nowTime} y={lastClose} r={5} fill="#fff" stroke="var(--c-purple)" strokeWidth={2}>
              <Label content={(p: any) => <CurrentPriceLabel {...p} value={lastClose} />} />
            </ReferenceDot>
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="pc-summary">
        <div className="pc-tile">
          <span className="tile-label">Direction</span>
          <div className={`pc-direction-badge ${directionTone}`}>
            <DirectionIcon size={16} />
            {directionText}
          </div>
          <span className="pc-tile-sub">{directionSub}</span>
        </div>

        <div className="pc-tile">
          <span className="tile-label">Confidence</span>
          <div className="pc-mini-gauge" style={{ ["--pct" as any]: confidence, ["--tone" as any]: `var(--c-${directionTone})` }}>
            <div className="pc-mini-gauge-inner">{fmtNum(confidence, 0)}%</div>
          </div>
          <span className="pc-tile-sub">{confidenceLabel}</span>
        </div>

        <div className="pc-tile">
          <span className="tile-label">Price Target {isNoTrade ? "" : `(${tfConfig.label})`}</span>
          <b className="tile-value green">{isNoTrade ? "—" : fmtUsd(target)}</b>
          <span className="pc-tile-sub green">{isNoTrade ? "" : fmtSignedPct(targetPct)}</span>
        </div>

        <div className="pc-tile">
          <span className="tile-label">Stop Loss</span>
          <b className="tile-value red">{isNoTrade ? "—" : fmtUsd(stop)}</b>
          <span className="pc-tile-sub red">{isNoTrade ? "" : fmtSignedPct(stopPct)}</span>
        </div>

        <div className="pc-tile">
          <span className="tile-label">Prediction Horizon</span>
          <b className="tile-value">{horizonText}</b>
          <span className="pc-tile-sub">{updatedAgo ? `Updated ${updatedAgo}` : "—"}</span>
        </div>
      </div>
    </div>
  );
}

export default memo(PredictionChart);
