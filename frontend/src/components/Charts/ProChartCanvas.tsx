// Institutional-grade canvas chart engine for the AI prediction chart.
//
// Design constraints (see PredictionChart.tsx for the container):
//  - One requestAnimationFrame loop; frames are skipped entirely unless a
//    "dirty" flag is set (data changed, an animation is still settling, or
//    the pointer moved), so idle CPU cost is near zero.
//  - No React state updates from interaction or the 1s live tick - crosshair,
//    pan/zoom and price smoothing all live in refs and draw straight to the
//    canvas. React only re-renders when actual props change (10s poll).
//  - GPU-friendly: a single canvas sized to devicePixelRatio, no layout
//    thrash, no DOM mutations per frame.

import { memo, useEffect, useMemo, useRef } from "react";
import type { Candle } from "../../hooks/useAppData";
import {
  adx,
  atr,
  bollinger,
  closes,
  ema,
  fairValueGaps,
  ichimoku,
  macd,
  orderBlocks,
  pivotPoints,
  regressionChannel,
  rsi,
  supertrend,
  supportResistance,
  volumeProfile,
  vwap,
  type Series,
} from "../../lib/chartIndicators";

export type IndicatorId =
  | "ema20"
  | "ema50"
  | "ema100"
  | "ema200"
  | "vwap"
  | "bollinger"
  | "supertrend"
  | "ichimoku"
  | "volume"
  | "volumeProfile"
  | "liquidity"
  | "sr"
  | "pivots"
  | "orderBlocks"
  | "fvg"
  | "regression"
  | "rsi"
  | "macd"
  | "atr"
  | "adx";

export type HistoryPoint = {
  id: number;
  timestamp: number;
  timeframe?: string | null;
  direction: string | null;
  confidence: number | null;
  regime: string | null;
  strategy: string | null;
  entry_price: number | null;
  predicted_price: number | null;
  target: number | null;
  stop: number | null;
  actual_price: number | null;
  outcome: string;
  accuracy_pct: number | null;
  error_pct?: number | null;
};

/** Buckets the backend's specific outcome values down to the three states
 *  this overlay actually distinguishes visually: correct (green), wrong
 *  (red), or not yet resolved (grey/yellow). Kept local to this module so
 *  both the dot color and the tooltip label always agree. */
function outcomeBucket(outcome: string | null | undefined): "correct" | "wrong" | "unresolved" {
  if (outcome === "CORRECT" || outcome === "WIN") return "correct";
  if (outcome === "INCORRECT" || outcome === "LOSS") return "wrong";
  return "unresolved";
}

export type LiquidityCluster = {
  price: number;
  intensity: number;
  dominant_side: string;
};

/** One paper trade (open or closed) as delivered by /api/paper/positions /
 *  /api/paper/history, including decision provenance for the tooltip. */
export type TradeMarker = {
  id: number;
  side: string;
  status: string;
  entry: number | null;
  exit?: number | null;
  sl?: number | null;
  tp?: number | null;
  pnl?: number | null;
  opened_at?: string | null;
  closed_at?: string | null;
  decision_mode?: string | null;
  champion_model_type?: string | null;
  strategy_used?: string | null;
  confidence?: number | null;
  risk_reason?: string | null;
  decision_reasons?: string[] | null;
  close_reason?: string | null;
  regime?: string | null;
  // leverage/risk fields for open-position level lines (null on trades
  // opened before leverage tracking existed)
  leverage?: number | null;
  liquidation_price?: number | null;
  trailing_stop?: number | null;
};

/** Hitbox of one open-position level line, recorded during draw so the
 *  pointerup handler can turn a tap on a line into an edit-risk action. */
type PositionLineHit = { tradeId: number; y: number; x1: number; kind: "entry" | "tp" | "sl" | "liq" };

type Props = {
  symbol: string;
  candles: Candle[];
  timeframeMs: number;
  prediction: any;
  history: HistoryPoint[];
  trades: TradeMarker[];
  liquidityClusters: LiquidityCluster[];
  indicators: IndicatorId[];
  /** Bars the forecast cone projects ahead - timeframe-aware, matches the
   *  backend's prediction_horizon.bars for the selected interval. */
  forecastBars: number;
  showAiOverlay: boolean;
  showHistory: boolean;
  showPrice: boolean;
  showForecast: boolean;
  showUpperBand: boolean;
  showLowerBand: boolean;
  showCone: boolean;
  showCrosshair: boolean;
  autoScale: boolean;
  neon: boolean;
  chartStyle: "candles" | "line";
  livePrice: number | null;
  resetSignal: number;
  onExportRef?: (fn: () => void) => void;
  /** Tap/click on an open position's entry/TP/SL/liq line opens the
   *  edit-risk dialog for that position. */
  onEditPosition?: (tradeId: number) => void;
};
const AXIS_W = 68;
const AXIS_H = 24;
const FONT = "Inter, ui-sans-serif, system-ui, sans-serif";
// Past-AI-prediction line color - fixed orange so it stays distinct from the
// theme-driven cyan forecast line in every theme.
const HISTORY_ORANGE = "#ff9f43";
const HISTORY_ORANGE_RGB = "255, 159, 67";

type View = { end: number; bars: number; lockedY: [number, number] | null };

type Calc = {
  ema20?: Series;
  ema50?: Series;
  ema100?: Series;
  ema200?: Series;
  vwap?: Series;
  bollinger?: ReturnType<typeof bollinger>;
  supertrend?: ReturnType<typeof supertrend>;
  ichimoku?: ReturnType<typeof ichimoku>;
  rsi?: Series;
  macd?: ReturnType<typeof macd>;
  atr?: Series;
  adx?: ReturnType<typeof adx>;
  pivots?: ReturnType<typeof pivotPoints>;
  sr?: ReturnType<typeof supportResistance>;
  regression?: ReturnType<typeof regressionChannel>;
  volumeProfile?: ReturnType<typeof volumeProfile>;
  fvg?: ReturnType<typeof fairValueGaps>;
  orderBlocks?: ReturnType<typeof orderBlocks>;
  maxVolume: number;
};

type Cone = { predicted: number[]; upper: number[]; lower: number[] };

/** Backend point series -> plain price array, accepting only finite,
 *  positive prices on strictly-increasing timestamps. One bad point
 *  invalidates the series (never plot a partially-garbled cone). */
function sanitizeSeries(points: any): number[] | null {
  if (!Array.isArray(points) || points.length === 0) return null;
  const out: number[] = [];
  let prevT = -Infinity;
  for (const p of points) {
    const t = p?.time;
    const v = p?.price;
    if (typeof t !== "number" || !Number.isFinite(t) || t <= 0 || t <= prevT) return null;
    if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return null;
    prevT = t;
    out.push(v);
  }
  return out;
}

function buildCone(candlesArr: Candle[], prediction: any): Cone {
  const predicted: number[] = [];
  const upper: number[] = [];
  const lower: number[] = [];
  const last = candlesArr[candlesArr.length - 1];
  const lastClose = last?.close ?? 0;
  if (!lastClose) return { predicted, upper, lower };

  const forecast = prediction?.forecast;
  if (!forecast?.available) return { predicted, upper, lower };
  // Exact server-computed series only. Invalid or incomplete data is never
  // replaced by a locally invented target/cone.
  const sf = sanitizeSeries(forecast.median_path ?? forecast.forecast_points ?? prediction?.forecast_points);
  const su = sanitizeSeries(forecast.upper_band ?? forecast.upper_bound ?? prediction?.upper_band_points);
  const sl = sanitizeSeries(forecast.lower_band ?? forecast.lower_bound ?? prediction?.lower_band_points);
  if (sf && su && sl && sf.length === su.length && su.length === sl.length) {
    return { predicted: sf, upper: su, lower: sl };
  }
  return { predicted, upper, lower };
}

const TF_LABELS: Record<number, string> = {
  60_000: "1m",
  180_000: "3m",
  300_000: "5m",
  900_000: "15m",
  1_800_000: "30m",
  3_600_000: "1H",
  14_400_000: "4H",
  86_400_000: "1D",
  604_800_000: "1W",
};

function tfLabel(ms: number): string {
  return TF_LABELS[ms] ?? `${Math.round(ms / 60_000)}m`;
}

function niceStep(range: number, targetTicks: number): number {
  const raw = range / Math.max(1, targetTicks);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = norm >= 5 ? 10 : norm >= 2.5 ? 5 : norm >= 1 ? 2.5 : 1;
  return step * mag;
}

