// Purpose-built canvas chart for the Backtesting terminal.
//
// Renders the REAL candles the backtest replayed (validated market_candles
// store via /api/data/candles) with the run's actual trades overlaid:
// entry/exit markers, stop-loss / take-profit exit glyphs, shaded holding
// regions, EMA20/50/200 + VWAP, a volume pane, a volume-profile heatmap,
// stored AI prediction overlays (/api/prediction/history), crosshair +
// tooltip, wheel/pinch zoom, drag pan, a mini navigator and a bar-by-bar
// replay mode. Single rAF loop with a dirty flag, canvas sized to
// devicePixelRatio — same performance constraints as ProChartCanvas.

import { memo, useEffect, useMemo, useRef } from "react";
import type { Candle } from "../../hooks/useAppData";
import { closes, ema, volumeProfile, vwap } from "../../lib/chartIndicators";
import type { BtTrade } from "../../lib/backtestStats";

export type BtOverlays = {
  trades: boolean;
  predictions: boolean;
  bands: boolean;
  indicators: boolean;
  heatmap: boolean;
  volume: boolean;
};

export type BtPrediction = {
  timestamp: number;
  direction: string | null;
  predicted_price: number | null;
  target: number | null;
  stop: number | null;
  confidence: number | null;
};

type Props = {
  candles: Candle[];
  trades: BtTrade[];
  predictions: BtPrediction[];
  overlays: BtOverlays;
  replaying: boolean;
  replayIndex: number; // candles revealed while replaying (ignored when !replaying)
  height?: number;
};

const FONT = "Inter, ui-sans-serif, system-ui, sans-serif";
const AXIS_W = 64;
const NAV_H = 34;
const GAP = 6;

type View = { end: number; bars: number };

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function niceStep(range: number, maxTicks: number): number {
  const rough = range / Math.max(1, maxTicks);
  const pow = Math.pow(10, Math.floor(Math.log10(rough || 1)));
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (rough <= m * pow) return m * pow;
  }
  return 10 * pow;
}

