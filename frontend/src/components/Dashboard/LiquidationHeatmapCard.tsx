import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Flame, Gauge, Magnet, Radar, TrendingDown, TrendingUp } from "lucide-react";
import { api } from "../../services/api";
import { fmtCompact, fmtNum, fmtPct } from "../../lib/format";

/**
 * Self-polls independently of useAppData's global 10s dashboard cycle (own
 * fetch loop below) so a heatmap refresh never re-renders the rest of the
 * dashboard, and the canvas redraw loop below runs off refs rather than
 * React state so animation never triggers a React re-render at all.
 */
const REFRESH_MS = 7000;
const MAX_COLUMNS = 48;
const EXTREME_THRESHOLD = 0.7;
const GLOW_COLUMN_LOOKBACK = 6;
const PRICE_EASE = 0.08;

type Cluster = {
  price: number;
  long_notional: number;
  short_notional: number;
  intensity: number;
  strength: "LOW" | "MEDIUM" | "HIGH" | "EXTREME";
  dominant_side: "LONG" | "SHORT" | "BALANCED";
  leverage_zone: string | null;
};

type Heatmap = {
  symbol: string;
  generated_at: number;
  current_price: number | null;
  atr: number | null;
  price_low: number | null;
  price_high: number | null;
  clusters: Cluster[];
  largest_long_cluster: Cluster | null;
  largest_short_cluster: Cluster | null;
  largest_cluster: Cluster | null;
  nearest_long_cluster: Cluster | null;
  nearest_short_cluster: Cluster | null;
  liquidity_imbalance_pct: number | null;
  magnet_price: number | null;
  stop_hunt_zone: { price: number; side: string; strength: string } | null;
  risk_score: number | null;
  recent_liquidations: { count: number; notional: number; pressure: string };
  data_source: string;
  error?: string;
};

type Column = { clusters: Cluster[]; priceLow: number; priceHigh: number };
type HoverInfo = { x: number; y: number; cluster: Cluster; distancePct: number };
type Props = { symbol: string };

// Blue -> purple -> cyan -> orange -> red, matching the rest of QuantX's theme;
// intensity 0 fades into the card's own navy background rather than pure black.
const COLOR_STOPS: [number, [number, number, number]][] = [
  [0.0, [10, 14, 30]],
  [0.15, [59, 110, 249]],
  [0.35, [124, 92, 255]],
  [0.55, [0, 245, 212]],
  [0.75, [255, 166, 77]],
  [1.0, [255, 93, 115]],
];

function intensityColor(t: number, alpha = 1): string {
  const clamped = Math.max(0, Math.min(1, t));
  for (let i = 0; i < COLOR_STOPS.length - 1; i++) {
    const [t0, c0] = COLOR_STOPS[i];
    const [t1, c1] = COLOR_STOPS[i + 1];
    if (clamped >= t0 && clamped <= t1) {
      const f = (clamped - t0) / (t1 - t0 || 1);
      const r = Math.round(c0[0] + (c1[0] - c0[0]) * f);
      const g = Math.round(c0[1] + (c1[1] - c0[1]) * f);
      const b = Math.round(c0[2] + (c1[2] - c0[2]) * f);
      return `rgba(${r},${g},${b},${alpha})`;
    }
  }
  const [r, g, b] = COLOR_STOPS[COLOR_STOPS.length - 1][1];
  return `rgba(${r},${g},${b},${alpha})`;
}

function nearestCluster(clusters: Cluster[], price: number): Cluster {
  let best = clusters[0];
  let bestDist = Infinity;
  for (const c of clusters) {
    const d = Math.abs(c.price - price);
    if (d < bestDist) {
      bestDist = d;
      best = c;
    }
  }
  return best;
}

/** Remaps every buffered column onto the most recent column's price grid
 * (via nearest-price lookup) so small ATR-driven drift in the live window
 * between polls doesn't misalign rows. */
function buildRenderGrid(columns: Column[], numRows: number): Cluster[][] {
  if (!columns.length) return [];
  const target = columns[columns.length - 1];
  const bucketSize = (target.priceHigh - target.priceLow) / numRows;
  return columns.map((col) => {
    const rows: Cluster[] = [];
    for (let r = 0; r < numRows; r++) {
      const price = target.priceLow + (r + 0.5) * bucketSize;
      rows.push(nearestCluster(col.clusters, price));
    }
    return rows;
  });
}

