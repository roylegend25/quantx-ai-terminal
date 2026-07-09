import { useEffect, useRef, useState } from "react";
import { CandlestickChart, Maximize2, Pause, Play, RotateCcw, X } from "lucide-react";
import BacktestChart, { type BtOverlays, type BtPrediction } from "./BacktestChart";
import type { Candle } from "../../hooks/useAppData";
import type { BtTrade } from "../../lib/backtestStats";

const TOGGLES: { key: keyof BtOverlays; label: string }[] = [
  { key: "trades", label: "Trades" },
  { key: "predictions", label: "Predictions" },
  { key: "bands", label: "Bands" },
  { key: "indicators", label: "Indicators" },
  { key: "heatmap", label: "Heatmap" },
  { key: "volume", label: "Volume" },
];

const LEGEND = [
  { label: "EMA20", cls: "cyan" },
  { label: "EMA50", cls: "purple" },
  { label: "EMA200", cls: "yellow" },
  { label: "VWAP", cls: "blue" },
];

type Props = {
  symbol: string;
  timeframe: string;
  candles: Candle[];
  candlesLoading: boolean;
  candlesError: string | null;
  trades: BtTrade[];
  predictions: BtPrediction[];
};

export default function ChartPanel({
  symbol,
  timeframe,
  candles,
  candlesLoading,
  candlesError,
  trades,
  predictions,
}: Props) {
  const [overlays, setOverlays] = useState<BtOverlays>({
    trades: true,
    predictions: false,
    bands: false,
    indicators: true,
    heatmap: false,
    volume: true,
  });
  const [fullscreen, setFullscreen] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [replayIndex, setReplayIndex] = useState(0);
  const replayTimer = useRef<number | null>(null);

  const stopReplay = () => {
    if (replayTimer.current) window.clearInterval(replayTimer.current);
    replayTimer.current = null;
    setReplaying(false);
  };

  const startReplay = () => {
    if (!candles.length) return;
    stopReplay();
    setReplaying(true);
    setReplayIndex((idx) => (idx > 2 && idx < candles.length ? idx : Math.max(2, Math.floor(candles.length * 0.1))));
    replayTimer.current = window.setInterval(() => {
      setReplayIndex((idx) => {
        if (idx >= candles.length) {
          stopReplay();
          return candles.length;
        }
        return idx + Math.max(1, Math.floor(candles.length / 600));
      });
    }, 40);
  };

  useEffect(() => stopReplay, []);
  // dataset changed → exit replay
  useEffect(() => {
    stopReplay();
    setReplayIndex(0);
  }, [candles]);

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setFullscreen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  const hasPredictionData = predictions.length > 0;

  const body = (
    <div className={`bt-chart-panel ${fullscreen ? "bt-chart-fullscreen" : ""}`}>
      <div className="bt-chart-toolbar">
        <div className="bt-chart-title">
          <CandlestickChart size={15} />
          <span>
            {symbol} · {timeframe.toUpperCase()}
          </span>
          {overlays.indicators && (
            <div className="bt-chart-legend">
              {LEGEND.map((l) => (
                <span className="bt-legend-item" key={l.label}>
                  <i className={`bt-legend-swatch ${l.cls}`} />
                  {l.label}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="bt-chart-actions">
          {TOGGLES.map((t) => (
            <button
              key={t.key}
              type="button"
              className={`bt-chip ${overlays[t.key] ? "active" : ""}`}
              title={
                (t.key === "predictions" || t.key === "bands") && !hasPredictionData
                  ? "No stored AI predictions overlap this window"
                  : undefined
              }
              onClick={() => setOverlays((o) => ({ ...o, [t.key]: !o[t.key] }))}
            >
              {t.label}
            </button>
          ))}
          {replaying ? (
            <button type="button" className="bt-chip active" onClick={stopReplay} title="Pause replay">
              <Pause size={13} /> Replay
            </button>
          ) : (
            <button
              type="button"
              className="bt-chip"
              onClick={startReplay}
              disabled={!candles.length}
              title="Replay the backtest bar by bar"
            >
              <Play size={13} /> Replay
            </button>
          )}
          {replayIndex > 0 && !replaying && (
            <button type="button" className="bt-chip" onClick={() => setReplayIndex(0)} title="Reset replay">
              <RotateCcw size={13} />
            </button>
          )}
          <button
            type="button"
            className="bt-chip"
            onClick={() => setFullscreen((f) => !f)}
            title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
          >
            {fullscreen ? <X size={13} /> : <Maximize2 size={13} />}
          </button>
        </div>
      </div>

      {candlesLoading ? (
        <div className="bt-chart-empty">
          <div className="bt-skeleton" style={{ height: "70%", width: "94%" }} />
        </div>
      ) : candlesError ? (
        <div className="bt-chart-empty">
          <p className="analytics-empty">{candlesError}</p>
        </div>
      ) : candles.length < 2 ? (
        <div className="bt-chart-empty">
          <p className="analytics-empty">
            No stored candles for {symbol} {timeframe.toUpperCase()} — download the dataset from the
            Research Lab's Data Engine, then run a backtest.
          </p>
        </div>
      ) : (
        <BacktestChart
          candles={candles}
          trades={trades}
          predictions={predictions}
          overlays={overlays}
          replaying={replaying || replayIndex > 0}
          replayIndex={replayIndex || candles.length}
          height={fullscreen ? window.innerHeight - 120 : 460}
        />
      )}

      {(overlays.predictions || overlays.bands) && !hasPredictionData && candles.length > 1 && (
        <p className="bt-note">
          No stored AI predictions overlap this candle window — the overlay stays empty rather than
          fabricating a forecast.
        </p>
      )}
    </div>
  );

  return body;
}
