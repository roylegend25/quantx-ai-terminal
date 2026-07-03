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

/**
 * Like useState, but skips the update (and the resulting re-render of every
 * memoized consumer) when the incoming value is deep-equal to the current
 * one. Poll-driven API responses are fresh objects every cycle even when the
 * underlying data hasn't changed, so a plain useState would defeat React.memo
 * on every dashboard card every POLL_MS.
 */
function useDedupedState<T>(initial: T): [T, (next: T) => void] {
  const [state, setState] = useState<T>(initial);
  const serialized = useRef<string>(JSON.stringify(initial));
  const setDeduped = useCallback((next: T) => {
    const s = JSON.stringify(next);
    if (s !== serialized.current) {
      serialized.current = s;
      setState(next);
    }
  }, []);
  return [state, setDeduped];
}

export function useAppData(authed: boolean | null) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setInterval_] = useState("15m");

  const [dashboard, setDashboard] = useDedupedState<any>(null);
  const [prediction, setPrediction] = useDedupedState<any>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [orderbook, setOrderbook] = useDedupedState<any>(null);
  const [trades, setTrades] = useDedupedState<any[]>([]);
  const [portfolio, setPortfolio] = useDedupedState<any>(null);
  const [positions, setPositions] = useDedupedState<any[]>([]);
  const [history, setHistory] = useDedupedState<any[]>([]);
  const [backtest, setBacktest] = useState<any>(null);
  const [marketContext, setMarketContext] = useDedupedState<any>(null);
  const [timeframesConsensus, setTimeframesConsensus] = useDedupedState<any>(null);
  const [strategyWeights, setStrategyWeights] = useDedupedState<any>(null);
  const [botStatus, setBotStatus] = useDedupedState<any>(null);
  const [systemStatus, setSystemStatus] = useDedupedState<any>(null);
  const [stressReport, setStressReport] = useDedupedState<any>(null);
  const [exchangeStatus, setExchangeStatus] = useDedupedState<any>(null);
  const [exchangeRiskCheck, setExchangeRiskCheck] = useDedupedState<any>(null);
  const [exchangeBalances, setExchangeBalances] = useDedupedState<any[]>([]);
  const [exchangePositions, setExchangePositions] = useDedupedState<any[]>([]);
  const [exchangeOpenOrders, setExchangeOpenOrders] = useDedupedState<any[]>([]);
  const [executionStatus, setExecutionStatus] = useDedupedState<any>(null);
  const [executionMetrics, setExecutionMetrics] = useDedupedState<any>(null);
  const [modelCenter, setModelCenter] = useDedupedState<any>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [toast, setToast] = useState("");
  const [loading, setLoading] = useState(false);

  const toastTimer = useRef<number | null>(null);

  const showToast = useCallback((message: string) => {
    setToast(message);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(""), 3000);
  }, []);

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

      const [ob, tr, pf, pos, hist, ctx, tf, weights, bot, sys, stress, exStatus, exRisk, execStatus, execMetrics, mlModels, mlChampion, mlExperiments, mlDrift] =
        await Promise.all([
          api.orderbook(symbol, 10).catch(() => null),
          api.trades(symbol, 20).catch(() => ({ trades: [] })),
          api.portfolio().catch(() => null),
          api.positions().catch(() => ({ positions: [] })),
          api.history().catch(() => ({ trades: [] })),
          api.marketContext(symbol).catch(() => null),
          api.timeframes(symbol).catch(() => null),
          api.strategyWeights().catch(() => null),
          api.botStatus().catch(() => null),
          api.systemStatus().catch(() => null),
          api.stressReport().catch(() => null),
          api.exchangeStatus().catch(() => null),
          api.exchangeRiskCheck().catch(() => null),
          api.executionStatus().catch(() => null),
          api.executionMetrics().catch(() => null),
          api.modelsList().catch(() => null),
          api.modelsChampion().catch(() => null),
          api.modelsExperiments().catch(() => null),
          api.modelsDrift().catch(() => null),
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
      setSystemStatus(sys);
      setStressReport(stress);
      setExchangeStatus(exStatus);
      setExchangeRiskCheck(exRisk);
      setExecutionStatus(execStatus);
      setExecutionMetrics(execMetrics);
      setModelCenter({
        models: mlModels?.models || [],
        champion: mlChampion?.champion || null,
        experiments: mlExperiments?.experiments || [],
        drift: mlDrift?.drift || {},
      });

      const connectedExchange = Object.entries(exStatus?.exchanges || {}).find(
        ([, v]: [string, any]) => v?.connected && v?.configured
      )?.[0];

      if (connectedExchange) {
        const [bal, epos, orders] = await Promise.all([
          api.exchangeBalances(connectedExchange).catch(() => null),
          api.exchangePositions(connectedExchange).catch(() => null),
          api.exchangeOpenOrders(connectedExchange).catch(() => null),
        ]);
        setExchangeBalances(bal?.balances || []);
        setExchangePositions(epos?.positions || []);
        setExchangeOpenOrders(orders?.open_orders || []);
      } else {
        setExchangeBalances([]);
        setExchangePositions([]);
        setExchangeOpenOrders([]);
      }

      setLastUpdated(new Date());
    } finally {
      setLoading(false);
    }
  }, [symbol, interval]);

  const runBacktest = useCallback(
    async (bt_interval = "5m") => {
      const data = await api.runBacktest(symbol, bt_interval);
      const results = data.results || data;
      if (results?.equity_curve) {
        results.chart = results.equity_curve.map((v: number, i: number) => ({ trade: i, equity: v }));
      }
      setBacktest(results);
      return results;
    },
    [symbol]
  );

  const openPaperTrade = useCallback(
    async (side: "LONG" | "SHORT") => {
      const res = await api.openPaperTrade(symbol, side, 1000);
      showToast(res?.message || `${side} opened`);
      await load();
    },
    [symbol, showToast, load]
  );

  const closePaperTrade = useCallback(
    async (id: number) => {
      const res = await api.closePaperTrade(id);
      showToast(res?.message || "Trade closed");
      await load();
    },
    [showToast, load]
  );

  const runStressTest = useCallback(async (scenarioId?: string) => {
    const res = await api.runStressTest(scenarioId);
    const rep = await api.stressReport().catch(() => null);
    setStressReport(rep);
    return res;
  }, []);

  const reloadModelCenter = useCallback(async () => {
    const [mlModels, mlChampion, mlExperiments, mlDrift] = await Promise.all([
      api.modelsList().catch(() => null),
      api.modelsChampion().catch(() => null),
      api.modelsExperiments().catch(() => null),
      api.modelsDrift().catch(() => null),
    ]);
    setModelCenter({
      models: mlModels?.models || [],
      champion: mlChampion?.champion || null,
      experiments: mlExperiments?.experiments || [],
      drift: mlDrift?.drift || {},
    });
  }, [setModelCenter]);

  const trainModel = useCallback(
    async (modelName: string, algorithm?: string) => {
      const res = await api.modelsTrain(modelName, algorithm);
      await reloadModelCenter();
      return res;
    },
    [reloadModelCenter]
  );

  const promoteModel = useCallback(
    async (modelId: string) => {
      const res = await api.modelsPromote(modelId);
      await reloadModelCenter();
      return res;
    },
    [reloadModelCenter]
  );

  const rollbackModel = useCallback(
    async (modelId: string) => {
      const res = await api.modelsRollback(modelId);
      await reloadModelCenter();
      return res;
    },
    [reloadModelCenter]
  );

  const archiveModel = useCallback(
    async (modelId: string) => {
      const res = await api.modelsArchive(modelId);
      await reloadModelCenter();
      return res;
    },
    [reloadModelCenter]
  );

  const botAction = useCallback(async (action: string) => {
    const data = await api.botAction(action);
    showToast(data.message || `Bot ${action}`);
    setBotStatus(data.state || null);
  }, [showToast]);

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
    systemStatus,
    stressReport,
    exchangeStatus,
    exchangeRiskCheck,
    exchangeBalances,
    exchangePositions,
    exchangeOpenOrders,
    executionStatus,
    executionMetrics,
    modelCenter,
    lastUpdated,
    toast,
    showToast,
    loading,
    load,
    runBacktest,
    openPaperTrade,
    closePaperTrade,
    botAction,
    runStressTest,
    trainModel,
    promoteModel,
    rollbackModel,
    archiveModel,
  };
}

export type AppData = ReturnType<typeof useAppData>;
