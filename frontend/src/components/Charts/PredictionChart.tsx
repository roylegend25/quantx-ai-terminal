import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Bug,
  Camera,
  ChevronDown,
  Crosshair,
  History,
  Info,
  Layers,
  LocateFixed,
  Maximize2,
  Minimize2,
  PenLine,
  RotateCcw,
  Sparkles,
  SlidersHorizontal,
  Star,
  TrendingDown,
  TrendingUp,
  X,
  Zap,
} from "lucide-react";
import type { Candle } from "../../hooks/useAppData";
import { api } from "../../services/api";
import { validateForecastChartData } from "../../lib/forecastChartData";
import { selectLatestEligiblePredictions } from "../../lib/latestPredictions";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { isTimeframe, TIMEFRAME_CONFIG, TIMEFRAME_ORDER } from "../../lib/timeframes";
import ProChartCanvas, {
  type HistoryPoint,
  type IndicatorId,
  type LiquidityCluster,
} from "./ProChartCanvas";
import { ChartDecisionChip, DecisionDetailsBottomSheet, DecisionDetailsPanel, ForecastLegend } from "./ChartDecisionDetails";
import DecisionSummaryTiles from "./DecisionSummaryTiles";

type Props = {
  symbol: string;
  onSymbolChange: (s: string) => void;
  interval: string;
  onIntervalChange: (i: string) => void;
  candles: Candle[];
  ticker: any;
  prediction: any;
  /** Paper trades (open + closed) for this symbol - drawn as entry/exit/
   *  SL/TP markers with decision-provenance tooltips. */
  trades?: any[];
  /** Tap on an open position's entry/TP/SL/liq chart line -> edit risk. */
  onEditPosition?: (tradeId: number) => void;
  candleState?: "loading" | "ready" | "error";
  candleError?: string | null;
};

const FORECAST_BARS = 40;

const INDICATOR_GROUPS: Array<{ title: string; items: Array<{ id: IndicatorId; label: string }> }> = [
  {
    title: "Trend",
    items: [
      { id: "ema20", label: "EMA 20" },
      { id: "ema50", label: "EMA 50" },
      { id: "ema100", label: "EMA 100" },
      { id: "ema200", label: "EMA 200" },
      { id: "vwap", label: "VWAP" },
      { id: "supertrend", label: "Supertrend" },
      { id: "ichimoku", label: "Ichimoku Cloud" },
      { id: "bollinger", label: "Bollinger Bands" },
      { id: "regression", label: "Regression Channel" },
    ],
  },
  {
    title: "Oscillators",
    items: [
      { id: "rsi", label: "RSI" },
      { id: "macd", label: "MACD" },
      { id: "atr", label: "ATR" },
      { id: "adx", label: "ADX" },
    ],
  },
  {
    title: "Structure & Flow",
    items: [
      { id: "volume", label: "Volume" },
      { id: "volumeProfile", label: "Volume Profile" },
      { id: "liquidity", label: "Liquidity Heatmap" },
      { id: "sr", label: "Support / Resistance" },
      { id: "pivots", label: "Pivot Points" },
      { id: "orderBlocks", label: "Order Blocks" },
      { id: "fvg", label: "Fair Value Gaps" },
    ],
  },
];

const DEFAULT_INDICATORS: IndicatorId[] = ["ema20", "ema50", "volume"];
const PREFS_KEY = "quantx_prochart_prefs_v1";

type ChartPrefs = {
  indicators: IndicatorId[];
  aiOverlay: boolean;
  history: boolean;
  bands: boolean;
  crosshair: boolean;
  autoScale: boolean;
  neon: boolean;
  style: "candles" | "line";
};

function loadPrefs(): ChartPrefs {
  const fallback: ChartPrefs = {
    indicators: DEFAULT_INDICATORS,
    aiOverlay: true,
    history: true,
    bands: true,
    crosshair: true,
    autoScale: true,
    neon: true,
    style: "candles",
  };
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return { ...fallback, ...parsed };
  } catch {
    return fallback;
  }
}

/** Buckets the backend's specific outcome values down to the three states
 *  the Past AI Prediction UI actually distinguishes visually (green/red/
 *  grey-yellow dots): correct, wrong, or not yet resolved. */
