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
  const [interval, setInterval_] = useState("1h");

  const [dashboard, setDashboard] = useDedupedState<any>(null);
  const [prediction, setPrediction] = useDedupedState<any>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [orderbook, setOrderbook] = useDedupedState<any>(null);
  // Phase 34: the actual wall-clock time of the last successful order book
  // fetch - not a fabricated "live" flag - so OrderBookCard can compute
  // real staleness and show a stale/reconnecting indicator instead of
  // silently displaying an old book as if it were current.
  const [orderbookUpdatedAt, setOrderbookUpdatedAt] = useState<number | null>(null);
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
  // Flat GET /api/ml/champion status: which model (if any) is the live
  // decision engine right now - drives the dashboard's Active Decision
  // Engine card.
  const [championStatus, setChampionStatus] = useDedupedState<any>(null);
  const [labExperiments, setLabExperiments] = useDedupedState<any[]>([]);
  const [labRun, setLabRun] = useState<any>(null);
  const [labCompare, setLabCompare] = useDedupedState<any>(null);
  const [labMonteCarlo, setLabMonteCarlo] = useState<any>(null);
  const [labWalkForward, setLabWalkForward] = useState<any>(null);
  // ---- Phase 20: data engine / advanced backtest / learning loop ----
  const [dataSources, setDataSources] = useDedupedState<any>(null);
  const [dataJobs, setDataJobs] = useDedupedState<any[]>([]);
  const [dataQuality, setDataQuality] = useDedupedState<any>(null);
  const [dataGaps, setDataGaps] = useState<any>(null);
  const [advRun, setAdvRun] = useState<any>(null);
  const [advRuns, setAdvRuns] = useDedupedState<any[]>([]);
  const [advWalkForward, setAdvWalkForward] = useState<any>(null);
  const [advMonteCarlo, setAdvMonteCarlo] = useState<any>(null);
  const [learningPerformance, setLearningPerformance] = useDedupedState<any>(null);
  const [learningWeights, setLearningWeights] = useDedupedState<any>(null);
  const [learningRecommendations, setLearningRecommendations] = useDedupedState<any>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [toast, setToast] = useState("");
  const [toastTone, setToastTone] = useState<"success" | "error">("success");
  const [loading, setLoading] = useState(false);

  const toastTimer = useRef<number | null>(null);
  // Guards against out-of-order network responses: if the user switches
  // symbol/interval (or the 10s poll fires) again before an in-flight load()
  // resolves, the older response must not clobber state with stale data.
  const loadRequestId = useRef(0);
  const predictionAbort = useRef<AbortController | null>(null);

  const showToast = useCallback((message: string, tone: "success" | "error" = "success") => {
    setToast(message);
    setToastTone(tone);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(""), 3000);
  }, []);

  const load = useCallback(async () => {
    const requestId = ++loadRequestId.current;
    const isStale = () => loadRequestId.current !== requestId;
    predictionAbort.current?.abort();
    const controller = new AbortController();
    predictionAbort.current = controller;

    setLoading(true);
    try {
      // Staged, isolated updates: each of these three commits its state the
      // moment its own request resolves (guarded by isStale), instead of a
      // single Promise.all gating all three on the SLOWEST. The prediction
      // endpoint fans out into a 6-timeframe consensus fetch and market
      // intelligence and routinely takes seconds; candles return in ~300ms.
      // Awaiting them together meant every symbol/timeframe switch showed
      // an empty chart until the prediction finished - the "forecast and
      // even candles disappear after switching" symptom. Failures stay
      // isolated per-call: a failed leg keeps its last known value.
      const dashP = api.dashboard().then(
        (dash) => { if (!isStale() && dash) setDashboard(dash); },
        () => {}
      );
      const predP = api.prediction(symbol, interval, controller.signal).then(
        (predRes) => {
          if (isStale() || !predRes) return;
          const next = predRes.prediction;
          if (predRes.symbol !== symbol || predRes.timeframe !== interval || next?.symbol !== symbol || next?.timeframe !== interval) return;
          setPrediction(next);
        },
        () => {}
      );
      const candleP = api.candles(symbol, interval, 220).then(
        (candleRows) => {
          if (isStale() || !candleRows) return;
          setCandles(
            candleRows.map((x: Candle) => ({
              ...x,
              label: new Date(x.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
            }))
          );
        },
        () => {}
      );
      await Promise.all([dashP, predP, candleP]);
      if (isStale()) return;

      const [ob, tr, pf, pos, hist, ctx, tf, weights, bot, sys, stress, exStatus, exRisk, execStatus, execMetrics, mlModels, mlChampion, mlExperiments, mlDrift, mlChampionStatus] =
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
          api.mlChampion().catch(() => null),
        ]);

      if (isStale()) return;

      // A failed/empty fetch keeps the last known-good book on screen
      // (mirrors useBinanceAccount's rate-limit handling) - orderbookUpdatedAt
      // only advances on an actual successful read, so staleness is always
      // measured from real data, never reset by a failure.
      if (ob) { setOrderbook(ob); setOrderbookUpdatedAt(Date.now()); }
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
      setChampionStatus(mlChampionStatus);

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
      if (!isStale()) setLoading(false);
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
    async (side: "LONG" | "SHORT", leverage = 1) => {
      try {
        const res = await api.openPaperTrade(symbol, side, 1000, leverage);
        showToast(res?.message || `${side} opened`, "success");
      } catch (e: any) {
        showToast(e?.message || `Failed to open ${side} trade`, "error");
      } finally {
        await load();
      }
    },
    [symbol, showToast, load]
  );

  const closePaperTrade = useCallback(
    async (id: number, quantity?: number) => {
      try {
        const res = await api.closePaperTrade(id, quantity);
        showToast(res?.message || "Trade closed", "success");
      } catch (e: any) {
        showToast(e?.message || "Failed to close trade", "error");
      } finally {
        await load();
      }
    },
    [showToast, load]
  );

  const updatePositionRisk = useCallback(
    async (id: number, patch: { stop_loss: number | null; take_profit: number | null; trailing_stop: number | null }) => {
      // errors propagate to the Edit Risk modal, which shows the server's
      // structured validation message inline
      const res = await api.updatePositionRisk(id, patch);
      showToast("Risk levels updated", "success");
      await load();
      return res;
    },
    [showToast, load]
  );

  const resetPaperTrading = useCallback(async () => {
    try {
      const res = await api.resetPaperTrading();
      showToast(res?.message || "Paper trading reset successfully", "success");
      return res;
    } catch (e: any) {
      showToast(e?.message || "Failed to reset paper trading", "error");
      throw e;
    } finally {
      await load();
    }
  }, [showToast, load]);

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

  const loadLabExperiments = useCallback(
    async (strategy?: string, symbol?: string) => {
      const res = await api.labExperiments(strategy, symbol).catch(() => null);
      setLabExperiments(res?.experiments || []);
    },
    [setLabExperiments]
  );

  const runLabBacktest = useCallback(
    async (payload: Record<string, unknown>) => {
      const res = await api.labRun(payload);
      setLabRun(res);
      await loadLabExperiments();
      return res;
    },
    [loadLabExperiments]
  );

  const runLabCompare = useCallback(
    async (compareSymbol: string, timeframe = "5m") => {
      const res = await api.labCompare(compareSymbol, timeframe);
      setLabCompare(res);
      return res;
    },
    [setLabCompare]
  );

  const runLabMonteCarlo = useCallback(async (experimentId: string, simulations = 1000) => {
    const res = await api.labMonteCarlo(experimentId, simulations);
    setLabMonteCarlo(res);
    return res;
  }, []);

  const runLabWalkForward = useCallback(async (experimentId: string, trainBars = 500, validateBars = 150) => {
    const res = await api.labWalkForward(experimentId, trainBars, validateBars);
    setLabWalkForward(res);
    return res;
  }, []);

  // ---- Phase 20: data engine ----
  const loadDataEngine = useCallback(async () => {
    const [sources, jobs, quality] = await Promise.all([
      api.dataSources().catch(() => null),
      api.dataJobs(20).catch(() => null),
      api.dataQuality().catch(() => null),
    ]);
    setDataSources(sources);
    setDataJobs(jobs?.jobs || []);
    setDataQuality(quality);
  }, [setDataSources, setDataJobs, setDataQuality]);

  const startDataDownload = useCallback(
    async (payload: Record<string, unknown>) => {
      const res = await api.dataDownload(payload);
      await loadDataEngine();
      return res;
    },
    [loadDataEngine]
  );

  const loadDataGaps = useCallback(async (gapSymbol: string, timeframe: string) => {
    const res = await api.dataGaps(gapSymbol, timeframe).catch((e: any) => ({ error: e?.message }));
    setDataGaps(res);
    return res;
  }, []);

  // ---- Phase 20: advanced backtest ----
  const runAdvancedBacktest = useCallback(async (payload: Record<string, unknown>) => {
    const res = await api.backtestRunAdvanced(payload);
    setAdvRun(res?.run || null);
    setAdvWalkForward(null);
    setAdvMonteCarlo(null);
    const runs = await api.backtestRuns(20).catch(() => null);
    setAdvRuns(runs?.runs || []);
    return res;
  }, [setAdvRuns]);

  const loadAdvancedRuns = useCallback(async () => {
    const runs = await api.backtestRuns(20).catch(() => null);
    setAdvRuns(runs?.runs || []);
  }, [setAdvRuns]);

  const loadAdvancedRun = useCallback(async (runId: string) => {
    const res = await api.backtestResult(runId);
    setAdvRun(res?.run || null);
    setAdvWalkForward(null);
    setAdvMonteCarlo(null);
    return res;
  }, []);

  const runAdvancedWalkForward = useCallback(async (runId: string, trainBars = 500, validateBars = 150) => {
    const res = await api.backtestWalkforwardAdvanced(runId, trainBars, validateBars);
    setAdvWalkForward(res);
    return res;
  }, []);

  const runAdvancedMonteCarlo = useCallback(async (runId: string, simulations = 1000) => {
    const res = await api.backtestMontecarloAdvanced(runId, simulations);
    setAdvMonteCarlo(res);
    return res;
  }, []);

  // ---- Phase 20: learning loop ----
  const loadLearning = useCallback(async () => {
    const [perf, weights, recs] = await Promise.all([
      api.learningPerformance().catch(() => null),
      api.learningStrategyWeights().catch(() => null),
      api.learningRecommendations().catch(() => null),
    ]);
    setLearningPerformance(perf?.performance || null);
    setLearningWeights(weights);
    setLearningRecommendations(recs);
  }, [setLearningPerformance, setLearningWeights, setLearningRecommendations]);

  const runLearningEvaluate = useCallback(
    async (evalSymbol?: string) => {
      const res = await api.learningEvaluate(evalSymbol);
      await loadLearning();
      return res;
    },
    [loadLearning]
  );

  const botAction = useCallback(async (action: string) => {
    try {
      const data = await api.botAction(action);
      showToast(data.message || `Bot ${action}`, data.ok === false ? "error" : "success");
      setBotStatus(data.state || null);
    } catch (e: any) {
      showToast(e?.message || `Failed to ${action} bot`, "error");
    }
  }, [showToast, setBotStatus]);

  // Drop the previous symbol/timeframe's prediction the instant either
  // changes, before load() has a chance to refetch - otherwise, for the
  // one round-trip it takes to fetch the new combo, every consumer
  // (chart tiles, dashboard, positions' "Bot Trading Status" card) would
  // keep rendering the old symbol's direction/confidence/target under the
  // newly-selected symbol/timeframe's label.
  useEffect(() => {
    setPrediction(null);
    predictionAbort.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, interval]);

  useEffect(() => {
    if (!authed) return;
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => { window.clearInterval(id); predictionAbort.current?.abort(); };
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
    orderbookUpdatedAt,
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
    championStatus,
    labExperiments,
    labRun,
    labCompare,
    labMonteCarlo,
    labWalkForward,
    lastUpdated,
    toast,
    toastTone,
    showToast,
    loading,
    load,
    runBacktest,
    openPaperTrade,
    closePaperTrade,
    updatePositionRisk,
    resetPaperTrading,
    botAction,
    runStressTest,
    trainModel,
    promoteModel,
    rollbackModel,
    archiveModel,
    loadLabExperiments,
    runLabBacktest,
    runLabCompare,
    runLabMonteCarlo,
    runLabWalkForward,
    // Phase 20
    dataSources,
    dataJobs,
    dataQuality,
    dataGaps,
    advRun,
    advRuns,
    advWalkForward,
    advMonteCarlo,
    learningPerformance,
    learningWeights,
    learningRecommendations,
    loadDataEngine,
    startDataDownload,
    loadDataGaps,
    runAdvancedBacktest,
    loadAdvancedRuns,
    loadAdvancedRun,
    runAdvancedWalkForward,
    runAdvancedMonteCarlo,
    loadLearning,
    runLearningEvaluate,
  };
}

export type AppData = ReturnType<typeof useAppData>;
