import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  Camera,
  ChevronDown,
  Crosshair,
  History,
  Info,
  Layers,
  LocateFixed,
  Maximize2,
  Minimize2,
  Minus,
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
import { fmtNum, fmtUsd } from "../../lib/format";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import ProChartCanvas, {
  type HistoryPoint,
  type IndicatorId,
  type LiquidityCluster,
} from "./ProChartCanvas";

type Props = {
  symbol: string;
  onSymbolChange: (s: string) => void;
  interval: string;
  onIntervalChange: (i: string) => void;
  candles: Candle[];
  ticker: any;
  prediction: any;
};

const TIMEFRAME_CONFIG: Record<string, { label: string; ms: number }> = {
  "1m": { label: "1m", ms: 60_000 },
  "5m": { label: "5m", ms: 5 * 60_000 },
  "15m": { label: "15m", ms: 15 * 60_000 },
  "30m": { label: "30m", ms: 30 * 60_000 },
  "1h": { label: "1H", ms: 3_600_000 },
  "4h": { label: "4H", ms: 4 * 3_600_000 },
  "1d": { label: "1D", ms: 86_400_000 },
  "1w": { label: "1W", ms: 7 * 86_400_000 },
};
const TIMEFRAME_ORDER = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"];

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

function formatHorizon(totalMs: number): string {
  const hours = totalMs / 3_600_000;
  if (hours < 1) return `${Math.round(totalMs / 60_000)} Minutes`;
  if (hours < 48) return `${Math.round(hours)} Hours`;
  const days = hours / 24;
  if (days < 14) return `${Math.round(days)} Days`;
  return `${Math.round(days / 7)} Weeks`;
}

function fmtSignedPct(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

/** Candle interval sanity check: the prop candles may briefly belong to the
 *  previous timeframe while a switch is in flight - detect by bar spacing. */
function candlesMatchTimeframe(candles: Candle[], tfMs: number): boolean {
  if (candles.length < 3) return candles.length > 0;
  const d1 = candles[1].time - candles[0].time;
  const d2 = candles[2].time - candles[1].time;
  return Math.min(d1, d2) === tfMs;
}

function PredictionChart({ symbol, onSymbolChange, interval, onIntervalChange, candles, ticker, prediction }: Props) {
  const isMobile = useMediaQuery("(max-width: 640px)");
  const isTabletPortrait = useMediaQuery("(min-width: 768px) and (max-width: 1024px)");
  const isTabletLandscape = useMediaQuery("(min-width: 1025px) and (max-width: 1366px)");

  const [showIndicators, setShowIndicators] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [prefs, setPrefs] = useState<ChartPrefs>(loadPrefs);
  const [resetSignal, setResetSignal] = useState(0);
  const [historyData, setHistoryData] = useState<{ points: HistoryPoint[]; summary: any }>({
    points: [],
    summary: null,
  });
  const [liquidity, setLiquidity] = useState<LiquidityCluster[]>([]);
  const [livePrice, setLivePrice] = useState<number | null>(null);

  const indicatorsRef = useRef<HTMLDivElement | null>(null);
  const exportFnRef = useRef<(() => void) | null>(null);
  // Session-level candle cache so revisiting a timeframe renders instantly
  // from the last data while the fresh fetch is in flight.
  const candleCacheRef = useRef<Map<string, Candle[]>>(new Map());
  const [cacheVersion, setCacheVersion] = useState(0);

  const tfConfig = TIMEFRAME_CONFIG[interval] || TIMEFRAME_CONFIG["1h"];
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

  // ---- prediction history (stored, real predictions only)
  useEffect(() => {
    let cancelled = false;
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
        const proto = window.location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${window.location.host}/ws/market/${symbol}`);
        ws.onmessage = (ev) => {
          try {
            const data = JSON.parse(ev.data);
            const p = Number(data?.ticker?.lastPrice);
            if (Number.isFinite(p) && p > 0) setLivePrice(p);
          } catch {
            /* malformed frame - skip */
          }
        };
        ws.onopen = () => {
          attempts = 0;
        };
        ws.onclose = () => {
          if (closed) return;
          attempts += 1;
          retryTimer = window.setTimeout(connect, Math.min(30_000, 2000 * attempts));
        };
        ws.onerror = () => {
          ws?.close();
        };
      } catch {
        /* WebSocket unavailable in this context */
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
  }, [symbol]);

  const lastPrice = livePrice ?? Number(ticker?.lastPrice || 0);
  const change = Number(ticker?.priceChangePercent || 0);
  const changeAbs = Number(ticker?.priceChange || 0);

  const chartHeight = fullscreen
    ? undefined
    : isMobile
    ? 300
    : isTabletPortrait
    ? 360
    : isTabletLandscape
    ? 420
    : 460;

  const direction = prediction?.direction;
  const isNoTrade = !prediction || direction === "NO_TRADE" || !direction;
  const confidence = Math.max(0, Math.min(100, prediction?.confidence ?? 0));
  const target = prediction?.target;
  const stop = prediction?.stop;
  const lastClose = displayCandles.length ? displayCandles[displayCandles.length - 1].close : lastPrice;

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

  const confidenceLabel =
    confidence >= 80 ? "High Confidence" : confidence >= 60 ? "Medium Confidence" : "Low Confidence";
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
          <span className="pc-legend-item">
            <i className="pc-swatch solid purple" /> Actual Price
          </span>
          <span className="pc-legend-item">
            <i className="pc-swatch solid cyan" /> AI Forecast
          </span>
          <span className="pc-legend-item">
            <i className="pc-swatch dash cyan" /> Past AI Predictions
          </span>
          <span className="pc-legend-item">
            <i className="pc-swatch dash green" /> Upper Band
          </span>
          <span className="pc-legend-item">
            <i className="pc-swatch dash red" /> Lower Band
          </span>
          {typeof hitRate === "number" && (
            <span className="pc-legend-item pcx-hitrate" title={`Direction hit rate over the last ${summary?.resolved ?? 0} resolved predictions on this timeframe`}>
              AI Hit Rate <b className={hitRate >= 50 ? "green" : "red"}>{hitRate.toFixed(1)}%</b>
            </span>
          )}
        </div>

        <div className="pc-price-row">
          <b>${lastPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}</b>
          <span className={change >= 0 ? "green" : "red"}>
            {change >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
            {change.toFixed(2)}% ({changeAbs >= 0 ? "+" : ""}
            {changeAbs.toFixed(1)})
          </span>
        </div>
      </div>

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

      <div className="pc-chart-wrap" style={{ height: fullscreen ? "calc(100vh - 280px)" : chartHeight }}>
        {fullscreen && (
          <button className="icon-btn pc-fullscreen-close" onClick={() => setFullscreen(false)} title="Close">
            <X size={18} />
          </button>
        )}
        <ProChartCanvas
          symbol={symbol}
          candles={displayCandles}
          timeframeMs={tfConfig.ms}
          prediction={prediction}
          history={historyData.points}
          liquidityClusters={liquidity}
          indicators={prefs.indicators}
          showAiOverlay={prefs.aiOverlay}
          showHistory={prefs.history}
          showBands={prefs.bands}
          showCrosshair={prefs.crosshair}
          autoScale={prefs.autoScale}
          neon={prefs.neon}
          chartStyle={prefs.style}
          livePrice={livePrice}
          resetSignal={resetSignal}
          onExportRef={handleExportRef}
        />
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
          <div
            className="pc-mini-gauge"
            style={{ ["--pct" as any]: confidence, ["--tone" as any]: `var(--c-${directionTone})` }}
          >
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