function fmtUsdCompact(n?: number | null): string {
  if (typeof n !== "number" || Number.isNaN(n)) return "—";
  return `$${fmtCompact(n)}`;
}

function strengthClass(strength?: string | null): string {
  if (strength === "EXTREME") return "red";
  if (strength === "HIGH") return "yellow";
  return "";
}

function LiquidationHeatmapCard({ symbol }: Props) {
  const [snapshot, setSnapshot] = useState<Heatmap | null>(null);
  const [hover, setHover] = useState<HoverInfo | null>(null);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const offscreenRef = useRef<HTMLCanvasElement | null>(null);
  const columnsRef = useRef<Column[]>([]);
  const gridRef = useRef<Cluster[][]>([]);
  const dprRef = useRef(1);
  const sizeRef = useRef({ w: 0, h: 0 });
  const displayedPriceRef = useRef<number | null>(null);
  const targetPriceRef = useRef<number | null>(null);
  const pulsePhaseRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const needsRegenRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    columnsRef.current = [];
    gridRef.current = [];
    displayedPriceRef.current = null;
    targetPriceRef.current = null;
    setSnapshot(null);
    setHover(null);

    async function poll() {
      const data: Heatmap | null = await api.liquidationHeatmap(symbol).catch(() => null);
      if (cancelled || !data) return;

      if (data.current_price != null && data.price_low != null && data.price_high != null) {
        const column: Column = { clusters: data.clusters, priceLow: data.price_low, priceHigh: data.price_high };
        if (columnsRef.current.length === 0) {
          // seed the whole visible width with this first real snapshot so the
          // chart reads as a full heatmap immediately, instead of a couple of
          // sparse bars — every seeded column is the same real data, not a
          // fabricated one; genuine per-poll history overwrites it left-to-right
          columnsRef.current = Array.from({ length: MAX_COLUMNS }, () => column);
        } else {
          columnsRef.current.push(column);
          if (columnsRef.current.length > MAX_COLUMNS) columnsRef.current.shift();
        }
        gridRef.current = buildRenderGrid(columnsRef.current, data.clusters.length || 80);
        needsRegenRef.current = true;
        targetPriceRef.current = data.current_price;
        if (displayedPriceRef.current == null) displayedPriceRef.current = data.current_price;
      }
      setSnapshot(data);
    }

    poll();
    const id = window.setInterval(poll, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [symbol]);

  // Resize handling — observes the canvas itself (its CSS width can differ
  // from the wrapper's once the mobile min-width/horizontal-scroll rule kicks in).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const observer = new ResizeObserver(() => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (w === 0 || h === 0) return;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      dprRef.current = dpr;
      sizeRef.current = { w, h };

      const offscreen = offscreenRef.current || document.createElement("canvas");
      offscreen.width = canvas.width;
      offscreen.height = canvas.height;
      offscreenRef.current = offscreen;

      needsRegenRef.current = true;
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  // Continuous rAF loop: heavy per-cell fills only happen when data/size
  // change (regenerateOffscreen); every frame just blits that cached image
  // plus cheap animated overlays, keeping this at 60fps without touching React.
  useEffect(() => {
    let lastFrameTime = performance.now();

    function regenerateOffscreen() {
      const offscreen = offscreenRef.current;
      const { w, h } = sizeRef.current;
      const dpr = dprRef.current;
      if (!offscreen || w === 0 || h === 0) return;

      const octx = offscreen.getContext("2d");
      if (!octx) return;
      octx.setTransform(dpr, 0, 0, dpr, 0, 0);
      octx.clearRect(0, 0, w, h);

      const bg = octx.createLinearGradient(0, 0, 0, h);
      bg.addColorStop(0, "#0d1224");
      bg.addColorStop(1, "#05060a");
      octx.fillStyle = bg;
      octx.fillRect(0, 0, w, h);

      const grid = gridRef.current;
      const numCols = grid.length;
      const numRows = numCols ? grid[0].length : 0;
      if (numCols === 0 || numRows === 0) return;

      const scaleWidth = w - 56; // reserve right-hand gutter for the price scale
      const colWidth = scaleWidth / numCols;
      const rowHeight = h / numRows;

      for (let ci = 0; ci < numCols; ci++) {
        for (let ri = 0; ri < numRows; ri++) {
          const cell = grid[ci][ri];
          if (cell.intensity <= 0.02) continue;
          const rowFromTop = numRows - 1 - ri;
          octx.fillStyle = intensityColor(cell.intensity, 0.12 + cell.intensity * 0.75);
          octx.fillRect(ci * colWidth, rowFromTop * rowHeight, colWidth + 1, rowHeight + 1);
        }
      }

      const lastColumn = columnsRef.current[columnsRef.current.length - 1];
      if (lastColumn) {
        octx.strokeStyle = "rgba(255,255,255,0.06)";
        octx.fillStyle = "#8b90a8";
        octx.font = "10px Inter, sans-serif";
        octx.textAlign = "left";
        const ticks = 5;
        for (let i = 0; i <= ticks; i++) {
          const y = (h / ticks) * i;
          const price = lastColumn.priceHigh - (i / ticks) * (lastColumn.priceHigh - lastColumn.priceLow);
          octx.beginPath();
          octx.moveTo(0, y);
          octx.lineTo(scaleWidth, y);
          octx.stroke();
          octx.fillText(price >= 1000 ? price.toFixed(0) : price.toFixed(2), scaleWidth + 6, Math.min(Math.max(y, 10), h - 4));
        }
      }
    }

    function tick(now: number) {
      const dt = Math.min(0.1, (now - lastFrameTime) / 1000);
      lastFrameTime = now;
      pulsePhaseRef.current += dt * 2.4;

      if (needsRegenRef.current) {
        regenerateOffscreen();
        needsRegenRef.current = false;
      }

      const canvas = canvasRef.current;
      const offscreen = offscreenRef.current;
      const { w, h } = sizeRef.current;
      const grid = gridRef.current;

      if (canvas && offscreen && w > 0 && h > 0) {
        const ctx = canvas.getContext("2d");
        if (ctx) {
          const dpr = dprRef.current;
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          ctx.clearRect(0, 0, w, h);
          ctx.drawImage(offscreen, 0, 0, offscreen.width, offscreen.height, 0, 0, w, h);

          const scaleWidth = w - 56;
          const lastColumn = columnsRef.current[columnsRef.current.length - 1];

          if (grid.length && lastColumn) {
            const numCols = grid.length;
            const numRows = grid[0].length;
            const colWidth = scaleWidth / numCols;
            const rowHeight = h / numRows;
            const glowAlpha = 0.3 + 0.4 * (0.5 + 0.5 * Math.sin(pulsePhaseRef.current));

            // pulsing glow on every currently-extreme cell in the most recent
            // columns — the "live edge" of the heatmap, not the full history
            const fromCol = Math.max(0, numCols - GLOW_COLUMN_LOOKBACK);
            ctx.save();
            ctx.shadowColor = intensityColor(1, 0.9);
            ctx.shadowBlur = 16;
            for (let ci = fromCol; ci < numCols; ci++) {
              for (let ri = 0; ri < numRows; ri++) {
                if (grid[ci][ri].intensity < EXTREME_THRESHOLD) continue;
                const rowFromTop = numRows - 1 - ri;
                ctx.fillStyle = intensityColor(1, glowAlpha);
                ctx.fillRect(ci * colWidth, rowFromTop * rowHeight, colWidth + 1, rowHeight + 1);
              }
            }
            ctx.restore();
          }

          // animated current-price line, eased toward the latest tick
          if (targetPriceRef.current != null && lastColumn) {
            if (displayedPriceRef.current == null) displayedPriceRef.current = targetPriceRef.current;
            displayedPriceRef.current += (targetPriceRef.current - displayedPriceRef.current) * PRICE_EASE;

            const range = lastColumn.priceHigh - lastColumn.priceLow || 1;
            const t = (lastColumn.priceHigh - displayedPriceRef.current) / range;
            const y = Math.max(0, Math.min(h, t * h));
            const dash = 6 + 2 * Math.sin(pulsePhaseRef.current * 0.8);

            ctx.save();
            ctx.strokeStyle = "#00f5d4";
            ctx.lineWidth = 1.5;
            ctx.setLineDash([dash, 4]);
            ctx.shadowColor = "rgba(0,245,212,0.8)";
            ctx.shadowBlur = 6;
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(scaleWidth, y);
            ctx.stroke();
            ctx.restore();

            ctx.fillStyle = "#00f5d4";
            ctx.font = "bold 10px Inter, sans-serif";
            ctx.textAlign = "left";
            ctx.fillText(
              displayedPriceRef.current >= 1000 ? displayedPriceRef.current.toFixed(0) : displayedPriceRef.current.toFixed(2),
              scaleWidth + 6,
              Math.min(Math.max(y + 3, 10), h - 4)
            );
          }
        }
      }

      rafRef.current = requestAnimationFrame(tick);
    }

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  function handlePointer(clientX: number, clientY: number) {
    const canvas = canvasRef.current;
    const grid = gridRef.current;
    const lastColumn = columnsRef.current[columnsRef.current.length - 1];
    if (!canvas || !grid.length || !lastColumn || snapshot?.current_price == null) return;

    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const scaleWidth = rect.width - 56;
    if (x < 0 || x > scaleWidth || y < 0 || y > rect.height) {
      setHover(null);
      return;
    }

    const numCols = grid.length;
    const numRows = grid[0].length;
    const colIndex = Math.max(0, Math.min(numCols - 1, Math.floor((x / scaleWidth) * numCols)));
    const rowFromTop = Math.max(0, Math.min(numRows - 1, Math.floor((y / rect.height) * numRows)));
    const rowIndex = numRows - 1 - rowFromTop;
    const cluster = grid[colIndex][rowIndex];

    const distancePct = ((cluster.price - snapshot.current_price) / snapshot.current_price) * 100;
    setHover({ x, y, cluster, distancePct });
  }

  const legendStops = useMemo(
    () => [
      { label: "Low Liquidity", t: 0.1 },
      { label: "Medium Liquidity", t: 0.3 },
      { label: "High Liquidity", t: 0.55 },
      { label: "Extreme Liquidity", t: 0.85 },
    ],
    []
  );

  const sourceLabel = snapshot?.data_source === "unavailable" ? "Reconnecting…" : "Estimated · Live Binance Data";
  const tooltipMaxLeft = (canvasRef.current?.clientWidth || 300) - 168;

  return (
    <div className="liq-heatmap">
      <div className="liq-heatmap-toolbar">
        <span className="liq-heatmap-source">
          <Radar size={12} /> {sourceLabel}
        </span>
        {snapshot?.symbol && <span className="liq-heatmap-symbol">{snapshot.symbol}</span>}
      </div>

      <div className="liq-heatmap-stats-row">
        <div className="liq-stat">
          <span className="tile-label">Current Price</span>
          <b>{snapshot?.current_price != null ? fmtNum(snapshot.current_price, 2) : "—"}</b>
        </div>
        <div className="liq-stat">
          <span className="tile-label">
            <TrendingDown size={11} /> Nearest Long
          </span>
          <b className="green">{snapshot?.nearest_long_cluster ? fmtNum(snapshot.nearest_long_cluster.price, 2) : "—"}</b>
        </div>
        <div className="liq-stat">
          <span className="tile-label">
            <TrendingUp size={11} /> Nearest Short
          </span>
          <b className="red">{snapshot?.nearest_short_cluster ? fmtNum(snapshot.nearest_short_cluster.price, 2) : "—"}</b>
        </div>
        <div className="liq-stat">
          <span className="tile-label">Largest Cluster</span>
          <b>{snapshot?.largest_cluster ? fmtNum(snapshot.largest_cluster.price, 2) : "—"}</b>
        </div>
        <div className="liq-stat">
          <span className="tile-label">Distance</span>
          <b>
            {snapshot?.largest_cluster && snapshot.current_price
              ? fmtPct(((snapshot.largest_cluster.price - snapshot.current_price) / snapshot.current_price) * 100, 2)
              : "—"}
          </b>
        </div>
      </div>

      <div className="liq-heatmap-frame">
        <div
          className="liq-heatmap-canvas-wrap"
          onMouseMove={(e) => handlePointer(e.clientX, e.clientY)}
          onMouseLeave={() => setHover(null)}
          onTouchStart={(e) => e.touches[0] && handlePointer(e.touches[0].clientX, e.touches[0].clientY)}
          onTouchMove={(e) => e.touches[0] && handlePointer(e.touches[0].clientX, e.touches[0].clientY)}
        >
          <canvas ref={canvasRef} className="liq-heatmap-canvas" />
          {!snapshot?.clusters?.length && (
            <div className="liq-heatmap-empty">
              <Flame size={22} />
              <span>Building live liquidation clusters…</span>
            </div>
          )}
          {hover && (
            <div className="liq-heatmap-tooltip" style={{ left: Math.min(hover.x + 12, Math.max(tooltipMaxLeft, 0)), top: Math.max(hover.y - 8, 0) }}>
              <b>{fmtNum(hover.cluster.price, 2)}</b>
              <span>
                Est. Liquidation Value <b>{fmtUsdCompact(hover.cluster.long_notional + hover.cluster.short_notional)}</b>
              </span>
              <span>
                Leverage Zone <b>{hover.cluster.leverage_zone || "—"}</b>
              </span>
              <span>
                Strength <b className={strengthClass(hover.cluster.strength)}>{hover.cluster.strength}</b>
              </span>
              <span>
                Distance <b className={hover.distancePct >= 0 ? "green" : "red"}>{fmtPct(hover.distancePct, 2)}</b>
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="liq-heatmap-legend">
        {legendStops.map((s) => (
          <span key={s.label} className="liq-legend-item">
            <i style={{ background: intensityColor(s.t, 0.9) }} />
            {s.label}
          </span>
        ))}
      </div>

      <div className="liq-heatmap-insights">
        <div className="liq-insight-tile">
          <span className="tile-label">Largest Long Cluster</span>
          <b className="green">{snapshot?.largest_long_cluster ? fmtNum(snapshot.largest_long_cluster.price, 2) : "—"}</b>
          <span className="sentiment-sub">{fmtUsdCompact(snapshot?.largest_long_cluster?.long_notional)}</span>
        </div>
        <div className="liq-insight-tile">
          <span className="tile-label">Largest Short Cluster</span>
          <b className="red">{snapshot?.largest_short_cluster ? fmtNum(snapshot.largest_short_cluster.price, 2) : "—"}</b>
          <span className="sentiment-sub">{fmtUsdCompact(snapshot?.largest_short_cluster?.short_notional)}</span>
        </div>
        <div className="liq-insight-tile">
          <span className="tile-label">Liquidity Imbalance</span>
          <b className={(snapshot?.liquidity_imbalance_pct ?? 0) >= 0 ? "green" : "red"}>
            {snapshot?.liquidity_imbalance_pct != null ? fmtPct(snapshot.liquidity_imbalance_pct, 1) : "—"}
          </b>
          <span className="sentiment-sub">{(snapshot?.liquidity_imbalance_pct ?? 0) >= 0 ? "Long Heavy" : "Short Heavy"}</span>
        </div>
        <div className="liq-insight-tile">
          <span className="tile-label">
            <Magnet size={11} /> Magnet Price
          </span>
          <b>{snapshot?.magnet_price != null ? fmtNum(snapshot.magnet_price, 2) : "—"}</b>
        </div>
        <div className="liq-insight-tile">
          <span className="tile-label">Stop Hunt Zone</span>
          <b className={snapshot?.stop_hunt_zone?.side === "SHORT" ? "red" : "green"}>
            {snapshot?.stop_hunt_zone ? fmtNum(snapshot.stop_hunt_zone.price, 2) : "—"}
          </b>
          <span className="sentiment-sub">{snapshot?.stop_hunt_zone?.strength || "—"}</span>
        </div>
        <div className="liq-insight-tile">
          <span className="tile-label">
            <Gauge size={11} /> Risk Score
          </span>
          <b className={(snapshot?.risk_score ?? 0) >= 65 ? "red" : (snapshot?.risk_score ?? 0) >= 35 ? "yellow" : "green"}>
            {snapshot?.risk_score != null ? fmtNum(snapshot.risk_score, 0) : "—"}
          </b>
        </div>
      </div>
    </div>
  );
}

export default memo(LiquidationHeatmapCard);