function fmtPrice(p: number): string {
  if (!Number.isFinite(p)) return "";
  const abs = Math.abs(p);
  const digits = abs >= 10000 ? 0 : abs >= 100 ? 1 : abs >= 1 ? 2 : 5;
  return p.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

function fmtTime(ms: number, spanMs: number): string {
  const d = new Date(ms);
  if (spanMs > 90 * 86400_000) return d.toLocaleDateString([], { month: "short", year: "2-digit" });
  if (spanMs > 3 * 86400_000) return d.toLocaleDateString([], { month: "short", day: "numeric" });
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Binary search: index of the last candle whose time <= t (-1 if before). */
function idxAtTime(candles: Candle[], t: number): number {
  let lo = 0;
  let hi = candles.length - 1;
  if (!candles.length || t < candles[0].time) return -1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (candles[mid].time <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

type TradeMark = {
  entryIdx: number;
  exitIdx: number;
  trade: BtTrade;
};

function BacktestChartInner({ candles, trades, predictions, overlays, replaying, replayIndex, height = 460 }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  const viewRef = useRef<View>({ end: 0, bars: 0 });
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  const dragRef = useRef<{ startX: number; startEnd: number; nav: boolean } | null>(null);
  const dirtyRef = useRef(true);
  const rafRef = useRef(0);
  const sizeRef = useRef({ w: 0, h: 0 });

  const calc = useMemo(() => {
    if (candles.length < 2) return null;
    const c = closes(candles);
    return {
      ema20: ema(c, 20),
      ema50: ema(c, 50),
      ema200: ema(c, 200),
      vwap: vwap(candles),
      profile: volumeProfile(candles, 26, Math.min(600, candles.length)),
      maxVolume: candles.reduce((m, x) => Math.max(m, x.volume || 0), 0),
    };
  }, [candles]);

  const marks = useMemo<TradeMark[]>(() => {
    if (!candles.length) return [];
    const out: TradeMark[] = [];
    for (const t of trades) {
      const et = t.entry_time ? new Date(t.entry_time).getTime() : NaN;
      const xt = t.exit_time ? new Date(t.exit_time).getTime() : NaN;
      const entryIdx = Number.isFinite(et) ? idxAtTime(candles, et) : -1;
      const exitIdx = Number.isFinite(xt) ? idxAtTime(candles, xt) : entryIdx;
      if (entryIdx >= 0) out.push({ entryIdx, exitIdx: Math.max(exitIdx, entryIdx), trade: t });
    }
    return out;
  }, [candles, trades]);

  const predMarks = useMemo(() => {
    if (!candles.length) return [] as { idx: number; p: BtPrediction }[];
    return predictions
      .map((p) => ({ idx: idxAtTime(candles, p.timestamp), p }))
      .filter((m) => m.idx >= 0);
  }, [candles, predictions]);

  // reset view when the dataset changes
  useEffect(() => {
    viewRef.current = { end: candles.length, bars: Math.min(220, Math.max(30, candles.length)) };
    dirtyRef.current = true;
  }, [candles]);

  useEffect(() => {
    dirtyRef.current = true;
  }, [overlays, marks, predMarks, replaying, replayIndex]);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const ro = new ResizeObserver(() => {
      const rect = wrap.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      sizeRef.current = { w: rect.width, h: rect.height };
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      dirtyRef.current = true;
    });
    ro.observe(wrap);

    const colors = () => ({
      purple: cssVar("--c-purple", "#7c5cff"),
      cyan: cssVar("--c-cyan", "#00f5d4"),
      blue: cssVar("--c-blue", "#00a8ff"),
      green: cssVar("--c-green", "#00f5a0"),
      red: cssVar("--c-red", "#ff5d73"),
      yellow: cssVar("--c-yellow", "#ffd166"),
      purpleRgb: cssVar("--c-purple-rgb", "124, 92, 255"),
      cyanRgb: cssVar("--c-cyan-rgb", "0, 245, 212"),
    });

    const draw = () => {
      rafRef.current = requestAnimationFrame(draw);
      if (!dirtyRef.current) return;
      dirtyRef.current = false;

      const { w, h } = sizeRef.current;
      if (w < 40 || h < 60) return;
      const col = colors();

      const visibleCount = replaying ? Math.max(2, Math.min(replayIndex, candles.length)) : candles.length;
      const data = candles.slice(0, visibleCount);
      const view = viewRef.current;
      if (replaying) {
        // follow the replay head
        view.end = visibleCount;
      }
      view.bars = Math.max(15, Math.min(view.bars || 220, Math.max(15, data.length)));
      view.end = Math.max(Math.min(view.bars, data.length), Math.min(view.end, data.length));
      const start = Math.max(0, view.end - view.bars);
      const slice = data.slice(start, view.end);

      ctx.clearRect(0, 0, w, h);
      if (slice.length < 2) return;

      const plotW = w - AXIS_W;
      const navTop = h - NAV_H;
      const axisBottom = navTop - 18;
      const volH = overlays.volume ? Math.round(axisBottom * 0.16) : 0;
      const plotH = axisBottom - volH - GAP;

      let lo = Infinity;
      let hi = -Infinity;
      for (const c of slice) {
        lo = Math.min(lo, c.low);
        hi = Math.max(hi, c.high);
      }
      if (overlays.indicators && calc) {
        for (let i = start; i < view.end; i++) {
          for (const s of [calc.ema20, calc.ema50, calc.ema200]) {
            const v = s[i];
            if (Number.isFinite(v) && v > 0) {
              lo = Math.min(lo, v);
              hi = Math.max(hi, v);
            }
          }
        }
      }
      const pad = (hi - lo) * 0.06 || hi * 0.005 || 1;
      lo -= pad;
      hi += pad;

      const xAt = (i: number) => ((i - start + 0.5) / slice.length) * plotW;
      const yAt = (p: number) => plotH - ((p - lo) / (hi - lo)) * plotH;
      const barW = Math.max(1, (plotW / slice.length) * 0.66);

      // ---- grid ----
      ctx.strokeStyle = "rgba(255,255,255,0.05)";
      ctx.fillStyle = "rgba(139,144,168,0.9)";
      ctx.font = `10px ${FONT}`;
      ctx.lineWidth = 1;
      const step = niceStep(hi - lo, 6);
      const firstTick = Math.ceil(lo / step) * step;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      for (let p = firstTick; p <= hi; p += step) {
        const y = yAt(p);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(plotW, y);
        ctx.stroke();
        ctx.fillText(fmtPrice(p), plotW + 8, y);
      }
      // time ticks
      const spanMs = slice[slice.length - 1].time - slice[0].time;
      const tickEvery = Math.max(1, Math.floor(slice.length / Math.max(2, Math.floor(plotW / 90))));
      ctx.textAlign = "center";
      for (let i = 0; i < slice.length; i += tickEvery) {
        const x = xAt(start + i);
        ctx.fillText(fmtTime(slice[i].time, spanMs), x, axisBottom + 9);
      }

      // ---- trade holding regions ----
      if (overlays.trades) {
        for (const m of marks) {
          if (m.exitIdx < start || m.entryIdx > view.end - 1 || m.entryIdx >= visibleCount) continue;
          const x1 = xAt(Math.max(m.entryIdx, start)) - barW / 2;
          const x2 = xAt(Math.min(m.exitIdx, view.end - 1)) + barW / 2;
          const win = (Number(m.trade.pnl) || 0) >= 0;
          ctx.fillStyle = win ? "rgba(0,245,160,0.06)" : "rgba(255,93,115,0.06)";
          ctx.fillRect(x1, 0, Math.max(2, x2 - x1), plotH);
        }
      }

      // ---- volume-profile heatmap (right-anchored horizontal bins) ----
      if (overlays.heatmap && calc?.profile?.length) {
        const maxBin = Math.max(...calc.profile.map((b) => b.volume)) || 1;
        for (const bin of calc.profile) {
          const y1 = yAt(bin.priceHigh);
          const y2 = yAt(bin.priceLow);
          if (y2 < 0 || y1 > plotH) continue;
          const frac = bin.volume / maxBin;
          ctx.fillStyle = `rgba(${col.purpleRgb}, ${0.04 + frac * 0.2})`;
          const bw = frac * plotW * 0.24;
          ctx.fillRect(plotW - bw, Math.min(y1, y2), bw, Math.max(1.5, Math.abs(y2 - y1) - 1));
        }
      }

      // ---- volume pane ----
      if (overlays.volume && calc && calc.maxVolume > 0) {
        for (let i = 0; i < slice.length; i++) {
          const c = slice[i];
          const vh = ((c.volume || 0) / calc.maxVolume) * volH;
          const up = c.close >= c.open;
          ctx.fillStyle = up ? "rgba(0,245,160,0.32)" : "rgba(255,93,115,0.32)";
          ctx.fillRect(xAt(start + i) - barW / 2, plotH + GAP + (volH - vh), barW, vh);
        }
      }

      // ---- candles ----
      for (let i = 0; i < slice.length; i++) {
        const c = slice[i];
        const x = xAt(start + i);
        const up = c.close >= c.open;
        ctx.strokeStyle = up ? col.green : col.red;
        ctx.fillStyle = up ? col.green : col.red;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, yAt(c.high));
        ctx.lineTo(x, yAt(c.low));
        ctx.stroke();
        const yo = yAt(c.open);
        const yc = yAt(c.close);
        const top = Math.min(yo, yc);
        ctx.fillRect(x - barW / 2, top, barW, Math.max(1, Math.abs(yc - yo)));
      }

      // ---- indicator lines ----
      if (overlays.indicators && calc) {
        const lines: { s: ArrayLike<number>; color: string; dash?: number[] }[] = [
          { s: calc.ema20, color: col.cyan },
          { s: calc.ema50, color: col.purple },
          { s: calc.ema200, color: col.yellow },
          { s: calc.vwap, color: col.blue, dash: [5, 4] },
        ];
        for (const line of lines) {
          ctx.strokeStyle = line.color;
          ctx.lineWidth = 1.4;
          ctx.setLineDash(line.dash || []);
          ctx.beginPath();
          let started = false;
          for (let i = start; i < view.end; i++) {
            const v = line.s[i];
            if (!Number.isFinite(v) || v <= 0) continue;
            const x = xAt(i);
            const y = yAt(v);
            if (!started) {
              ctx.moveTo(x, y);
              started = true;
            } else ctx.lineTo(x, y);
          }
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }

      // ---- stored AI predictions ----
      if ((overlays.predictions || overlays.bands) && predMarks.length) {
        for (const m of predMarks) {
          if (m.idx < start || m.idx > view.end - 1 || m.idx >= visibleCount) continue;
          const x = xAt(m.idx);
          const px = m.p.predicted_price ?? m.p.target;
          if (overlays.bands && m.p.target != null && m.p.stop != null) {
            const yT = yAt(m.p.target);
            const yS = yAt(m.p.stop);
            const bandW = Math.min(barW * 8, plotW * 0.08);
            const grad = ctx.createLinearGradient(x, 0, x + bandW, 0);
            grad.addColorStop(0, `rgba(${col.cyanRgb}, 0.18)`);
            grad.addColorStop(1, `rgba(${col.cyanRgb}, 0)`);
            ctx.fillStyle = grad;
            ctx.fillRect(x, Math.min(yT, yS), bandW, Math.abs(yS - yT));
          }
          if (overlays.predictions && px != null) {
            const y = yAt(px);
            ctx.fillStyle = col.cyan;
            ctx.beginPath();
            ctx.moveTo(x, y - 5);
            ctx.lineTo(x + 5, y);
            ctx.lineTo(x, y + 5);
            ctx.lineTo(x - 5, y);
            ctx.closePath();
            ctx.fill();
          }
        }
      }

      // ---- trade markers ----
      if (overlays.trades) {
        for (const m of marks) {
          const t = m.trade;
          const win = (Number(t.pnl) || 0) >= 0;
          if (m.entryIdx >= start && m.entryIdx <= view.end - 1 && m.entryIdx < visibleCount) {
            const x = xAt(m.entryIdx);
            const y = yAt(t.entry_price);
            const long = t.side === "LONG";
            ctx.fillStyle = long ? col.green : col.red;
            ctx.beginPath();
            if (long) {
              ctx.moveTo(x, y + 4);
              ctx.lineTo(x - 5, y + 12);
              ctx.lineTo(x + 5, y + 12);
            } else {
              ctx.moveTo(x, y - 4);
              ctx.lineTo(x - 5, y - 12);
              ctx.lineTo(x + 5, y - 12);
            }
            ctx.closePath();
            ctx.fill();
          }
          if (m.exitIdx >= start && m.exitIdx <= view.end - 1 && m.exitIdx < visibleCount) {
            const x = xAt(m.exitIdx);
            const y = yAt(t.exit_price);
            const isSL = t.exit_reason === "stop_loss";
            const isTP = t.exit_reason === "take_profit";
            const color = isSL ? col.red : isTP ? col.green : win ? col.green : col.red;
            ctx.strokeStyle = color;
            ctx.fillStyle = color;
            ctx.lineWidth = 1.6;
            if (isSL) {
              // square = stop-loss exit
              ctx.strokeRect(x - 4, y - 4, 8, 8);
            } else if (isTP) {
              // diamond = take-profit exit
              ctx.beginPath();
              ctx.moveTo(x, y - 6);
              ctx.lineTo(x + 6, y);
              ctx.lineTo(x, y + 6);
              ctx.lineTo(x - 6, y);
              ctx.closePath();
              ctx.stroke();
            } else {
              // circle = signal flip / time exit
              ctx.beginPath();
              ctx.arc(x, y, 4, 0, Math.PI * 2);
              ctx.stroke();
            }
          }
        }
      }

      // ---- last price line ----
      const last = slice[slice.length - 1];
      const yLast = yAt(last.close);
      ctx.strokeStyle = `rgba(${col.purpleRgb}, 0.6)`;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(0, yLast);
      ctx.lineTo(plotW, yLast);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = col.purple;
      ctx.fillRect(plotW, yLast - 9, AXIS_W, 18);
      ctx.fillStyle = "#0b0c15";
      ctx.textAlign = "center";
      ctx.font = `700 10px ${FONT}`;
      ctx.fillText(fmtPrice(last.close), plotW + AXIS_W / 2, yLast);
      ctx.font = `10px ${FONT}`;

      // ---- navigator ----
      const navY = navTop + 2;
      const navH = NAV_H - 6;
      ctx.fillStyle = "rgba(255,255,255,0.03)";
      ctx.fillRect(0, navY, plotW, navH);
      if (data.length > 2) {
        let nLo = Infinity;
        let nHi = -Infinity;
        for (const c of data) {
          nLo = Math.min(nLo, c.close);
          nHi = Math.max(nHi, c.close);
        }
        const nY = (p: number) => navY + navH - ((p - nLo) / (nHi - nLo || 1)) * navH;
        ctx.strokeStyle = `rgba(${col.cyanRgb}, 0.6)`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        const step2 = Math.max(1, Math.floor(data.length / plotW));
        for (let i = 0; i < data.length; i += step2) {
          const x = (i / data.length) * plotW;
          const y = nY(data[i].close);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
        const wx1 = (start / data.length) * plotW;
        const wx2 = (view.end / data.length) * plotW;
        ctx.fillStyle = `rgba(${col.purpleRgb}, 0.16)`;
        ctx.fillRect(wx1, navY, Math.max(6, wx2 - wx1), navH);
        ctx.strokeStyle = `rgba(${col.purpleRgb}, 0.7)`;
        ctx.strokeRect(wx1, navY, Math.max(6, wx2 - wx1), navH);
      }

      // ---- crosshair + tooltip ----
      const tooltip = tooltipRef.current;
      const pt = pointerRef.current;
      if (pt && pt.x < plotW && pt.y < axisBottom && tooltip) {
        const i = Math.min(slice.length - 1, Math.max(0, Math.floor((pt.x / plotW) * slice.length)));
        const c = slice[i];
        const x = xAt(start + i);
        ctx.strokeStyle = "rgba(255,255,255,0.22)";
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, axisBottom);
        ctx.moveTo(0, pt.y);
        ctx.lineTo(plotW, pt.y);
        ctx.stroke();
        ctx.setLineDash([]);
        // y-axis price bubble
        const priceAt = lo + (1 - pt.y / plotH) * (hi - lo);
        if (pt.y <= plotH) {
          ctx.fillStyle = "rgba(255,255,255,0.14)";
          ctx.fillRect(plotW, pt.y - 8, AXIS_W, 16);
          ctx.fillStyle = "#fff";
          ctx.textAlign = "center";
          ctx.fillText(fmtPrice(priceAt), plotW + AXIS_W / 2, pt.y);
        }

        const mark = marks.find(
          (m) => overlays.trades && (m.entryIdx === start + i || m.exitIdx === start + i)
        );
        const rows = [
          `<div class="pc-tooltip-time">${new Date(c.time).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</div>`,
          `<div class="pc-tooltip-row"><span>O</span><b>${fmtPrice(c.open)}</b></div>`,
          `<div class="pc-tooltip-row"><span>H</span><b>${fmtPrice(c.high)}</b></div>`,
          `<div class="pc-tooltip-row"><span>L</span><b>${fmtPrice(c.low)}</b></div>`,
          `<div class="pc-tooltip-row"><span>C</span><b>${fmtPrice(c.close)}</b></div>`,
          overlays.volume ? `<div class="pc-tooltip-row"><span>Vol</span><b>${Intl.NumberFormat("en", { notation: "compact" }).format(c.volume || 0)}</b></div>` : "",
          mark
            ? `<div class="pc-tooltip-row"><span>${mark.trade.side}</span><b class="${(mark.trade.pnl ?? 0) >= 0 ? "green" : "red"}">${(mark.trade.pnl ?? 0) >= 0 ? "+" : ""}${(mark.trade.pnl ?? 0).toFixed(2)} · ${mark.trade.exit_reason.replace(/_/g, " ")}</b></div>`
            : "",
        ].join("");
        tooltip.innerHTML = rows;
        tooltip.style.display = "block";
        const tw = tooltip.offsetWidth || 130;
        tooltip.style.left = `${Math.min(plotW - tw - 8, Math.max(4, x + 12))}px`;
        tooltip.style.top = `${Math.max(4, Math.min(pt.y - 20, axisBottom - 120))}px`;
      } else if (tooltip) {
        tooltip.style.display = "none";
      }
    };

    rafRef.current = requestAnimationFrame(draw);

    // ---- interactions ----
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const view = viewRef.current;
      const factor = e.deltaY > 0 ? 1.12 : 0.9;
      const newBars = Math.round(Math.max(15, Math.min(candles.length, view.bars * factor)));
      // zoom anchored to cursor position
      const rect = canvas.getBoundingClientRect();
      const fx = Math.min(1, Math.max(0, (e.clientX - rect.left) / (rect.width - AXIS_W)));
      const anchor = view.end - view.bars + view.bars * fx;
      view.bars = newBars;
      view.end = Math.round(Math.min(candles.length, Math.max(newBars, anchor + newBars * (1 - fx))));
      dirtyRef.current = true;
    };
    const posOf = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };
    const onPointerDown = (e: PointerEvent) => {
      const p = posOf(e);
      const nav = p.y > sizeRef.current.h - NAV_H;
      dragRef.current = { startX: p.x, startEnd: viewRef.current.end, nav };
      canvas.setPointerCapture(e.pointerId);
    };
    const onPointerMove = (e: PointerEvent) => {
      const p = posOf(e);
      pointerRef.current = p;
      const drag = dragRef.current;
      if (drag) {
        const view = viewRef.current;
        const plotW = sizeRef.current.w - AXIS_W;
        if (drag.nav) {
          const frac = p.x / plotW;
          view.end = Math.round(Math.max(view.bars, Math.min(candles.length, frac * candles.length + view.bars / 2)));
        } else {
          const dBars = ((drag.startX - p.x) / plotW) * view.bars;
          view.end = Math.round(Math.max(view.bars, Math.min(candles.length, drag.startEnd + dBars)));
        }
      }
      dirtyRef.current = true;
    };
    const onPointerUp = () => {
      dragRef.current = null;
      dirtyRef.current = true;
    };
    const onLeave = () => {
      pointerRef.current = null;
      dirtyRef.current = true;
    };

    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointerleave", onLeave);

    return () => {
      cancelAnimationFrame(rafRef.current);
      ro.disconnect();
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointerleave", onLeave);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, calc, marks, predMarks, overlays, replaying, replayIndex]);

  return (
    <div className="bt-chart-wrap" ref={wrapRef} style={{ height }}>
      <canvas ref={canvasRef} className="bt-chart-canvas" />
      <div ref={tooltipRef} className="pc-tooltip" style={{ display: "none" }} />
    </div>
  );
}

const BacktestChart = memo(BacktestChartInner);
export default BacktestChart;