function outcomeBucket(outcome: string | null | undefined): "correct" | "wrong" | "unresolved" {
  if (outcome === "CORRECT" || outcome === "WIN") return "correct";
  if (outcome === "INCORRECT" || outcome === "LOSS") return "wrong";
  return "unresolved"; // PENDING, NO_TRADE
}

/** Candle interval sanity check: the prop candles may briefly belong to the
 *  previous timeframe while a switch is in flight - detect by bar spacing. */
function candlesMatchTimeframe(candles: Candle[], tfMs: number): boolean {
  if (candles.length < 3) return candles.length > 0;
  const d1 = candles[1].time - candles[0].time;
  const d2 = candles[2].time - candles[1].time;
  if (tfMs === 30 * 86_400_000) return [d1, d2].every(d => d >= 28 * 86_400_000 && d <= 31 * 86_400_000);
  return Math.min(d1, d2) === tfMs;
}

function PredictionChart({ symbol, onSymbolChange, interval, onIntervalChange, candles, ticker, prediction, trades, onEditPosition, candleState, candleError }: Props) {
  const isMobile = useMediaQuery("(max-width: 640px)");
  const isTabletPortrait = useMediaQuery("(min-width: 768px) and (max-width: 1024px)");
  const isTabletLandscape = useMediaQuery("(min-width: 1025px) and (max-width: 1366px)");

  const [showIndicators, setShowIndicators] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [prefs, setPrefs] = useState<ChartPrefs>(loadPrefs);
  const [resetSignal, setResetSignal] = useState(0);
  const [historyData, setHistoryData] = useState<{ points: HistoryPoint[]; summary: any }>({
    points: [],
    summary: null,
  });
  const [liquidity, setLiquidity] = useState<LiquidityCluster[]>([]);
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<"LIVE"|"RECONNECTING"|"CACHED"|"STALE"|"OFFLINE">("CACHED");
  const [lastMarketUpdate, setLastMarketUpdate] = useState<number | null>(null);
  const [flowCandles, setFlowCandles] = useState<Candle[]>([]);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const indicatorsRef = useRef<HTMLDivElement | null>(null);
  const exportFnRef = useRef<(() => void) | null>(null);
  // Session-level candle cache so revisiting a timeframe renders instantly
  // from the last data while the fresh fetch is in flight.
  const candleCacheRef = useRef<Map<string, Candle[]>>(new Map());
  const [cacheVersion, setCacheVersion] = useState(0);

  const tfConfig = isTimeframe(interval) ? TIMEFRAME_CONFIG[interval] : TIMEFRAME_CONFIG["1h"];
  const cacheKey = `${symbol}:${interval}`;

  useEffect(() => {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch {
      /* storage full/blocked - prefs just won't persist */
    }
  }, [prefs]);

  useEffect(() => {
    function onOutsideClick(e: MouseEvent) {
      if (indicatorsRef.current && !indicatorsRef.current.contains(e.target as Node)) {
        setShowIndicators(false);
      }
    }
    document.addEventListener("mousedown", onOutsideClick);
    return () => document.removeEventListener("mousedown", onOutsideClick);
  }, []);

  // Cache incoming candles under the symbol+timeframe they actually belong
  // to. Two guards against attributing in-flight stale data to a new key:
  // bar spacing must match the timeframe, and an array reference seen before
  // a symbol/interval switch is never credited to the post-switch key.
  const attributedRef = useRef<{ key: string; candles: Candle[] | null }>({ key: "", candles: null });
  useEffect(() => {
    if (!candles.length) return;
    const attr = attributedRef.current;
    if (attr.key !== cacheKey) {
      const isStaleArray = attr.candles === candles;
      attr.key = cacheKey;
      if (isStaleArray) return;
    }
    if (candlesMatchTimeframe(candles, tfConfig.ms)) {
      candleCacheRef.current.set(cacheKey, candles);
      attr.candles = candles;
      setCacheVersion((v) => v + 1);
    }
  }, [candles, cacheKey, tfConfig.ms]);

  const displayCandles = useMemo(() => {
    void cacheVersion;
    return candleCacheRef.current.get(cacheKey) ?? [];
  }, [cacheKey, cacheVersion]);

  // Historical candles arrive on the shared 10s load cycle.  Between those
  // loads, apply the shared market socket price to the current OHLC bar in
  // place (or append the next interval bar).  This path is deliberately
  // independent of the V2 decision, so NO_TRADE never freezes market flow.
  useEffect(() => {
    setFlowCandles(displayCandles.map((c) => ({ ...c })));
  }, [displayCandles, cacheKey]);
  useEffect(() => {
    if (!Number.isFinite(livePrice) || !livePrice || !tfConfig.ms) return;
    setFlowCandles((current) => {
      if (!current.length) return current;
      const next = current.slice();
      const last = next[next.length - 1];
      const bucket = Math.floor(Date.now() / tfConfig.ms) * tfConfig.ms;
      if (bucket < last.time) return current;
      if (bucket === last.time) {
        next[next.length - 1] = {
          ...last, high: Math.max(last.high, livePrice), low: Math.min(last.low, livePrice),
          close: livePrice,
        };
      } else {
        next.push({ time: bucket, open: last.close, high: livePrice, low: livePrice, close: livePrice, volume: 0 });
        if (next.length > 240) next.shift();
      }
      return next;
    });
  }, [livePrice, tfConfig.ms]);

  // ---- prediction history (stored, real predictions only)
  useEffect(() => {
    let cancelled = false;
    // Clear immediately on switch, synchronously with the symbol/interval
    // change, rather than waiting for the fetch below to resolve - a BTC 1h
    // history point must never render, even briefly, once the user has
    // already switched to ETH or to 15m.
    setHistoryData({ points: [], summary: null });
    const fetchHistory = async () => {
      try {
        const res = await api.predictionHistory(symbol, interval, 500);
        if (!cancelled) setHistoryData({ points: res?.history ?? [], summary: res?.summary ?? null });
      } catch {
        /* endpoint unreachable - keep last data */
      }
    };
    fetchHistory();
    const id = window.setInterval(fetchHistory, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [symbol, interval]);

  // Phase 34: the main chart draws only the latest 10 ELIGIBLE predictions
  // (a real predicted_price and timestamp) as one continuous line, in
  // chronological order - the full fetched set (up to 500 points) stays
  // available in historyData.points for an expandable analytics view, it
  // just never all renders on the price chart itself, which is what made
  // the overlay read as visual clutter / "stale duplicate forecast lines".
  const latestTenPredictions = useMemo(
    () => selectLatestEligiblePredictions(historyData.points, 10),
    [historyData.points]
  );

  // ---- liquidity heatmap (only fetched while the overlay is on)
  const liquidityOn = prefs.indicators.includes("liquidity");
  useEffect(() => {
    if (!liquidityOn) return;
    let cancelled = false;
    const fetchHeatmap = async () => {
      try {
        const res = await api.liquidationHeatmap(symbol);
        if (!cancelled) setLiquidity(res?.clusters ?? []);
      } catch {
        /* degraded - overlay simply stays empty */
      }
    };
    fetchHeatmap();
    const id = window.setInterval(fetchHeatmap, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [symbol, liquidityOn]);

  // ---- live 1s price via the backend market websocket, with the 10s ticker
  // poll as fallback. Never synthesized - if the socket is down the marker
  // simply updates at poll cadence.
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retryTimer: number | null = null;
    let attempts = 0;

    const connect = () => {
      if (closed) return;
      try {
        setConnectionStatus(attempts ? "RECONNECTING" : "CACHED");
        const proto = window.location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${window.location.host}/ws/market/${symbol}`);
        ws.onmessage = (ev) => {
          try {
            const data = JSON.parse(ev.data);
            const p = Number(data?.ticker?.lastPrice);
            if (Number.isFinite(p) && p > 0) {
              setLivePrice(p);
              setLastMarketUpdate(Date.now());
              setConnectionStatus("LIVE");
            }
          } catch {
            /* malformed frame - skip */
          }
        };
        ws.onopen = () => {
          attempts = 0;
          setConnectionStatus("LIVE");
        };
        ws.onclose = () => {
          if (closed) return;
          attempts += 1;
          setConnectionStatus("RECONNECTING");
          const backoff = Math.min(30_000, 1000 * (2 ** Math.min(attempts, 5)));
          retryTimer = window.setTimeout(connect, backoff + Math.floor(Math.random() * 500));
        };
        ws.onerror = () => {
          ws?.close();
        };
      } catch {
        setConnectionStatus("OFFLINE");
      }
    };
    connect();
    return () => {
      closed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      ws?.close();
    };
  }, [symbol]);

  // Reset the WS price when symbol changes so the old symbol's price never
  // renders against the new symbol's candles.
  useEffect(() => {
    setLivePrice(null);
    setLastMarketUpdate(null);
    setConnectionStatus("CACHED");
  }, [symbol]);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (!lastMarketUpdate) return;
      const age = Date.now() - lastMarketUpdate;
      if (age > 30_000) setConnectionStatus("STALE");
      else if (age > 5_000 && connectionStatus === "LIVE") setConnectionStatus("CACHED");
    }, 1000);
    return () => window.clearInterval(id);
  }, [lastMarketUpdate, connectionStatus]);

  const lastPrice = livePrice ?? Number(ticker?.lastPrice || 0);
  const change = Number(ticker?.priceChangePercent || 0);
  const changeAbs = Number(ticker?.priceChange || 0);

  // Phase 34: desktop/MacBook heights use clamp() instead of one fixed
  // number for every width from 1280px to 4K - a fixed 460px reads as
  // "compressed" on a 1512-1728px MacBook that actually has the vertical
  // room for a taller chart. Mobile/tablet buckets are unchanged (already
  // viewport-appropriate, not part of the MacBook compression complaint).
  const chartHeight = fullscreen
    ? undefined
    : isMobile
    ? 400
    : isTabletPortrait
    ? 480
    : isTabletLandscape
    ? 520
    : 580;

  const forecast = prediction?.forecast;
  const chartCandles=flowCandles.length?flowCandles:displayCandles;
  const lastCandleTime=chartCandles.at(-1)?.time??0;
  const validatedForecast=useMemo(()=>validateForecastChartData(prediction,symbol,tfConfig.ms,lastCandleTime),[prediction,symbol,tfConfig.ms,lastCandleTime]);
  const forecastAvailable = validatedForecast.valid;
  const chartForecast={...forecast,available:forecastAvailable,reason:validatedForecast.reason??forecast?.reason};
  const lastClose = displayCandles.length ? displayCandles[displayCandles.length - 1].close : lastPrice;

  // Short display symbol for chart annotations ("BTC" not "BTCUSDT").
  const shortSymbol = symbol.replace(/USDT$/, "");

  // Debug info: mirrors ProChartCanvas's buildCone() gate exactly (a forecast
  // is only ever drawn for a directional, non-NO_TRADE prediction with at
  // least one real candle) so "why is there no forecast line" is answerable
  // without reading canvas internals.
  const forecastPointCount = forecastAvailable && Array.isArray(forecast?.median_path) ? forecast.median_path.length : 0;
  const forecastHiddenReason = !displayCandles.length
    ? "no candle data loaded yet"
    : !forecastAvailable
    ? forecast?.reason ?? "forecast unavailable"
    : null;

  useEffect(() => {
    // Dev-only: import.meta.env.DEV is stripped to `false` (and this whole
    // block dead-code-eliminated) in a production Vite build, so nothing
    // logs in prod regardless of the showDebug panel toggle below.
    if (!import.meta.env.DEV) return;
    // eslint-disable-next-line no-console
    console.debug("[QuantX chart debug]", {
      selectedSymbol: symbol,
      selectedTimeframe: interval,
      direction: prediction?.direction ?? null,
      confidence: prediction?.confidence ?? null,
      requiredConfidence: prediction?.risk?.required_confidence ?? null,
      riskAllowed: prediction?.risk?.allowed ?? null,
      riskReason: prediction?.risk?.reason ?? null,
      candleCount: displayCandles.length,
      historyPointCount: historyData.points.length,
      forecastPointCount,
      forecastHiddenReason,
    });
  }, [symbol, interval, prediction, displayCandles.length, historyData.points.length, forecastPointCount, forecastHiddenReason]);

  const toggleIndicator = useCallback((id: IndicatorId) => {
    setPrefs((prev) => ({
      ...prev,
      indicators: prev.indicators.includes(id)
        ? prev.indicators.filter((x) => x !== id)
        : [...prev.indicators, id],
    }));
  }, []);

  const togglePref = useCallback((key: keyof Omit<ChartPrefs, "indicators" | "style">) => {
    setPrefs((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const handleExportRef = useCallback((fn: () => void) => {
    exportFnRef.current = fn;
  }, []);

  const summary = historyData.summary;
  const hitRate = summary?.direction_hit_rate_pct;

  // Past AI Prediction stat row - derived straight from the same history
  // points the chart plots as dots, so the numbers always match what's on
  // screen (rather than trusting the backend summary's own bucketing).
  const pastPredictionStats = useMemo(() => {
    const points = historyData.points as any[];
    let correct = 0;
    let wrong = 0;
    let unresolved = 0;
    let errorSum = 0;
    let errorCount = 0;
    for (const p of points) {
      const bucket = outcomeBucket(p.outcome);
      if (bucket === "correct") correct += 1;
      else if (bucket === "wrong") wrong += 1;
      else unresolved += 1;
      if (typeof p.error_pct === "number") {
        errorSum += p.error_pct;
        errorCount += 1;
      }
    }
    const resolved = correct + wrong;
    return {
      total: points.length,
      correct,
      wrong,
      unresolved,
      hitRatePct: resolved ? (correct / resolved) * 100 : null,
      avgErrorPct: errorCount ? errorSum / errorCount : null,
    };
  }, [historyData.points]);

  const toolButtons: Array<{
    key: string;
    title: string;
    icon: ReactNode;
    active?: boolean;
    onClick: () => void;
    disabled?: boolean;
  }> = [
    {
      key: "ai",
      title: "AI overlay (entry / TP / SL / signal card)",
      icon: <Sparkles size={15} />,
      active: prefs.aiOverlay,
      onClick: () => togglePref("aiOverlay"),
    },
    {
      key: "history",
      title: "Past AI predictions vs actual price",
      icon: <History size={15} />,
      active: prefs.history,
      onClick: () => togglePref("history"),
    },
    {
      key: "bands",
      title: "Prediction confidence bands",
      icon: <Layers size={15} />,
      active: prefs.bands,
      onClick: () => togglePref("bands"),
    },
    {
      key: "crosshair",
      title: "Crosshair",
      icon: <Crosshair size={15} />,
      active: prefs.crosshair,
      onClick: () => togglePref("crosshair"),
    },
    {
      key: "autoscale",
      title: "Auto scale",
      icon: <LocateFixed size={15} />,
      active: prefs.autoScale,
      onClick: () => togglePref("autoScale"),
    },
    {
      key: "neon",
      title: "Neon glow theme",
      icon: <Zap size={15} />,
      active: prefs.neon,
      onClick: () => togglePref("neon"),
    },
    {
      key: "style",
      title: prefs.style === "candles" ? "Switch to line chart" : "Switch to candlesticks",
      icon: prefs.style === "candles" ? <TrendingUp size={15} /> : <Layers size={15} />,
      active: false,
      onClick: () => setPrefs((prev) => ({ ...prev, style: prev.style === "candles" ? "line" : "candles" })),
    },
    {
      key: "drawing",
      title: "Drawing tools (coming soon)",
      icon: <PenLine size={15} />,
      onClick: () => {},
      disabled: true,
    },
    {
      key: "reset",
      title: "Reset view (double-click chart also resets)",
      icon: <RotateCcw size={15} />,
      onClick: () => setResetSignal((v) => v + 1),
    },
    {
      key: "export",
      title: "Export chart as PNG",
      icon: <Camera size={15} />,
      onClick: () => exportFnRef.current?.(),
    },
    {
      key: "debug",
      title: "Debug panel (symbol/prediction/data-pipeline diagnostics)",
      icon: <Bug size={15} />,
      active: showDebug,
      onClick: () => setShowDebug((v) => !v),
    },
  ];

  return (
    <div className={`chart-card pc-card${fullscreen ? " pc-fullscreen" : ""}`}>
      <div className="chart-toolbar pc-toolbar">
        <div className="pc-title-group">
          <div className="pc-title-row">
            <h3>{symbol} AI Prediction Chart</h3>
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
          <div className="tf-group pcx-tf-group">
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
              <div className="pc-indicators-panel pcx-indicators-panel">
                {INDICATOR_GROUPS.map((group) => (
                  <div key={group.title} className="pcx-ind-group">
                    <div className="pcx-ind-group-title">{group.title}</div>
                    <div className="pcx-ind-grid">
                      {group.items.map((item) => (
                        <label key={item.id} className="pcx-ind-item">
                          <input
                            type="checkbox"
                            checked={prefs.indicators.includes(item.id)}
                            onChange={() => toggleIndicator(item.id)}
                          />
                          <span>{item.label}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
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
          <span className="pc-legend-item" title="Live market candles / price line">
            <i className="pc-swatch solid purple" /> Actual Price
          </span>
          <ForecastLegend forecast={chartForecast} bands={prefs.bands} />
          <span
            className={`pc-legend-item${!prefs.history || !historyData.points.length ? " pc-legend-off" : ""}`}
            title={
              !prefs.history
                ? "Hidden - re-enable with the history toolbar button below"
                : !historyData.points.length
                ? `No stored predictions yet for ${shortSymbol} ${interval}`
                : "Stored past AI predictions vs what price actually did"
            }
          >
            <i className="pc-swatch dash orange" /> Past AI Predictions
            {!prefs.history ? " (off)" : !historyData.points.length ? " (none yet)" : ""}
          </span>
          {typeof hitRate === "number" && (
            <span className="pc-legend-item pcx-hitrate" title={`Direction hit rate over the last ${summary?.resolved ?? 0} resolved predictions on this timeframe`}>
              AI Hit Rate <b className={hitRate >= 50 ? "green" : "red"}>{hitRate.toFixed(1)}%</b>
            </span>
          )}
        </div>

        <div className="pc-price-row">
          <span className={`market-connection ${connectionStatus.toLowerCase()}`} title={lastMarketUpdate?`Last market update ${Math.max(0,Math.round((Date.now()-lastMarketUpdate)/1000))}s ago`:"Waiting for live market stream"}>
            <i /> {connectionStatus}
          </span>
          <b>${lastPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b>
          <span className={change >= 0 ? "green" : "red"}>
            {change >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
            {change.toFixed(2)}% ({changeAbs >= 0 ? "+" : ""}
            {changeAbs.toFixed(1)})
          </span>
        </div>
      </div>

      {pastPredictionStats.total > 0 && (
        <div className="pc-past-stat-row" title="Computed from this symbol/timeframe's stored prediction history only">
          <div className="pc-past-stat">
            <span>Past AI Hit Rate</span>
            <b className={pastPredictionStats.hitRatePct != null && pastPredictionStats.hitRatePct >= 50 ? "green" : "red"}>
              {pastPredictionStats.hitRatePct != null ? `${pastPredictionStats.hitRatePct.toFixed(1)}%` : "—"}
            </b>
          </div>
          <div className="pc-past-stat">
            <span>Avg Error %</span>
            <b>{pastPredictionStats.avgErrorPct != null ? `${pastPredictionStats.avgErrorPct.toFixed(2)}%` : "—"}</b>
          </div>
          <div className="pc-past-stat">
            <span>Total Predictions</span>
            <b>{pastPredictionStats.total}</b>
          </div>
          <div className="pc-past-stat">
            <span>Correct</span>
            <b className="green">{pastPredictionStats.correct}</b>
          </div>
          <div className="pc-past-stat">
            <span>Wrong</span>
            <b className="red">{pastPredictionStats.wrong}</b>
          </div>
          <div className="pc-past-stat">
            <span>Unresolved</span>
            <b className="yellow">{pastPredictionStats.unresolved}</b>
          </div>
        </div>
      )}

      <div className="pcx-toolrow">
        {toolButtons.map((b) => (
          <button
            key={b.key}
            className={`pcx-tool-btn${b.active ? " active" : ""}${b.disabled ? " disabled" : ""}`}
            title={b.title}
            onClick={b.onClick}
            disabled={b.disabled}
          >
            {b.icon}
          </button>
        ))}
      </div>

      <div className="pc-chart-layout">
      <div className="pc-chart-wrap" style={{ height: fullscreen ? "calc(100vh - 280px)" : chartHeight }}>
        {fullscreen && (
          <button className="icon-btn pc-fullscreen-close" onClick={() => setFullscreen(false)} title="Close">
            <X size={18} />
          </button>
        )}
        <ProChartCanvas
          symbol={symbol}
          candles={chartCandles}
          timeframeMs={tfConfig.ms}
          prediction={prediction}
          history={latestTenPredictions}
          trades={trades ?? []}
          liquidityClusters={liquidity}
          indicators={prefs.indicators}
          showAiOverlay={prefs.aiOverlay}
          showHistory={prefs.history}
          forecastBars={prediction?.forecast?.bars || FORECAST_BARS}
          showPrice={true}
          showForecast={true}
          showUpperBand={prefs.bands}
          showLowerBand={prefs.bands}
          showCone={prefs.bands}
          showCrosshair={prefs.crosshair}
          autoScale={prefs.autoScale}
          neon={prefs.neon}
          chartStyle={prefs.style}
          livePrice={livePrice}
          resetSignal={resetSignal}
          onExportRef={handleExportRef}
          onEditPosition={onEditPosition}
        />
        {candleState === "loading" && !chartCandles.length && <div className="chart-data-state">Loading {interval} candles…</div>}
        {candleState === "error" && <div className="chart-data-state error" role="alert">{candleError || "Candle data unavailable"}</div>}
        {prediction ? <ChartDecisionChip prediction={prediction} onOpen={() => setDetailsOpen(true)} /> : <div className="forecast-loading">Calculating {symbol} {interval} forecast…</div>}
      </div>
      <div className="pc-desktop-decision">{prediction ? <DecisionDetailsPanel prediction={prediction} /> : <div className="forecast-loading-panel">Calculating Active Drive V2 decision…</div>}</div>
      </div>
      <div className="pc-tablet-decision">{prediction ? <DecisionDetailsPanel prediction={prediction} /> : <div className="forecast-loading-panel">Calculating Active Drive V2 decision…</div>}</div>
      <DecisionDetailsBottomSheet prediction={prediction} open={detailsOpen} onClose={() => setDetailsOpen(false)} />

      <DecisionSummaryTiles
        prediction={prediction}
        interval={interval}
        intervalMs={tfConfig.ms}
        forecastBars={FORECAST_BARS}
        lastClose={lastClose ?? null}
      />

      {showDebug && (
        <div className="pc-debug-panel">
          <div className="pc-debug-row"><span>selectedSymbol</span><b>{symbol}</b></div>
          <div className="pc-debug-row"><span>interval</span><b>{interval}</b></div>
          <div className="pc-debug-row"><span>prediction.direction</span><b>{prediction?.direction ?? "—"}</b></div>
          <div className="pc-debug-row"><span>prediction.confidence</span><b>{prediction?.confidence ?? "—"}</b></div>
          <div className="pc-debug-row"><span>required confidence</span><b>{prediction?.risk?.required_confidence ?? "—"}</b></div>
          <div className="pc-debug-row"><span>risk.allowed</span><b>{String(prediction?.risk?.allowed ?? "—")}</b></div>
          <div className="pc-debug-row"><span>candle count</span><b>{displayCandles.length}</b></div>
          <div className="pc-debug-row"><span>history points</span><b>{historyData.points.length}</b></div>
          <div className="pc-debug-row"><span>forecast points</span><b>{forecastPointCount}</b></div>
          <div className="pc-debug-row"><span>forecast hidden reason</span><b>{forecastHiddenReason ?? "n/a - forecast is showing"}</b></div>
          <div className="pc-debug-row"><span>decision ID</span><b>{prediction?.decision_id ?? "—"}</b></div>
          <div className="pc-debug-row"><span>last candle (ms)</span><b>{lastCandleTime || "—"}</b></div>
          <div className="pc-debug-row"><span>forecast anchor (s)</span><b>{validatedForecast.anchorTime ?? "—"}</b></div>
          <div className="pc-debug-row"><span>last forecast (s)</span><b>{validatedForecast.lastTime ?? "—"}</b></div>
          <div className="pc-debug-row"><span>interval seconds</span><b>{tfConfig.ms / 1000}</b></div>
          <div className="pc-debug-row"><span>series valid</span><b>{String(validatedForecast.valid)}</b></div>
        </div>
      )}
    </div>
  );
}

export default memo(PredictionChart);