function fmtPrice(v: number): string {
  const abs = Math.abs(v);
  const digits = abs >= 10000 ? 0 : abs >= 100 ? 1 : abs >= 1 ? 2 : 5;
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

function fmtVol(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

const timeFmtIntraday = new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit" });
const timeFmtDay = new Intl.DateTimeFormat([], { month: "short", day: "numeric" });
const timeFmtFull = new Intl.DateTimeFormat([], {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

type Theme = {
  green: string;
  red: string;
  yellow: string;
  purple: string;
  blue: string;
  cyan: string;
  purpleRgb: string;
  blueRgb: string;
  cyanRgb: string;
  text: string;
  textDim: string;
  grid: string;
};

function readTheme(): Theme {
  const cs = getComputedStyle(document.documentElement);
  const get = (name: string, fallback: string) => (cs.getPropertyValue(name) || fallback).trim() || fallback;
  return {
    green: get("--c-green", "#00f5a0"),
    red: get("--c-red", "#ff5d73"),
    yellow: get("--c-yellow", "#ffd166"),
    purple: get("--c-purple", "#7c5cff"),
    blue: get("--c-blue", "#00a8ff"),
    cyan: get("--c-cyan", "#00f5d4"),
    purpleRgb: get("--c-purple-rgb", "124, 92, 255"),
    blueRgb: get("--c-blue-rgb", "0, 168, 255"),
    cyanRgb: get("--c-cyan-rgb", "0, 245, 212"),
    text: "#c8cbe0",
    textDim: "#8b90a8",
    grid: "rgba(255,255,255,0.05)",
  };
}

function ProChartCanvas(props: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Everything the render loop needs lives in refs so pointer/live updates
  // never trigger React renders.
  const propsRef = useRef(props);
  propsRef.current = props;

  const viewRef = useRef<View>({ end: 0, bars: 0, lockedY: null });
  const viewInitialized = useRef(false);
  const dirtyRef = useRef(true);
  const pointerRef = useRef<{ x: number; y: number; inside: boolean }>({ x: 0, y: 0, inside: false });
  const dragRef = useRef<{ active: boolean; lastX: number; pointers: Map<number, { x: number; y: number }>; pinchDist: number }>({
    active: false,
    lastX: 0,
    pointers: new Map(),
    pinchDist: 0,
  });
  const themeRef = useRef<{ theme: Theme; at: number }>({ theme: readTheme(), at: 0 });
  // Open-position line hitboxes, rewritten every draw; consumed by the
  // pointerup tap-to-edit handler.
  const posLinesRef = useRef<PositionLineHit[]>([]);

  // Smoothed animation state: the drawn price/cone chase their targets with
  // an exponential approach each frame, so 1s ticks morph instead of jump.
  const animRef = useRef<{ price: number; cone: Cone | null; yMin: number; yMax: number; initialized: boolean }>({
    price: 0,
    cone: null,
    yMin: 0,
    yMax: 1,
    initialized: false,
  });

  const { candles, timeframeMs, indicators } = props;
  const indicatorKey = useMemo(() => [...indicators].sort().join(","), [indicators]);

  const calc: Calc = useMemo(() => {
    const has = (id: IndicatorId) => indicators.includes(id);
    const out: Calc = { maxVolume: 0 };
    if (candles.length === 0) return out;
    const c = closes(candles);
    if (has("ema20")) out.ema20 = ema(c, 20);
    if (has("ema50")) out.ema50 = ema(c, 50);
    if (has("ema100")) out.ema100 = ema(c, 100);
    if (has("ema200")) out.ema200 = ema(c, 200);
    if (has("vwap")) out.vwap = vwap(candles);
    if (has("bollinger")) out.bollinger = bollinger(candles);
    if (has("supertrend")) out.supertrend = supertrend(candles);
    if (has("ichimoku")) out.ichimoku = ichimoku(candles);
    if (has("rsi")) out.rsi = rsi(candles);
    if (has("macd")) out.macd = macd(candles);
    if (has("atr")) out.atr = atr(candles);
    if (has("adx")) out.adx = adx(candles);
    if (has("pivots")) out.pivots = pivotPoints(candles);
    if (has("sr")) out.sr = supportResistance(candles);
    if (has("regression")) out.regression = regressionChannel(candles);
    if (has("volumeProfile")) out.volumeProfile = volumeProfile(candles);
    if (has("fvg")) out.fvg = fairValueGaps(candles);
    if (has("orderBlocks")) out.orderBlocks = orderBlocks(candles);
    if (has("volume")) {
      let m = 0;
      for (const cd of candles) if (cd.volume > m) m = cd.volume;
      out.maxVolume = m;
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, indicatorKey]);

  const coneTarget = useMemo(
    () => buildCone(candles, props.prediction),
    [candles, props.prediction, props.forecastBars]
  );

  const calcRef = useRef(calc);
  calcRef.current = calc;
  const coneTargetRef = useRef(coneTarget);
  coneTargetRef.current = coneTarget;

  // Mark dirty when any prop-driven input changes.
  useEffect(() => {
    dirtyRef.current = true;
  });

  // Initialize / re-anchor the view when the dataset identity changes
  // (symbol or timeframe switch = different first-candle time or bar count
  // far off). Regular 10s polls extend by 0-1 bars and keep the view.
  const datasetKey = `${props.symbol}|${timeframeMs}`;
  const prevDatasetKey = useRef("");
  useEffect(() => {
    const total = candles.length + props.forecastBars;
    if (!viewInitialized.current || prevDatasetKey.current !== datasetKey) {
      viewRef.current = { end: total, bars: Math.min(total, 160), lockedY: null };
      viewInitialized.current = candles.length > 0;
      prevDatasetKey.current = datasetKey;
      animRef.current.initialized = false;
      // Invalidate the smoothed y-domain along with the view: a locked
      // (auto-scale off) domain is only meaningful within the dataset it
      // was captured on. Without this, the first draw after a symbol
      // switch re-locks to the PREVIOUS symbol's finite anim.yMin/yMax -
      // e.g. BTC's ~60k range - and the new symbol's candles (ETH at
      // ~2k) land far outside the visible band: a blank price chart with
      // every stat still rendering.
      animRef.current.yMin = NaN;
      animRef.current.yMax = NaN;
    } else {
      // keep right edge pinned if the user was already at the right edge
      const v = viewRef.current;
      if (v.end >= total - 2) v.end = total;
      if (v.end > total) v.end = total;
    }
    dirtyRef.current = true;
  }, [datasetKey, candles.length]);

  useEffect(() => {
    viewRef.current = { end: candles.length + props.forecastBars, bars: Math.min(candles.length + props.forecastBars, 160), lockedY: null };
    animRef.current.initialized = false;
    dirtyRef.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.resetSignal]);

  // ---------------------------------------------------------------- render
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let running = true;
    let lastSize = { w: 0, h: 0, dpr: 1 };

    const resize = () => {
      // clientWidth/Height exclude the wrap's border - measuring the
      // border-inclusive rect and writing it back to the canvas creates a
      // 2px/frame ResizeObserver feedback loop.
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = Math.max(50, wrap.clientWidth);
      const h = Math.max(50, wrap.clientHeight);
      if (w !== lastSize.w || h !== lastSize.h || dpr !== lastSize.dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
        lastSize = { w, h, dpr };
        dirtyRef.current = true;
      }
    };

    const ro = new ResizeObserver(() => {
      resize();
    });
    ro.observe(wrap);
    resize();

    const frame = () => {
      if (!running) return;
      raf = requestAnimationFrame(frame);

      const p = propsRef.current;
      const cs = p.candles;
      const anim = animRef.current;

      // ---- animation settling (runs even when not otherwise dirty)
      const lastClose = cs.length ? cs[cs.length - 1].close : 0;
      const priceTarget = typeof p.livePrice === "number" && p.livePrice > 0 ? p.livePrice : lastClose;
      const target = coneTargetRef.current;

      if (!anim.initialized && cs.length) {
        anim.price = priceTarget;
        anim.cone = {
          predicted: [...target.predicted],
          upper: [...target.upper],
          lower: [...target.lower],
        };
        anim.initialized = true;
        dirtyRef.current = true;
      }

      let animating = false;
      if (anim.initialized) {
        const approach = (cur: number, tgt: number, k: number) => {
          const next = cur + (tgt - cur) * k;
          return Math.abs(next - tgt) < Math.abs(tgt) * 1e-7 + 1e-7 ? tgt : next;
        };
        const prevPrice = anim.price;
        anim.price = approach(anim.price, priceTarget, 0.18);
        if (anim.price !== prevPrice) animating = true;

        if (anim.cone && target.predicted.length === anim.cone.predicted.length) {
          for (let i = 0; i < target.predicted.length; i++) {
            const np = approach(anim.cone.predicted[i], target.predicted[i], 0.14);
            const nu = approach(anim.cone.upper[i], target.upper[i], 0.14);
            const nl = approach(anim.cone.lower[i], target.lower[i], 0.14);
            if (np !== anim.cone.predicted[i] || nu !== anim.cone.upper[i] || nl !== anim.cone.lower[i]) animating = true;
            anim.cone.predicted[i] = np;
            anim.cone.upper[i] = nu;
            anim.cone.lower[i] = nl;
          }
        } else if (anim.cone !== null || target.predicted.length) {
          anim.cone = { predicted: [...target.predicted], upper: [...target.upper], lower: [...target.lower] };
          animating = true;
        }
      }

      if (!dirtyRef.current && !animating) return; // idle: skip the frame entirely

      // y-domain smoothing happens inside draw(); it reports back whether
      // it is still converging so we keep animating.
      const stillMoving = draw(ctx, lastSize.w, lastSize.h, lastSize.dpr);
      dirtyRef.current = stillMoving;
    };

    // ------------------------------------------------------------- drawing
    const draw = (g: CanvasRenderingContext2D, W: number, H: number, dpr: number): boolean => {
      const p = propsRef.current;
      const cs = p.candles;
      const anim = animRef.current;
      const c = calcRef.current;

      if (Date.now() - themeRef.current.at > 1000) {
        themeRef.current = { theme: readTheme(), at: Date.now() };
      }
      const T = themeRef.current.theme;

      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);
      g.font = `10px ${FONT}`;

      if (cs.length === 0) {
        g.fillStyle = T.textDim;
        g.textAlign = "center";
        g.fillText(`No ${p.symbol} ${tfLabel(p.timeframeMs)} candle data loaded yet — waiting for market data…`, W / 2, H / 2);
        return false;
      }

      // ---- layout
      const oscIds: IndicatorId[] = (["rsi", "macd", "atr", "adx"] as IndicatorId[]).filter((id) =>
        p.indicators.includes(id)
      );
      const oscH = oscIds.length ? Math.max(46, Math.min(72, Math.floor((H * 0.42) / oscIds.length))) : 0;
      const mainBottom = H - AXIS_H - oscH * oscIds.length;
      const plotL = 0;
      const plotR = W - AXIS_W;
      const plotW = plotR - plotL;

      const view = viewRef.current;
      const total = cs.length + p.forecastBars;
      view.bars = Math.max(20, Math.min(view.bars, Math.max(40, total * 1.2)));
      view.end = Math.max(Math.min(view.end, total + view.bars * 0.25), 10);
      const barW = plotW / view.bars;
      const viewStart = view.end - view.bars;
      const xAt = (idx: number) => plotL + (idx - viewStart + 0.5) * barW;
      const idxAtX = (x: number) => viewStart + (x - plotL) / barW - 0.5;

      const firstVisible = Math.max(0, Math.floor(viewStart));
      const lastVisibleData = Math.min(cs.length - 1, Math.ceil(view.end));

      // ---- y domain
      let yMinT = Infinity;
      let yMaxT = -Infinity;
      const scan = (v: number | null | undefined) => {
        if (typeof v === "number" && Number.isFinite(v)) {
          if (v < yMinT) yMinT = v;
          if (v > yMaxT) yMaxT = v;
        }
      };
      for (let i = firstVisible; i <= lastVisibleData; i++) {
        scan(cs[i].low);
        scan(cs[i].high);
      }
      // y-domain includes only the overlays actually being drawn, and only
      // finite values (scan() filters) - a hidden or invalid series can
      // never distort the axis
      if (anim.cone) {
        for (let i = 0; i < anim.cone.predicted.length; i++) {
          const idx = cs.length + i;
          if (idx < viewStart || idx > view.end) continue;
          if (p.showUpperBand || p.showCone) scan(anim.cone.upper[i]);
          if (p.showLowerBand || p.showCone) scan(anim.cone.lower[i]);
          if (p.showForecast) scan(anim.cone.predicted[i]);
        }
      }
      if (p.showAiOverlay && p.prediction?.forecast?.trade_actionable) {
        scan(p.prediction.target);
        scan(p.prediction.stop);
      }
      if (!Number.isFinite(yMinT) || !Number.isFinite(yMaxT)) {
        yMinT = 0;
        yMaxT = 1;
      }
      const padY = (yMaxT - yMinT) * 0.08 || yMaxT * 0.01 || 1;
      yMinT -= padY;
      yMaxT += padY;

      let stillMoving = false;
      const animDomainValid = Number.isFinite(anim.yMin) && Number.isFinite(anim.yMax) && anim.yMax > anim.yMin;
      if (!p.autoScale) {
        // freeze the domain at the moment auto-scale was switched off
        if (!view.lockedY) view.lockedY = animDomainValid ? [anim.yMin, anim.yMax] : [yMinT, yMaxT];
      } else {
        // smooth domain transitions to avoid vertical jumps on live updates
        if (!animDomainValid || !anim.initialized) {
          anim.yMin = yMinT;
          anim.yMax = yMaxT;
        } else {
          const range = yMaxT - yMinT;
          const k = 0.25;
          const nMin = anim.yMin + (yMinT - anim.yMin) * k;
          const nMax = anim.yMax + (yMaxT - anim.yMax) * k;
          anim.yMin = Math.abs(nMin - yMinT) < range * 0.001 ? yMinT : nMin;
          anim.yMax = Math.abs(nMax - yMaxT) < range * 0.001 ? yMaxT : nMax;
          if (anim.yMin !== yMinT || anim.yMax !== yMaxT) stillMoving = true;
        }
        view.lockedY = null;
      }
      let [yMin, yMax] = view.lockedY ?? [anim.yMin, anim.yMax];
      // Last-resort guard: whatever the source (locked or animated), a
      // non-finite or collapsed domain would map every price to ±Infinity
      // and silently draw nothing - fall back to the domain computed from
      // the actual visible candles this frame.
      if (!Number.isFinite(yMin) || !Number.isFinite(yMax) || yMax <= yMin) {
        yMin = yMinT;
        yMax = yMaxT;
      }
      const volTop = p.indicators.includes("volume") ? mainBottom - Math.min(70, mainBottom * 0.16) : mainBottom;
      const priceBottom = volTop - 2;
      const yAt = (v: number) => 8 + ((yMax - v) / (yMax - yMin)) * (priceBottom - 16);
      const vAtY = (y: number) => yMax - ((y - 8) / (priceBottom - 16)) * (yMax - yMin);

      const clipMain = () => {
        g.save();
        g.beginPath();
        g.rect(plotL, 0, plotW, mainBottom);
        g.clip();
      };

      // ---- grid + y axis
      g.strokeStyle = T.grid;
      g.lineWidth = 1;
      g.textAlign = "left";
      const step = niceStep(yMax - yMin, 6);
      const gridStart = Math.ceil(yMin / step) * step;
      for (let v = gridStart; v <= yMax; v += step) {
        const y = yAt(v);
        if (y < 6 || y > priceBottom) continue;
        g.beginPath();
        g.moveTo(plotL, y);
        g.lineTo(plotR, y);
        g.stroke();
        g.fillStyle = T.textDim;
        g.fillText(fmtPrice(v), plotR + 6, y + 3);
      }

      // ---- x axis ticks
      const labelEvery = Math.max(1, Math.ceil(view.bars / Math.max(2, plotW / 92)));
      const isIntraday = p.timeframeMs < 86_400_000;
      g.textAlign = "center";
      for (let i = Math.ceil(viewStart / labelEvery) * labelEvery; i <= view.end; i += labelEvery) {
        const x = xAt(i);
        if (x < plotL || x > plotR) continue;
        const t = i < cs.length ? cs[Math.max(0, Math.floor(i))].time : cs[cs.length - 1].time + (i - cs.length + 1) * p.timeframeMs;
        g.strokeStyle = T.grid;
        g.beginPath();
        g.moveTo(x, 0);
        g.lineTo(x, H - AXIS_H);
        g.stroke();
        g.fillStyle = T.textDim;
        const d = new Date(t);
        g.fillText(isIntraday ? timeFmtIntraday.format(d) : timeFmtDay.format(d), x, H - AXIS_H + 14);
      }

      const nowIdx = cs.length - 1;
      const nowX = xAt(nowIdx);

      // ================= main pane content (clipped) =================
      clipMain();

      // volume profile (right-anchored horizontal histogram)
      if (c.volumeProfile && c.volumeProfile.length) {
        let maxV = 0;
        for (const b of c.volumeProfile) if (b.volume > maxV) maxV = b.volume;
        const maxW = plotW * 0.18;
        for (const b of c.volumeProfile) {
          const y1 = yAt(b.priceHigh);
          const y2 = yAt(b.priceLow);
          const w = maxV > 0 ? (b.volume / maxV) * maxW : 0;
          g.fillStyle = `rgba(${T.blueRgb}, 0.10)`;
          g.fillRect(plotR - w, Math.min(y1, y2), w, Math.abs(y2 - y1) - 1);
        }
      }

      // liquidity heatmap bands
      if (p.indicators.includes("liquidity") && p.liquidityClusters.length) {
        for (const cl of p.liquidityClusters) {
          const intensity = Math.max(0, Math.min(1, cl.intensity));
          if (intensity < 0.15) continue;
          const y = yAt(cl.price);
          if (y < 0 || y > priceBottom) continue;
          const isLong = cl.dominant_side !== "SHORT";
          const rgb = isLong ? "0, 245, 160" : "255, 93, 115";
          const bandH = Math.max(3, priceBottom * 0.012 + intensity * 6);
          const grad = g.createLinearGradient(plotL, 0, plotR, 0);
          grad.addColorStop(0, `rgba(${rgb}, 0)`);
          grad.addColorStop(0.65, `rgba(${rgb}, ${0.05 + intensity * 0.16})`);
          grad.addColorStop(1, `rgba(${rgb}, ${0.08 + intensity * 0.2})`);
          g.fillStyle = grad;
          g.fillRect(plotL, y - bandH / 2, plotW, bandH);
        }
      }

      // FVG + order block zones
      const drawZone = (startIdx: number, low: number, high: number, side: "bull" | "bear", label: string) => {
        const x = xAt(startIdx);
        if (x > plotR) return;
        const y1 = yAt(high);
        const y2 = yAt(low);
        const rgb = side === "bull" ? "0, 245, 160" : "255, 93, 115";
        g.fillStyle = `rgba(${rgb}, 0.07)`;
        g.fillRect(Math.max(plotL, x), y1, plotR - Math.max(plotL, x), y2 - y1);
        g.strokeStyle = `rgba(${rgb}, 0.25)`;
        g.strokeRect(Math.max(plotL, x), y1, plotR - Math.max(plotL, x), y2 - y1);
        g.fillStyle = `rgba(${rgb}, 0.7)`;
        g.textAlign = "left";
        g.fillText(label, Math.max(plotL, x) + 4, y1 + 10);
      };
      if (c.fvg) for (const z of c.fvg) drawZone(z.startIdx, z.low, z.high, z.side, "FVG");
      if (c.orderBlocks) for (const z of c.orderBlocks) drawZone(z.startIdx, z.low, z.high, z.side, "OB");

      // regression channel
      if (c.regression) {
        const r = c.regression;
        const x1 = xAt(r.startIdx);
        const x2 = xAt(r.endIdx);
        const draws: Array<[number, string, number]> = [
          [0, `rgba(${T.cyanRgb}, 0.55)`, 1],
          [r.halfWidth, `rgba(${T.cyanRgb}, 0.3)`, 1],
          [-r.halfWidth, `rgba(${T.cyanRgb}, 0.3)`, 1],
        ];
        for (const [off, color, lw] of draws) {
          g.strokeStyle = color;
          g.lineWidth = lw;
          g.setLineDash(off === 0 ? [] : [5, 4]);
          g.beginPath();
          g.moveTo(x1, yAt(r.valueAt(r.startIdx) + off));
          g.lineTo(x2, yAt(r.valueAt(r.endIdx) + off));
          g.stroke();
        }
        g.setLineDash([]);
        g.fillStyle = `rgba(${T.cyanRgb}, 0.05)`;
        g.beginPath();
        g.moveTo(x1, yAt(r.valueAt(r.startIdx) + r.halfWidth));
        g.lineTo(x2, yAt(r.valueAt(r.endIdx) + r.halfWidth));
        g.lineTo(x2, yAt(r.valueAt(r.endIdx) - r.halfWidth));
        g.lineTo(x1, yAt(r.valueAt(r.startIdx) - r.halfWidth));
        g.closePath();
        g.fill();
      }

      // ichimoku cloud
      if (c.ichimoku) {
        const ich = c.ichimoku;
        g.beginPath();
        let started = false;
        for (let i = firstVisible; i < Math.min(ich.senkouA.length, view.end + 1); i++) {
          const a = ich.senkouA[i];
          if (Number.isNaN(a)) continue;
          const x = xAt(i);
          if (!started) {
            g.moveTo(x, yAt(a));
            started = true;
          } else g.lineTo(x, yAt(a));
        }
        for (let i = Math.min(ich.senkouB.length, Math.ceil(view.end)) - 1; i >= firstVisible; i--) {
          const b = ich.senkouB[i];
          if (Number.isNaN(b)) continue;
          g.lineTo(xAt(i), yAt(b));
        }
        g.closePath();
        g.fillStyle = `rgba(${T.purpleRgb}, 0.08)`;
        g.fill();
        const lineSeries: Array<[Series, string]> = [
          [ich.tenkan, `rgba(${T.cyanRgb}, 0.5)`],
          [ich.kijun, `rgba(${T.purpleRgb}, 0.5)`],
        ];
        for (const [s, color] of lineSeries) drawSeries(g, s, firstVisible, Math.min(cs.length - 1, Math.ceil(view.end)), xAt, yAt, color, 1);
      }

      // bollinger
      if (c.bollinger) {
        const bb = c.bollinger;
        g.beginPath();
        let started = false;
        const lastI = Math.min(cs.length - 1, Math.ceil(view.end));
        for (let i = firstVisible; i <= lastI; i++) {
          if (Number.isNaN(bb.upper[i])) continue;
          const x = xAt(i);
          if (!started) {
            g.moveTo(x, yAt(bb.upper[i]));
            started = true;
          } else g.lineTo(x, yAt(bb.upper[i]));
        }
        for (let i = lastI; i >= firstVisible; i--) {
          if (Number.isNaN(bb.lower[i])) continue;
          g.lineTo(xAt(i), yAt(bb.lower[i]));
        }
        g.closePath();
        g.fillStyle = `rgba(${T.blueRgb}, 0.06)`;
        g.fill();
        drawSeries(g, bb.upper, firstVisible, lastI, xAt, yAt, `rgba(${T.blueRgb}, 0.5)`, 1);
        drawSeries(g, bb.mid, firstVisible, lastI, xAt, yAt, `rgba(${T.blueRgb}, 0.35)`, 1, [4, 4]);
        drawSeries(g, bb.lower, firstVisible, lastI, xAt, yAt, `rgba(${T.blueRgb}, 0.5)`, 1);
      }

      // ---- candles / line (live close uses the smoothed price)
      const liveClose = anim.price || cs[cs.length - 1].close;
      if (p.chartStyle === "candles") {
        const bodyW = Math.max(1, Math.min(barW * 0.7, 14));
        for (let i = firstVisible; i <= lastVisibleData; i++) {
          const cd = cs[i];
          const isLast = i === cs.length - 1;
          const close = isLast ? liveClose : cd.close;
          const high = isLast ? Math.max(cd.high, close) : cd.high;
          const low = isLast ? Math.min(cd.low, close) : cd.low;
          const up = close >= cd.open;
          const x = xAt(i);
          g.strokeStyle = up ? T.green : T.red;
          g.fillStyle = up ? `rgba(0, 245, 160, 0.9)` : `rgba(255, 93, 115, 0.9)`;
          g.lineWidth = 1;
          g.beginPath();
          g.moveTo(x, yAt(high));
          g.lineTo(x, yAt(low));
          g.stroke();
          const yO = yAt(cd.open);
          const yC = yAt(close);
          const top = Math.min(yO, yC);
          const hgt = Math.max(1, Math.abs(yC - yO));
          g.fillRect(x - bodyW / 2, top, bodyW, hgt);
        }
      } else {
        g.save();
        if (p.neon) {
          g.shadowColor = `rgba(${T.purpleRgb}, 0.6)`;
          g.shadowBlur = 8;
        }
        g.strokeStyle = T.purple;
        g.lineWidth = 2.25;
        g.beginPath();
        let started = false;
        for (let i = firstVisible; i <= lastVisibleData; i++) {
          const close = i === cs.length - 1 ? liveClose : cs[i].close;
          const x = xAt(i);
          if (!started) {
            g.moveTo(x, yAt(close));
            started = true;
          } else g.lineTo(x, yAt(close));
        }
        g.stroke();
        g.restore();
        // soft area fill under the line
        if (started) {
          const gradient = g.createLinearGradient(0, 0, 0, priceBottom);
          gradient.addColorStop(0, `rgba(${T.purpleRgb}, 0.16)`);
          gradient.addColorStop(1, `rgba(${T.purpleRgb}, 0)`);
          g.fillStyle = gradient;
          g.lineTo(xAt(lastVisibleData), priceBottom);
          g.lineTo(xAt(firstVisible), priceBottom);
          g.closePath();
          g.fill();
        }
      }

      // ---- volume bars
      if (p.indicators.includes("volume") && c.maxVolume > 0) {
        const volH = mainBottom - volTop;
        const bodyW = Math.max(1, Math.min(barW * 0.6, 12));
        for (let i = firstVisible; i <= lastVisibleData; i++) {
          const cd = cs[i];
          const up = cd.close >= cd.open;
          const h = (cd.volume / c.maxVolume) * (volH - 6);
          g.fillStyle = up ? "rgba(0, 245, 160, 0.28)" : "rgba(255, 93, 115, 0.28)";
          g.fillRect(xAt(i) - bodyW / 2, mainBottom - h, bodyW, h);
        }
        g.fillStyle = T.textDim;
        g.textAlign = "left";
        g.fillText("VOL", plotL + 6, volTop + 10);
      }

      // ---- moving average style overlays
      const lastI = lastVisibleData;
      if (c.ema20) drawSeries(g, c.ema20, firstVisible, lastI, xAt, yAt, T.cyan, 1.25);
      if (c.ema50) drawSeries(g, c.ema50, firstVisible, lastI, xAt, yAt, T.blue, 1.25);
      if (c.ema100) drawSeries(g, c.ema100, firstVisible, lastI, xAt, yAt, T.yellow, 1.25);
      if (c.ema200) drawSeries(g, c.ema200, firstVisible, lastI, xAt, yAt, T.purple, 1.5);
      if (c.vwap) drawSeries(g, c.vwap, firstVisible, lastI, xAt, yAt, `rgba(255,255,255,0.55)`, 1.25, [6, 3]);
      if (c.supertrend) {
        // color by direction, split segments on flips
        const st = c.supertrend;
        for (let i = firstVisible + 1; i <= lastI; i++) {
          if (Number.isNaN(st.line[i]) || Number.isNaN(st.line[i - 1])) continue;
          if (st.direction[i] !== st.direction[i - 1]) continue;
          g.strokeStyle = st.direction[i] === 1 ? T.green : T.red;
          g.lineWidth = 1.5;
          g.beginPath();
          g.moveTo(xAt(i - 1), yAt(st.line[i - 1]));
          g.lineTo(xAt(i), yAt(st.line[i]));
          g.stroke();
        }
      }

      // ---- S/R + pivots
      const hLine = (price: number, color: string, label: string, dash: number[] = [6, 4]) => {
        const y = yAt(price);
        if (y < 0 || y > priceBottom) return;
        g.strokeStyle = color;
        g.lineWidth = 1;
        g.setLineDash(dash);
        g.beginPath();
        g.moveTo(plotL, y);
        g.lineTo(plotR, y);
        g.stroke();
        g.setLineDash([]);
        g.fillStyle = color;
        g.textAlign = "left";
        g.fillText(label, plotL + 6, y - 3);
      };
      if (c.sr) {
        for (const lv of c.sr) {
          hLine(
            lv.price,
            lv.kind === "support" ? "rgba(0, 245, 160, 0.4)" : "rgba(255, 93, 115, 0.4)",
            `${lv.kind === "support" ? "S" : "R"} · ${lv.touches}x`
          );
        }
      }
      if (c.pivots) {
        const pv = c.pivots;
        hLine(pv.p, "rgba(255, 209, 102, 0.55)", "P", [2, 3]);
        hLine(pv.r1, "rgba(255, 93, 115, 0.35)", "R1", [2, 3]);
        hLine(pv.r2, "rgba(255, 93, 115, 0.28)", "R2", [2, 3]);
        hLine(pv.s1, "rgba(0, 245, 160, 0.35)", "S1", [2, 3]);
        hLine(pv.s2, "rgba(0, 245, 160, 0.28)", "S2", [2, 3]);
      }

      // ---- historical AI prediction line (real stored predictions)
      const t0 = cs[0].time;
      const idxForTime = (t: number) => (t - t0) / p.timeframeMs;
      let hoveredHist: { pt: HistoryPoint; x: number; y: number } | null = null;
      if (p.showHistory && p.history.length) {
        g.save();
        if (p.neon) {
          g.shadowColor = `rgba(${HISTORY_ORANGE_RGB}, 0.5)`;
          g.shadowBlur = 6;
        }
        g.strokeStyle = `rgba(${HISTORY_ORANGE_RGB}, 0.85)`;
        g.lineWidth = 1.5;
        g.setLineDash([2, 3]);
        g.beginPath();
        let started = false;
        const drawable: Array<{ pt: HistoryPoint; x: number; y: number }> = [];
        for (const pt of p.history) {
          if (typeof pt.predicted_price !== "number") continue;
          const idx = idxForTime(pt.timestamp);
          if (idx < viewStart - 2 || idx > view.end + 2 || idx > nowIdx + 0.5) continue;
          const x = xAt(idx);
          const y = yAt(pt.predicted_price);
          drawable.push({ pt, x, y });
          if (!started) {
            g.moveTo(x, y);
            started = true;
          } else g.lineTo(x, y);
        }
        g.stroke();
        g.setLineDash([]);
        g.restore();

        // dots colored by outcome + hover detection. Unresolved splits into
        // two shades - NO_TRADE (grey) vs still-PENDING (yellow) - since
        // those mean different things even though both bucket to
        // "unresolved" for the stat row.
        const ptr = pointerRef.current;
        for (const d of drawable) {
          const oc = d.pt.outcome;
          const bucket = outcomeBucket(oc);
          const color =
            bucket === "correct"
              ? T.green
              : bucket === "wrong"
              ? T.red
              : oc === "NO_TRADE"
              ? T.textDim
              : T.yellow;
          g.fillStyle = color;
          g.beginPath();
          g.arc(d.x, d.y, 2.5, 0, Math.PI * 2);
          g.fill();
          if (ptr.inside && Math.abs(ptr.x - d.x) < 9 && Math.abs(ptr.y - d.y) < 9) {
            hoveredHist = d;
          }
        }
        if (hoveredHist) {
          g.strokeStyle = "#fff";
          g.lineWidth = 1.5;
          g.beginPath();
          g.arc(hoveredHist.x, hoveredHist.y, 5, 0, Math.PI * 2);
          g.stroke();
        }
      }

      // ---- paper trade markers: entry / exit / SL / TP, each hoverable for
      // the decision provenance stored on the trade (model, strategy,
      // confidence, reasons, result). Only real recorded trades are drawn.
      type TradeHit = { t: TradeMarker; x: number; y: number; kind: "entry" | "exit" | "sl" | "tp" };
      let hoveredTrade: TradeHit | null = null;
      if (p.trades && p.trades.length) {
        const ptrT = pointerRef.current;
        const hits: TradeHit[] = [];
        const xyFor = (ms: number, price: number | null | undefined): [number, number] | null => {
          if (!Number.isFinite(ms) || typeof price !== "number" || !Number.isFinite(price)) return null;
          const idx = idxForTime(ms);
          if (idx < viewStart - 2 || idx > view.end + 2) return null;
          const y = yAt(price);
          if (y < 0 || y > priceBottom) return null;
          return [xAt(idx), y];
        };
        const consider = (t: TradeMarker, x: number, y: number, kind: TradeHit["kind"]) => {
          if (ptrT.inside && Math.abs(ptrT.x - x) < 9 && Math.abs(ptrT.y - y) < 9) hits.push({ t, x, y, kind });
        };

        for (const t of p.trades) {
          const openMs = t.opened_at ? Date.parse(t.opened_at) : NaN;
          const closeMs = t.closed_at ? Date.parse(t.closed_at) : NaN;
          const long = t.side === "LONG";
          const won = (t.pnl ?? 0) >= 0;

          const entryXY = xyFor(openMs, t.entry);
          const exitXY = t.status === "CLOSED" ? xyFor(closeMs, t.exit) : null;

          // faint connector from entry to exit for closed trades
          if (entryXY && exitXY) {
            g.strokeStyle = won ? "rgba(0, 245, 160, 0.35)" : "rgba(255, 93, 115, 0.35)";
            g.lineWidth = 1;
            g.setLineDash([3, 3]);
            g.beginPath();
            g.moveTo(entryXY[0], entryXY[1]);
            g.lineTo(exitXY[0], exitXY[1]);
            g.stroke();
            g.setLineDash([]);
          }

          if (entryXY) {
            const [x, y] = entryXY;
            g.fillStyle = long ? T.green : T.red;
            g.strokeStyle = "#0b0e1d";
            g.lineWidth = 1;
            g.beginPath();
            if (long) {
              g.moveTo(x, y - 5.5);
              g.lineTo(x - 5, y + 4);
              g.lineTo(x + 5, y + 4);
            } else {
              g.moveTo(x, y + 5.5);
              g.lineTo(x - 5, y - 4);
              g.lineTo(x + 5, y - 4);
            }
            g.closePath();
            g.fill();
            g.stroke();
            consider(t, x, y, "entry");
          }

          if (exitXY) {
            const [x, y] = exitXY;
            g.fillStyle = won ? T.green : T.red;
            g.strokeStyle = "#0b0e1d";
            g.lineWidth = 1;
            g.beginPath();
            g.rect(x - 4, y - 4, 8, 8);
            g.fill();
            g.stroke();
            consider(t, x, y, "exit");
          }

          // SL/TP shown while the position is live (they are still armed)
          if (t.status === "OPEN") {
            const diamond = (x: number, y: number, color: string) => {
              g.strokeStyle = color;
              g.lineWidth = 1.5;
              g.beginPath();
              g.moveTo(x, y - 4.5);
              g.lineTo(x + 4.5, y);
              g.lineTo(x, y + 4.5);
              g.lineTo(x - 4.5, y);
              g.closePath();
              g.stroke();
            };
            const slXY = xyFor(openMs, t.sl);
            const tpXY = xyFor(openMs, t.tp);
            if (slXY) {
              diamond(slXY[0], slXY[1], T.red);
              consider(t, slXY[0], slXY[1], "sl");
            }
            if (tpXY) {
              diamond(tpXY[0], tpXY[1], T.green);
              consider(t, tpXY[0], tpXY[1], "tp");
            }
          }
        }

        hoveredTrade = hits.length ? hits[hits.length - 1] : null;
        if (hoveredTrade) {
          g.strokeStyle = "#fff";
          g.lineWidth = 1.5;
          g.beginPath();
          g.arc(hoveredTrade.x, hoveredTrade.y, 7, 0, Math.PI * 2);
          g.stroke();
        }
      }

      // ---- open-position level lines: entry / TP / SL / estimated
      // liquidation drawn across the plot from the position's open time,
      // labeled at the line start. Tap/click a line to edit risk (hitboxes
      // recorded for the pointerup handler). Liquidation is the paper
      // engine's ESTIMATE - labeled as such.
      posLinesRef.current = [];
      if (p.trades && p.trades.length) {
        for (const t of p.trades) {
          if (t.status !== "OPEN") continue;
          const openMs = t.opened_at ? Date.parse(t.opened_at) : NaN;
          let x1 = plotL;
          if (Number.isFinite(openMs)) {
            const idx = idxForTime(openMs);
            x1 = Math.max(plotL, Math.min(plotR - 60, xAt(idx)));
          }
          const posLine = (
            price: number | null | undefined,
            color: string,
            label: string,
            kind: PositionLineHit["kind"]
          ) => {
            if (typeof price !== "number" || !Number.isFinite(price)) return;
            const y = yAt(price);
            if (y < 0 || y > priceBottom) return;
            g.strokeStyle = color;
            g.lineWidth = 1.25;
            g.setLineDash([6, 4]);
            g.beginPath();
            g.moveTo(x1, y);
            g.lineTo(plotR, y);
            g.stroke();
            g.setLineDash([]);
            const text = `${label} ${fmtPrice(price)}`;
            g.font = `600 9px ${FONT}`;
            const w = g.measureText(text).width + 10;
            g.fillStyle = "rgba(13, 16, 33, 0.85)";
            g.beginPath();
            g.roundRect(x1 + 2, y - 8, w, 14, 4);
            g.fill();
            g.fillStyle = color;
            g.textAlign = "left";
            g.fillText(text, x1 + 7, y + 3);
            g.font = `10px ${FONT}`;
            posLinesRef.current.push({ tradeId: t.id, y, x1, kind });
          };

          const sideTag = t.side === "LONG" ? "▲" : "▼";
          posLine(t.entry, t.side === "LONG" ? T.green : T.red, `${sideTag} ENTRY`, "entry");
          posLine(t.tp, T.green, "TP", "tp");
          posLine(t.sl, T.red, t.trailing_stop ? "TRAIL SL" : "SL", "sl");
          posLine(t.liquidation_price, HISTORY_ORANGE, "EST. LIQ", "liq");
        }
      }

      // ---- forecast cone + future prediction line
      if (anim.cone && anim.cone.predicted.length) {
        const cone = anim.cone;
        const coneX = (i: number) => xAt(cs.length - 1 + (i + 1));
        if (p.showCone) {
          g.beginPath();
          g.moveTo(nowX, yAt(liveClose));
          for (let i = 0; i < cone.upper.length; i++) g.lineTo(coneX(i), yAt(cone.upper[i]));
          for (let i = cone.lower.length - 1; i >= 0; i--) g.lineTo(coneX(i), yAt(cone.lower[i]));
          g.closePath();
          const grad = g.createLinearGradient(nowX, 0, Math.min(plotR, coneX(cone.upper.length - 1)), 0);
          grad.addColorStop(0, `rgba(${T.blueRgb}, 0.22)`);
          grad.addColorStop(1, `rgba(${T.blueRgb}, 0.04)`);
          g.fillStyle = grad;
          g.fill();
        }

        if (p.showUpperBand) {
          g.strokeStyle = T.green;
          g.lineWidth = 1.5;
          g.setLineDash([7, 5]);
          g.beginPath();
          g.moveTo(nowX, yAt(liveClose));
          for (let i = 0; i < cone.upper.length; i++) g.lineTo(coneX(i), yAt(cone.upper[i]));
          g.stroke();
          g.setLineDash([]);
        }
        if (p.showLowerBand) {
          g.strokeStyle = T.red;
          g.lineWidth = 1.5;
          g.setLineDash([7, 5]);
          g.beginPath();
          g.moveTo(nowX, yAt(liveClose));
          for (let i = 0; i < cone.lower.length; i++) g.lineTo(coneX(i), yAt(cone.lower[i]));
          g.stroke();
          g.setLineDash([]);
        }

        if (p.showForecast) {
          g.save();
          if (p.neon) {
            g.shadowColor = `rgba(${T.cyanRgb}, 0.75)`;
            g.shadowBlur = 9;
          }
          g.strokeStyle = T.cyan;
          g.lineWidth = 2.5;
          g.beginPath();
          g.moveTo(nowX, yAt(liveClose));
          for (let i = 0; i < cone.predicted.length; i++) g.lineTo(coneX(i), yAt(cone.predicted[i]));
          g.stroke();
          g.restore();
        }
      }

      // ---- AI overlay: entry / target / stop + info card
      if (p.showAiOverlay && p.prediction?.forecast?.trade_actionable) {
        const pr = p.prediction;
        const entry = typeof pr.price === "number" ? pr.price : liveClose;
        const coneEndX = Math.min(plotR, xAt(cs.length - 1 + p.forecastBars));
        const zoneL = Math.max(plotL, nowX);
        if (typeof pr.target === "number" && typeof pr.stop === "number") {
          // risk / reward shading
          g.fillStyle = "rgba(0, 245, 160, 0.07)";
          g.fillRect(zoneL, Math.min(yAt(entry), yAt(pr.target)), coneEndX - zoneL, Math.abs(yAt(pr.target) - yAt(entry)));
          g.fillStyle = "rgba(255, 93, 115, 0.07)";
          g.fillRect(zoneL, Math.min(yAt(entry), yAt(pr.stop)), coneEndX - zoneL, Math.abs(yAt(pr.stop) - yAt(entry)));

          const levelLine = (price: number, color: string, label: string) => {
            const y = yAt(price);
            if (y < 0 || y > priceBottom) return;
            g.strokeStyle = color;
            g.lineWidth = 1.25;
            g.setLineDash([8, 4]);
            g.beginPath();
            g.moveTo(zoneL, y);
            g.lineTo(plotR, y);
            g.stroke();
            g.setLineDash([]);
            const text = `${label} ${fmtPrice(price)}`;
            g.font = `600 10px ${FONT}`;
            const w = g.measureText(text).width + 12;
            g.fillStyle = color;
            g.beginPath();
            g.roundRect(plotR - w - 4, y - 9, w, 16, 4);
            g.fill();
            g.fillStyle = "#0b0e1d";
            g.textAlign = "left";
            g.fillText(text, plotR - w + 2, y + 3);
            g.font = `10px ${FONT}`;
          };
          levelLine(pr.target, T.green, "TP");
          levelLine(pr.stop, T.red, "SL");
          levelLine(entry, T.yellow, "ENTRY");
        }
      }

      // ---- now line + live price marker
      g.strokeStyle = "rgba(255,255,255,0.25)";
      g.setLineDash([4, 4]);
      g.beginPath();
      g.moveTo(nowX, 0);
      g.lineTo(nowX, mainBottom);
      g.stroke();
      g.setLineDash([]);
      g.fillStyle = T.textDim;
      g.textAlign = "center";
      g.fillText("NOW", nowX, 10);

      const py = yAt(liveClose);
      g.save();
      if (p.neon) {
        g.shadowColor = `rgba(${T.purpleRgb}, 0.9)`;
        g.shadowBlur = 10;
      }
      g.fillStyle = "#fff";
      g.strokeStyle = T.purple;
      g.lineWidth = 2;
      g.beginPath();
      g.arc(nowX, py, 4.5, 0, Math.PI * 2);
      g.fill();
      g.stroke();
      g.restore();

      g.restore(); // main clip

      // live price pill on the y axis (outside clip so it sits over the axis)
      if (py > 0 && py < priceBottom) {
        const text = fmtPrice(liveClose);
        g.font = `700 10px ${FONT}`;
        const w = Math.max(52, g.measureText(text).width + 14);
        g.fillStyle = T.purple;
        g.beginPath();
        g.roundRect(plotR + 2, py - 9, Math.min(w, AXIS_W - 3), 18, 5);
        g.fill();
        g.fillStyle = "#fff";
        g.textAlign = "left";
        g.fillText(text, plotR + 8, py + 3);
        g.font = `10px ${FONT}`;
      }

      // ---- oscillator panes
      let paneTop = mainBottom;
      for (const id of oscIds) {
        drawOscPane(g, id, c, cs, firstVisible, lastVisibleData, xAt, plotL, plotR, plotW, paneTop, oscH, T);
        paneTop += oscH;
      }

      // ---- crosshair + tooltips
      const ptr = pointerRef.current;
      if (p.showCrosshair && ptr.inside && ptr.x >= plotL && ptr.x <= plotR && ptr.y <= H - AXIS_H) {
        g.strokeStyle = "rgba(255,255,255,0.22)";
        g.setLineDash([3, 3]);
        g.beginPath();
        g.moveTo(ptr.x, 0);
        g.lineTo(ptr.x, H - AXIS_H);
        g.moveTo(plotL, ptr.y);
        g.lineTo(plotR, ptr.y);
        g.stroke();
        g.setLineDash([]);

        // y axis price tag
        if (ptr.y < priceBottom) {
          const v = vAtY(ptr.y);
          const text = fmtPrice(v);
          g.fillStyle = "rgba(20, 24, 46, 0.95)";
          g.strokeStyle = "rgba(255,255,255,0.2)";
          g.beginPath();
          g.roundRect(plotR + 2, ptr.y - 9, AXIS_W - 3, 18, 4);
          g.fill();
          g.stroke();
          g.fillStyle = T.text;
          g.textAlign = "left";
          g.fillText(text, plotR + 8, ptr.y + 3);
        }

        // x axis time tag
        const hoverIdx = Math.round(idxAtX(ptr.x));
        const hoverTime =
          hoverIdx < cs.length
            ? cs[Math.max(0, hoverIdx)].time
            : cs[cs.length - 1].time + (hoverIdx - cs.length + 1) * p.timeframeMs;
        const tText = timeFmtFull.format(new Date(hoverTime));
        g.font = `600 10px ${FONT}`;
        const tw = g.measureText(tText).width + 14;
        g.fillStyle = "rgba(20, 24, 46, 0.95)";
        g.strokeStyle = "rgba(255,255,255,0.2)";
        g.beginPath();
        g.roundRect(Math.min(Math.max(ptr.x - tw / 2, plotL), plotR - tw), H - AXIS_H + 2, tw, 18, 4);
        g.fill();
        g.stroke();
        g.fillStyle = T.text;
        g.textAlign = "center";
        g.fillText(tText, Math.min(Math.max(ptr.x, plotL + tw / 2), plotR - tw / 2), H - AXIS_H + 15);
        g.font = `10px ${FONT}`;

        // OHLC readout for the hovered candle
        if (hoverIdx >= 0 && hoverIdx < cs.length && !hoveredHist && !hoveredTrade) {
          const cd = cs[hoverIdx];
          const chg = ((cd.close - cd.open) / cd.open) * 100;
          g.textAlign = "left";
          g.font = `600 10px ${FONT}`;
          const segs: Array<[string, string]> = [
            [`O ${fmtPrice(cd.open)}`, T.text],
            [`H ${fmtPrice(cd.high)}`, T.green],
            [`L ${fmtPrice(cd.low)}`, T.red],
            [`C ${fmtPrice(cd.close)}`, cd.close >= cd.open ? T.green : T.red],
            [`${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`, chg >= 0 ? T.green : T.red],
            [`V ${fmtVol(cd.volume)}`, T.textDim],
          ];
          let x = plotL + 8;
          const yRow = mainBottom - 8;
          g.fillStyle = "rgba(11, 14, 29, 0.72)";
          let totalW = 0;
          for (const [s] of segs) totalW += g.measureText(s).width + 12;
          g.beginPath();
          g.roundRect(x - 5, yRow - 12, totalW + 6, 17, 4);
          g.fill();
          for (const [s, color] of segs) {
            g.fillStyle = color;
            g.fillText(s, x, yRow);
            x += g.measureText(s).width + 12;
          }
          g.font = `10px ${FONT}`;
        }
      }

      // prediction-history / trade-marker tooltip card
      if (hoveredHist) {
        drawHistoryTooltip(g, hoveredHist, W, mainBottom, T);
      } else if (hoveredTrade) {
        drawTradeTooltip(g, hoveredTrade, W, mainBottom, T);
      }

      // watermark
      g.font = `700 26px ${FONT}`;
      g.fillStyle = "rgba(255,255,255,0.035)";
      g.textAlign = "center";
      g.fillText(`${p.symbol} · QUANTX AI`, plotL + plotW / 2, mainBottom / 2);
      g.font = `10px ${FONT}`;

      return stillMoving;
    };

    raf = requestAnimationFrame(frame);
    return () => {
      running = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------ interaction
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const markDirty = () => {
      dirtyRef.current = true;
    };

    // Tap-vs-drag discrimination for the position-line edit affordance: a
    // pointer that never strays more than a few px from its down point is a
    // tap, anything else is a pan/pinch.
    const tapRef = { downX: 0, downY: 0, moved: true };

    const onPointerDown = (e: PointerEvent) => {
      canvas.setPointerCapture(e.pointerId);
      const d = dragRef.current;
      d.pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      if (d.pointers.size === 1) {
        d.active = true;
        d.lastX = e.offsetX;
        tapRef.downX = e.offsetX;
        tapRef.downY = e.offsetY;
        tapRef.moved = false;
      } else if (d.pointers.size === 2) {
        const pts = [...d.pointers.values()];
        d.pinchDist = Math.abs(pts[0].x - pts[1].x);
        d.active = false;
        tapRef.moved = true;
      }
    };

    const onPointerMove = (e: PointerEvent) => {
      const d = dragRef.current;
      pointerRef.current = { x: e.offsetX, y: e.offsetY, inside: true };
      if (d.pointers.has(e.pointerId)) d.pointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });

      if (d.pointers.size === 2) {
        // pinch zoom (x axis)
        const pts = [...d.pointers.values()];
        const dist = Math.max(12, Math.abs(pts[0].x - pts[1].x));
        if (d.pinchDist > 0) {
          const scale = d.pinchDist / dist;
          zoomBy(scale, (pts[0].x + pts[1].x) / 2);
        }
        d.pinchDist = dist;
      } else if (d.active) {
        const rect = canvas.getBoundingClientRect();
        const plotW = rect.width - AXIS_W;
        const v = viewRef.current;
        const dxBars = ((e.offsetX - d.lastX) / plotW) * v.bars;
        v.end -= dxBars;
        d.lastX = e.offsetX;
      }
      if (!tapRef.moved && (Math.abs(e.offsetX - tapRef.downX) > 5 || Math.abs(e.offsetY - tapRef.downY) > 5)) {
        tapRef.moved = true;
      }
      markDirty();
    };

    const endPointer = (e: PointerEvent) => {
      const d = dragRef.current;
      d.pointers.delete(e.pointerId);
      if (d.pointers.size === 0) {
        d.active = false;
        d.pinchDist = 0;
      }
      // tap on an open-position line -> edit its risk levels (never drag-
      // to-edit: taps only, validated in the modal + server-side)
      const onEdit = propsRef.current.onEditPosition;
      if (!tapRef.moved && onEdit) {
        const hit = posLinesRef.current.find(
          (l) => Math.abs(e.offsetY - l.y) <= 6 && e.offsetX >= l.x1
        );
        if (hit) onEdit(hit.tradeId);
      }
      tapRef.moved = true;
      markDirty();
    };

    const onLeave = () => {
      pointerRef.current.inside = false;
      markDirty();
    };

    const zoomBy = (scale: number, anchorX: number) => {
      const rect = canvas.getBoundingClientRect();
      const plotW = rect.width - AXIS_W;
      const v = viewRef.current;
      const frac = Math.max(0, Math.min(1, anchorX / plotW));
      const anchorIdx = v.end - v.bars + frac * v.bars;
      const newBars = Math.max(20, Math.min(1500, v.bars * scale));
      v.end = anchorIdx + (1 - frac) * newBars;
      v.bars = newBars;
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        // trackpad horizontal scroll = pan
        const rect = canvas.getBoundingClientRect();
        const plotW = rect.width - AXIS_W;
        const v = viewRef.current;
        v.end += (e.deltaX / plotW) * v.bars;
      } else {
        zoomBy(e.deltaY > 0 ? 1.12 : 0.9, e.offsetX);
      }
      markDirty();
    };

    const onDblClick = () => {
      const p = propsRef.current;
      const total = p.candles.length + p.forecastBars;
      viewRef.current = { end: total, bars: Math.min(total, 160), lockedY: null };
      markDirty();
    };

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", endPointer);
    canvas.addEventListener("pointercancel", endPointer);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("dblclick", onDblClick);
    return () => {
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", endPointer);
      canvas.removeEventListener("pointercancel", endPointer);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("dblclick", onDblClick);
    };
  }, []);

  // PNG export
  useEffect(() => {
    if (!props.onExportRef) return;
    props.onExportRef(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const p = propsRef.current;
      const a = document.createElement("a");
      a.download = `${p.symbol}-quantx-chart-${new Date().toISOString().slice(0, 19)}.png`;
      a.href = canvas.toDataURL("image/png");
      a.click();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.onExportRef]);

  return (
    <div ref={wrapRef} className="pcx-canvas-wrap">
      <canvas ref={canvasRef} className="pcx-canvas" />
    </div>
  );
}

// ---------------------------------------------------------------- helpers

function drawSeries(
  g: CanvasRenderingContext2D,
  s: Series,
  from: number,
  to: number,
  xAt: (i: number) => number,
  yAt: (v: number) => number,
  color: string,
  width: number,
  dash: number[] = []
) {
  g.strokeStyle = color;
  g.lineWidth = width;
  g.setLineDash(dash);
  g.beginPath();
  let started = false;
  for (let i = Math.max(0, from); i <= Math.min(s.length - 1, to); i++) {
    const v = s[i];
    if (Number.isNaN(v)) {
      started = false;
      continue;
    }
    const x = xAt(i);
    if (!started) {
      g.moveTo(x, yAt(v));
      started = true;
    } else g.lineTo(x, yAt(v));
  }
  g.stroke();
  g.setLineDash([]);
}

function drawOscPane(
  g: CanvasRenderingContext2D,
  id: IndicatorId,
  c: Calc,
  cs: Candle[],
  from: number,
  to: number,
  xAt: (i: number) => number,
  plotL: number,
  plotR: number,
  plotW: number,
  top: number,
  h: number,
  T: Theme
) {
  g.strokeStyle = "rgba(255,255,255,0.09)";
  g.beginPath();
  g.moveTo(plotL, top + 0.5);
  g.lineTo(plotR + AXIS_W, top + 0.5);
  g.stroke();

  g.save();
  g.beginPath();
  g.rect(plotL, top + 1, plotW, h - 2);
  g.clip();

  const inner = (min: number, max: number) => (v: number) => top + 4 + ((max - v) / (max - min)) * (h - 10);

  let label = "";
  let valueText = "";

  if (id === "rsi" && c.rsi) {
    const y = inner(0, 100);
    // guide bands at 30/70
    g.fillStyle = "rgba(255,255,255,0.03)";
    g.fillRect(plotL, y(70), plotW, y(30) - y(70));
    g.strokeStyle = "rgba(255,255,255,0.08)";
    g.setLineDash([3, 4]);
    for (const lvl of [30, 70]) {
      g.beginPath();
      g.moveTo(plotL, y(lvl));
      g.lineTo(plotR, y(lvl));
      g.stroke();
    }
    g.setLineDash([]);
    drawSeries(g, c.rsi, from, to, xAt, (v) => y(v), T.cyan, 1.25);
    const last = lastFinite(c.rsi, to);
    label = "RSI 14";
    valueText = last === null ? "" : last.toFixed(1);
  } else if (id === "macd" && c.macd) {
    let mn = Infinity;
    let mx = -Infinity;
    for (let i = from; i <= to; i++) {
      for (const v of [c.macd.line[i], c.macd.signal[i], c.macd.hist[i]]) {
        if (!Number.isNaN(v)) {
          if (v < mn) mn = v;
          if (v > mx) mx = v;
        }
      }
    }
    if (!Number.isFinite(mn) || mx <= mn) {
      mn = -1;
      mx = 1;
    }
    const pad = (mx - mn) * 0.15;
    const y = inner(mn - pad, mx + pad);
    const zero = y(0);
    const barW = Math.max(1, Math.min(((plotR - plotL) / (to - from + 1)) * 0.55, 9));
    for (let i = from; i <= to; i++) {
      const v = c.macd.hist[i];
      if (Number.isNaN(v)) continue;
      g.fillStyle = v >= 0 ? "rgba(0, 245, 160, 0.45)" : "rgba(255, 93, 115, 0.45)";
      const yy = y(v);
      g.fillRect(xAt(i) - barW / 2, Math.min(zero, yy), barW, Math.max(1, Math.abs(zero - yy)));
    }
    drawSeries(g, c.macd.line, from, to, xAt, (v) => y(v), T.cyan, 1.1);
    drawSeries(g, c.macd.signal, from, to, xAt, (v) => y(v), T.yellow, 1.1);
    const last = lastFinite(c.macd.hist, to);
    label = "MACD 12·26·9";
    valueText = last === null ? "" : last.toFixed(2);
  } else if (id === "atr" && c.atr) {
    let mn = Infinity;
    let mx = -Infinity;
    for (let i = from; i <= to; i++) {
      const v = c.atr[i];
      if (!Number.isNaN(v)) {
        if (v < mn) mn = v;
        if (v > mx) mx = v;
      }
    }
    if (!Number.isFinite(mn) || mx <= mn) {
      mn = 0;
      mx = 1;
    }
    const y = inner(mn * 0.95, mx * 1.05);
    drawSeries(g, c.atr, from, to, xAt, (v) => y(v), T.purple, 1.25);
    const last = lastFinite(c.atr, to);
    label = "ATR 14";
    valueText = last === null ? "" : last.toFixed(1);
  } else if (id === "adx" && c.adx) {
    const y = inner(0, 60);
    g.strokeStyle = "rgba(255,255,255,0.08)";
    g.setLineDash([3, 4]);
    g.beginPath();
    g.moveTo(plotL, y(25));
    g.lineTo(plotR, y(25));
    g.stroke();
    g.setLineDash([]);
    drawSeries(g, c.adx.plusDI, from, to, xAt, (v) => y(Math.min(60, v)), "rgba(0, 245, 160, 0.55)", 1);
    drawSeries(g, c.adx.minusDI, from, to, xAt, (v) => y(Math.min(60, v)), "rgba(255, 93, 115, 0.55)", 1);
    drawSeries(g, c.adx.adx, from, to, xAt, (v) => y(Math.min(60, v)), T.yellow, 1.4);
    const last = lastFinite(c.adx.adx, to);
    label = "ADX 14";
    valueText = last === null ? "" : last.toFixed(1);
  }

  g.restore();
  g.fillStyle = T.textDim;
  g.textAlign = "left";
  g.font = `600 9px ${FONT}`;
  g.fillText(`${label}${valueText ? `  ${valueText}` : ""}`, plotL + 6, top + 12);
  g.font = `10px ${FONT}`;
  void cs;
}

function lastFinite(s: Series, to: number): number | null {
  for (let i = Math.min(s.length - 1, to); i >= 0; i--) {
    if (!Number.isNaN(s[i])) return s[i];
  }
  return null;
}

function drawAiCard(g: CanvasRenderingContext2D, prediction: any, x: number, y: number, T: Theme) {
  const dir = prediction.direction;
  const directional = dir === "LONG" || dir === "SHORT";
  const confidence = Math.max(0, Math.min(100, prediction.confidence ?? 0));
  const price = prediction.price;
  const target = prediction.target;
  const stop = prediction.stop;
  const weights = prediction.strategy_weights || {};
  let strategy = "";
  let best = -1;
  for (const k of Object.keys(weights)) {
    if (weights[k] > best) {
      best = weights[k];
      strategy = k;
    }
  }
  const rr =
    directional && typeof target === "number" && typeof stop === "number" && typeof price === "number" && price !== stop
      ? Math.abs(target - price) / Math.abs(price - stop)
      : null;
  const expectedMove =
    directional && typeof target === "number" && typeof price === "number" && price > 0
      ? ((target - price) / price) * 100
      : null;

  const rows: Array<[string, string, string]> = [
    ["Signal", directional ? dir : "NO TRADE", directional ? (dir === "LONG" ? T.green : T.red) : T.yellow],
    ["Confidence", `${confidence.toFixed(0)}%`, confidence >= 70 ? T.green : confidence >= 50 ? T.yellow : T.red],
  ];
  if (typeof prediction.risk?.required_confidence === "number") {
    rows.push(["Required", `${prediction.risk.required_confidence.toFixed(0)}%`, T.textDim]);
  }
  // Decision-engine provenance: who is actually driving this prediction
  // (Champion ML vs strategy ensemble vs fallback) and the risk verdict.
  const de = prediction.decision_engine;
  if (de?.mode) {
    const modeLabel =
      de.mode === "champion_ml" ? "Champion ML" : de.mode === "fallback" ? "Fallback (ensemble)" : "Strategy Ensemble";
    rows.push(["Mode", modeLabel, de.mode === "champion_ml" ? T.green : de.mode === "fallback" ? T.yellow : T.cyan]);
    if (de.mode === "champion_ml" && de.active_model) {
      rows.push(["Model", `${de.active_model.model_type ?? de.active_model.name} ${de.active_model.version ?? ""}`.trim(), T.cyan]);
    }
  }
  if (typeof prediction.risk?.allowed === "boolean") {
    rows.push(["Risk", prediction.risk.allowed ? "ALLOWED" : "BLOCKED", prediction.risk.allowed ? T.green : T.red]);
  }
  if (expectedMove !== null) rows.push(["Exp. move", `${expectedMove >= 0 ? "+" : ""}${expectedMove.toFixed(2)}%`, expectedMove >= 0 ? T.green : T.red]);
  if (rr !== null) rows.push(["R : R", `1 : ${rr.toFixed(2)}`, T.text]);
  if (prediction.regime) rows.push(["Regime", String(prediction.regime).replace(/_/g, " "), T.text]);
  if (strategy) rows.push(["Strategy", strategy.replace(/_/g, " "), T.cyan]);
  if (!directional && dir === "NO_TRADE" && prediction.strategies) {
    const blocked = Object.entries(prediction.strategies as Record<string, any>)
      .filter(([, v]) => v?.direction === "NO_TRADE")
      .map(([name]) => name.replace(/_/g, " "));
    if (blocked.length) {
      const text = blocked.join(", ");
      rows.push(["Blocked by", text.length > 42 ? `${text.slice(0, 41)}…` : text, T.textDim]);
    }
  }
  if (!directional && prediction.risk?.reason) {
    const reason = String(prediction.risk.reason);
    rows.push(["Reason", reason.length > 42 ? `${reason.slice(0, 41)}…` : reason, T.textDim]);
  }

  g.font = `600 10px ${FONT}`;
  let wMax = 0;
  for (const [k, v] of rows) {
    const w = g.measureText(`${k}  ${v}`).width;
    if (w > wMax) wMax = w;
  }
  const cardW = wMax + 40;
  const cardH = rows.length * 15 + 22;
  g.fillStyle = "rgba(13, 16, 33, 0.82)";
  g.strokeStyle = `rgba(${T.purpleRgb}, 0.35)`;
  g.lineWidth = 1;
  g.beginPath();
  g.roundRect(x, y, cardW, cardH, 8);
  g.fill();
  g.stroke();

  g.fillStyle = T.textDim;
  g.textAlign = "left";
  g.font = `700 9px ${FONT}`;
  g.fillText("AI DECISION", x + 10, y + 14);
  g.font = `600 10px ${FONT}`;
  rows.forEach(([k, v, color], i) => {
    const ry = y + 29 + i * 15;
    g.fillStyle = T.textDim;
    g.fillText(k, x + 10, ry);
    g.fillStyle = color;
    g.textAlign = "right";
    g.fillText(v, x + cardW - 10, ry);
    g.textAlign = "left";
  });
  g.font = `10px ${FONT}`;
}
// Retained only for exported-chart backwards compatibility; the live chart
// no longer calls it because readable decision details live outside candles.
void drawAiCard;

function drawHistoryTooltip(
  g: CanvasRenderingContext2D,
  hovered: { pt: HistoryPoint; x: number; y: number },
  W: number,
  mainBottom: number,
  T: Theme
) {
  const pt = hovered.pt;
  const errorPct =
    typeof pt.error_pct === "number"
      ? pt.error_pct
      : typeof pt.predicted_price === "number" && typeof pt.actual_price === "number" && pt.actual_price
      ? Math.abs((pt.actual_price - pt.predicted_price) / pt.actual_price) * 100
      : null;
  const bucket = outcomeBucket(pt.outcome);
  const bucketLabel = bucket === "correct" ? "Correct" : bucket === "wrong" ? "Wrong" : "Unresolved";
  const bucketColor = bucket === "correct" ? T.green : bucket === "wrong" ? T.red : pt.outcome === "NO_TRADE" ? T.textDim : T.yellow;

  const rows: Array<[string, string, string]> = [
    ["Time", timeFmtFull.format(new Date(pt.timestamp)), T.text],
  ];
  if (pt.timeframe) rows.push(["Timeframe", pt.timeframe, T.text]);
  if (pt.direction) rows.push(["Direction", pt.direction, pt.direction === "LONG" ? T.green : pt.direction === "SHORT" ? T.red : T.textDim]);
  if (pt.confidence != null) rows.push(["Confidence", `${pt.confidence.toFixed(0)}%`, T.text]);
  rows.push(["Predicted", pt.predicted_price != null ? fmtPrice(pt.predicted_price) : "—", HISTORY_ORANGE]);
  rows.push(["Actual", pt.actual_price != null ? fmtPrice(pt.actual_price) : "pending", T.text]);
  if (errorPct !== null) rows.push(["Error %", `${errorPct.toFixed(2)}%`, errorPct <= 1 ? T.green : errorPct <= 5 ? T.yellow : T.red]);
  if (pt.strategy) rows.push(["Strategy", pt.strategy.replace(/_/g, " "), T.cyan]);
  if (pt.regime) rows.push(["Regime", pt.regime.replace(/_/g, " "), T.text]);
  rows.push(["Outcome", bucketLabel, bucketColor]);

  g.font = `600 10px ${FONT}`;
  let wMax = 96;
  for (const [k, v] of rows) {
    const w = g.measureText(`${k}    ${v}`).width;
    if (w > wMax) wMax = w;
  }
  const cardW = wMax + 34;
  const cardH = rows.length * 14 + 24;
  let cx = hovered.x + 14;
  let cy = hovered.y - cardH / 2;
  if (cx + cardW > W - 8) cx = hovered.x - cardW - 14;
  cy = Math.max(8, Math.min(cy, mainBottom - cardH - 8));

  g.fillStyle = "rgba(13, 16, 33, 0.94)";
  g.strokeStyle = `rgba(${HISTORY_ORANGE_RGB}, 0.45)`;
  g.lineWidth = 1;
  g.beginPath();
  g.roundRect(cx, cy, cardW, cardH, 8);
  g.fill();
  g.stroke();

  g.fillStyle = T.textDim;
  g.textAlign = "left";
  g.font = `700 9px ${FONT}`;
  g.fillText("AI PREDICTION", cx + 10, cy + 14);
  g.font = `600 10px ${FONT}`;
  rows.forEach(([k, v, color], i) => {
    const ry = cy + 28 + i * 14;
    g.fillStyle = T.textDim;
    g.fillText(k, cx + 10, ry);
    g.fillStyle = color;
    g.textAlign = "right";
    g.fillText(v, cx + cardW - 10, ry);
    g.textAlign = "left";
  });
  g.font = `10px ${FONT}`;
}

const DECISION_MODE_LABEL: Record<string, string> = {
  champion_ml: "Champion ML",
  strategy_ensemble: "Strategy Ensemble",
  fallback: "Fallback (ensemble)",
  manual: "Manual",
};

function drawTradeTooltip(
  g: CanvasRenderingContext2D,
  hovered: { t: TradeMarker; x: number; y: number; kind: "entry" | "exit" | "sl" | "tp" },
  W: number,
  mainBottom: number,
  T: Theme
) {
  const t = hovered.t;
  const kindLabel =
    hovered.kind === "entry" ? "Entry" : hovered.kind === "exit" ? "Exit" : hovered.kind === "sl" ? "Stop Loss" : "Take Profit";
  const price =
    hovered.kind === "entry" ? t.entry : hovered.kind === "exit" ? t.exit : hovered.kind === "sl" ? t.sl : t.tp;
  const won = (t.pnl ?? 0) >= 0;
  const clip = (s: string, n = 44) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

  const rows: Array<[string, string, string]> = [
    [kindLabel, price != null ? fmtPrice(price) : "—", hovered.kind === "sl" ? T.red : hovered.kind === "tp" ? T.green : T.text],
    ["Side", t.side, t.side === "LONG" ? T.green : T.red],
    ["Status", t.status, t.status === "OPEN" ? T.yellow : T.text],
  ];
  if (t.decision_mode) {
    rows.push(["Engine", DECISION_MODE_LABEL[t.decision_mode] ?? t.decision_mode, t.decision_mode === "champion_ml" ? T.green : T.cyan]);
  }
  if (t.champion_model_type) rows.push(["Model", t.champion_model_type, T.cyan]);
  if (t.strategy_used) rows.push(["Strategy", t.strategy_used.replace(/_/g, " "), T.cyan]);
  if (t.confidence != null) rows.push(["Confidence", `${t.confidence.toFixed(0)}%`, T.text]);
  if (t.regime) rows.push(["Regime", t.regime.replace(/_/g, " "), T.text]);
  const openReason = t.decision_reasons?.[0];
  if (openReason) rows.push(["Reason", clip(openReason), T.textDim]);
  if (t.risk_reason) rows.push(["Risk", clip(t.risk_reason), T.textDim]);
  if (t.status === "CLOSED") {
    rows.push(["Result", `${won ? "+" : ""}${(t.pnl ?? 0).toFixed(2)} USDT`, won ? T.green : T.red]);
    if (t.close_reason) rows.push(["Closed", clip(t.close_reason.replace(/_/g, " ")), T.textDim]);
  }

  g.font = `600 10px ${FONT}`;
  let wMax = 96;
  for (const [k, v] of rows) {
    const w = g.measureText(`${k}    ${v}`).width;
    if (w > wMax) wMax = w;
  }
  const cardW = wMax + 34;
  const cardH = rows.length * 14 + 24;
  let cx = hovered.x + 14;
  let cy = hovered.y - cardH / 2;
  if (cx + cardW > W - 8) cx = hovered.x - cardW - 14;
  cy = Math.max(8, Math.min(cy, mainBottom - cardH - 8));

  g.fillStyle = "rgba(13, 16, 33, 0.94)";
  g.strokeStyle = `rgba(${T.cyanRgb}, 0.45)`;
  g.lineWidth = 1;
  g.beginPath();
  g.roundRect(cx, cy, cardW, cardH, 8);
  g.fill();
  g.stroke();

  g.fillStyle = T.textDim;
  g.textAlign = "left";
  g.font = `700 9px ${FONT}`;
  g.fillText("PAPER TRADE", cx + 10, cy + 14);
  g.font = `600 10px ${FONT}`;
  rows.forEach(([k, v, color], i) => {
    const ry = cy + 28 + i * 14;
    g.fillStyle = T.textDim;
    g.fillText(k, cx + 10, ry);
    g.fillStyle = color;
    g.textAlign = "right";
    g.fillText(v, cx + cardW - 10, ry);
    g.textAlign = "left";
  });
  g.font = `10px ${FONT}`;
}

export default memo(ProChartCanvas);
