import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";

export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  label?: string;
};

const POLL_MS = 10000;

export function useAppData(authed: boolean | null) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setInterval_] = useState("15m");

  const [dashboard, setDashboard] = useState<any>(null);
  const [prediction, setPrediction] = useState<any>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [orderbook, setOrderbook] = useState<any>(null);
  const [trades, setTrades] = useState<any[]>([]);
  const [portfolio, setPortfolio] = useState<any>(null);
  const [positions, setPositions] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [backtest, setBacktest] = useState<any>(null);
  const [marketContext, setMarketContext] = useState<any>(null);
  const [timeframesConsensus, setTimeframesConsensus] = useState<any>(null);
  const [strategyWeights, setStrategyWeights] = useState<any>(null);
  const [botStatus, setBotStatus] = useState<any>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [toast, setToast] = useState("");
  const [loading, setLoading] = useState(false);

  const toastTimer = useRef<number | null>(null);

  function showToast(message: string) {
    setToast(message);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(""), 3000);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [dash, predRes, candleRows] = await Promise.all([
        api.dashboard(),
        api.prediction(symbol, interval),
        api.candles(symbol, interval, 220),
      ]);

      setDashboard(dash);
      setPrediction(predRes.prediction);
      setCandles(
        (candleRows || []).map((x: Candle) => ({
          ...x,
          label: new Date(x.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        }))
      );

      const [ob, tr, pf, pos, hist, ctx, tf, weights, bot] = await Promise.all([
        api.orderbook(symbol, 10).catch(() => null),
        api.trades(symbol, 20).catch(() => ({ trades: [] })),
        api.portfolio().catch(() => null),
        api.positions().catch(() => ({ positions: [] })),
        api.history().catch(() => ({ trades: [] })),
        api.marketContext(symbol).catch(() => null),
        api.timeframes(symbol).catch(() => null),
        api.strategyWeights().catch(() => null),
        api.botStatus().catch(() => null),
      ]);

      setOrderbook(ob);
      setTrades(tr?.trades || []);
      setPortfolio(pf);
      setPositions(pos?.positions || []);
      setHistory(hist?.trades || []);
      setMarketContext(ctx);
      setTimeframesConsensus(tf);
      setStrategyWeights(weights);
      setBotStatus(bot);
      setLastUpdated(new Date());
    } finally {
      setLoading(false);
    }
  }, [symbol, interval]);

  async function runBacktest(bt_interval = "5m") {
    const data = await api.runBacktest(symbol, bt_interval);
    const results = data.results || data;
    if (results?.equity_curve) {
      results.chart = results.equity_curve.map((v: number, i: number) => ({ trade: i, equity: v }));
    }
    setBacktest(results);
    return results;
  }

  async function openPaperTrade(side: "LONG" | "SHORT") {
    const res = await api.openPaperTrade(symbol, side, 1000);
    showToast(res?.message || `${side} opened`);
    await load();
  }

  async function closePaperTrade(id: number) {
    const res = await api.closePaperTrade(id);
    showToast(res?.message || "Trade closed");
    await load();
  }

  async function botAction(action: string) {
    const data = await api.botAction(action);
    showToast(data.message || `Bot ${action}`);
    setBotStatus(data.state || null);
  }

  useEffect(() => {
    if (!authed) return;
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, interval, authed]);

  return {
    symbol,
    setSymbol,
    interval,
    setInterval: setInterval_,
    dashboard,
    prediction,
    candles,
    orderbook,
    trades,
    portfolio,
    positions,
    history,
    backtest,
    marketContext,
    timeframesConsensus,
    strategyWeights,
    botStatus,
    lastUpdated,
    toast,
    showToast,
    loading,
    load,
    runBacktest,
    openPaperTrade,
    closePaperTrade,
    botAction,
  };
}

export type AppData = ReturnType<typeof useAppData>;
