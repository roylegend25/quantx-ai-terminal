// Quant research terminal for the advanced backtester (Phase 20).
//
// Every number on this page comes from a backend API: runs from
// POST /api/backtest/run-advanced, candles from /api/data/candles, dataset
// provenance from /api/data/gaps + /api/data/quality, optimization from
// POST /api/backtest/optimize, walk-forward / Monte Carlo from their run
// endpoints, champion-vs-challenger from /api/ml/compare, feature
// importance from /api/ml/shap, drift from /api/ml/drift and
// recommendations from /api/learning/recommendations. Derived panels
// (histogram, monthly heatmap, rolling metrics, confusion matrix) are pure
// client-side transforms of the run's real trade list.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../services/api";
import type { AppData, Candle } from "../hooks/useAppData";
import ControlBar, { RANGES, STRATEGIES, type BtConfig } from "../components/Backtest/ControlBar";
import ConfigPanel, { type DatasetInfo } from "../components/Backtest/ConfigPanel";
import ChartPanel from "../components/Backtest/ChartPanel";
import KpiPanel from "../components/Backtest/KpiPanel";
import PerformanceRow from "../components/Backtest/PerformanceRow";
import TradeHistoryTable from "../components/Backtest/TradeHistoryTable";
import StrategyAnalytics from "../components/Backtest/StrategyAnalytics";
import OptimizationPanel from "../components/Backtest/OptimizationPanel";
import type { BtPrediction } from "../components/Backtest/BacktestChart";
import { filterTradesByDirection, type BtTrade } from "../lib/backtestStats";

const DEFAULT_CFG: BtConfig = {
  symbol: "BTCUSDT",
  timeframe: "5m",
  rangeKey: "3mo",
  customStart: "",
  customEnd: "",
  capital: 10000,
  positionSize: 1000,
  commissionPct: 0.04,
  slippageBps: 2,
  fundingPct: 0.01,
  leverage: 1,
  direction: "both",
  strategy: "ensemble",
  preset: "",
  atrSlMult: 1.5,
  atrTpMult: 3,
  entryThreshold: 50,
};

function rangeDates(cfg: BtConfig): { start: string | null; end: string | null } {
  if (cfg.rangeKey === "custom") {
    return { start: cfg.customStart || null, end: cfg.customEnd || null };
  }
  const days = RANGES.find((r) => r.key === cfg.rangeKey)?.days ?? 90;
  const start = new Date(Date.now() - days * 86400_000).toISOString().slice(0, 10);
  return { start, end: null };
}

export default function BacktestingPage(props: AppData) {
  const {
    advRun,
    advRuns,
    advWalkForward,
    advMonteCarlo,
    learningRecommendations,
    runAdvancedBacktest,
    runAdvancedWalkForward,
    runAdvancedMonteCarlo,
    loadAdvancedRuns,
    loadAdvancedRun,
    loadLearning,
    showToast,
  } = props;

  const [cfg, setCfg] = useState<BtConfig>(DEFAULT_CFG);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const progressTimer = useRef<number | null>(null);

  const [dataset, setDataset] = useState<DatasetInfo | null>(null);
  const [datasetLoading, setDatasetLoading] = useState(false);

  const [candles, setCandles] = useState<Candle[]>([]);
  const [candlesLoading, setCandlesLoading] = useState(false);
  const [candlesError, setCandlesError] = useState<string | null>(null);
  const [predictions, setPredictions] = useState<BtPrediction[]>([]);

  const [optResult, setOptResult] = useState<any>(null);
  const [mlCompareRows, setMlCompareRows] = useState<any[] | null>(null);
  const [shap, setShap] = useState<any>(null);
  const [drift, setDrift] = useState<any>(null);

  const onChange = useCallback((patch: Partial<BtConfig>) => {
    setCfg((c) => ({ ...c, ...patch }));
  }, []);

  // ---------------------------------------------------------------- run data
  const run = advRun;
  const combo = useMemo(() => {
    const combos: any[] = run?.combinations || [];
    return (
      combos.find((c) => c.ok && c.symbol === cfg.symbol && c.timeframe === cfg.timeframe) ||
      combos.find((c) => c.ok) ||
      null
    );
  }, [run, cfg.symbol, cfg.timeframe]);

  const trades: BtTrade[] = useMemo(() => combo?.trades || [], [combo]);
  const lensTrades = useMemo(() => filterTradesByDirection(trades, cfg.direction), [trades, cfg.direction]);
  const startingBalance = run?.starting_balance ?? cfg.capital;
  const strategyLabel = STRATEGIES.find((s) => s.value === (run?.strategy ?? cfg.strategy))?.label
    ?? (run?.strategy ?? cfg.strategy);
  const failedCombo = useMemo(() => {
    const combos: any[] = run?.combinations || [];
    return combos.length && !combos.some((c) => c.ok) ? combos[0] : null;
  }, [run]);

  // -------------------------------------------------------- dataset details
  useEffect(() => {
    let cancelled = false;
    setDatasetLoading(true);
    Promise.all([
      api.dataGaps(cfg.symbol, cfg.timeframe).catch(() => null),
      api.dataQuality(cfg.symbol, cfg.timeframe).catch(() => null),
    ]).then(([gaps, quality]) => {
      if (cancelled) return;
      const coverage = (quality?.coverage || []).find(
        (c: any) => c.symbol === cfg.symbol && c.timeframe === cfg.timeframe
      );
      setDataset({
        quality_score: gaps?.quality_score ?? coverage?.quality_score ?? null,
        missing_candles: gaps?.missing_candles ?? null,
        interpolated: gaps?.interpolated ?? coverage?.interpolated_rows ?? null,
        rows: coverage?.rows ?? null,
        provider: "Binance Futures (validated store)",
      });
      setDatasetLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [cfg.symbol, cfg.timeframe]);

  // ------------------------------------------------- registry / drift / recs
  useEffect(() => {
    let cancelled = false;
    api.mlCompare()
      .then((res) => {
        if (cancelled) return;
        const rows = res?.rows || [];
        setMlCompareRows(rows);
        const champion = rows.find((r: any) => r.role === "champion");
        if (champion?.model_id) {
          api.mlShap(champion.model_id)
            .then((s) => !cancelled && setShap(s))
            .catch(() => !cancelled && setShap(null));
        }
      })
      .catch(() => !cancelled && setMlCompareRows([]));
    api.mlDrift()
      .then((res) => !cancelled && setDrift(res?.drift || null))
      .catch(() => !cancelled && setDrift(null));
    loadLearning();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------- restore the most recent run
  useEffect(() => {
    if (!run) loadAdvancedRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!run && advRuns?.length) {
      const latest = advRuns[0];
      loadAdvancedRun(latest.run_id).catch(() => {});
      if (latest.symbols?.length) {
        setCfg((c) => ({
          ...c,
          symbol: latest.symbols.includes(c.symbol) ? c.symbol : latest.symbols[0],
          timeframe: latest.timeframes?.[0] ?? c.timeframe,
        }));
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [advRuns]);

  // ------------------------------------------------------- chart candle set
  useEffect(() => {
    if (!combo) {
      setCandles([]);
      setCandlesError(null);
      return;
    }
    let cancelled = false;
    setCandlesLoading(true);
    setCandlesError(null);

    // window: slightly before the first trade through the last trade (or the
    // run's stored range when it produced no trades)
    const firstEntry = combo.trades?.[0]?.entry_time;
    const lastExit = combo.trades?.length ? combo.trades[combo.trades.length - 1].exit_time : null;
    const startDate = firstEntry
      ? new Date(new Date(firstEntry).getTime() - 3 * 86400_000).toISOString().slice(0, 10)
      : undefined;
    const endDate = lastExit
      ? new Date(new Date(lastExit).getTime() + 86400_000).toISOString().slice(0, 10)
      : undefined;

    api.dataCandles(combo.symbol, combo.timeframe, 5000, startDate, endDate)
      .then((res) => {
        if (cancelled) return;
        const rows: Candle[] = (res?.candles || []).map((c: any) => ({
          time: Number(c.time),
          open: Number(c.open),
          high: Number(c.high),
          low: Number(c.low),
          close: Number(c.close),
          volume: Number(c.volume) || 0,
        }));
        setCandles(rows);
        setCandlesLoading(false);
      })
      .catch((e: any) => {
        if (cancelled) return;
        setCandles([]);
        setCandlesError(e?.message || "Failed to load candles");
        setCandlesLoading(false);
      });

    api.predictionHistory(combo.symbol, combo.timeframe, 500)
      .then((res) => {
        if (cancelled) return;
        setPredictions(
          (res?.history || [])
            .filter((p: any) => p.direction === "LONG" || p.direction === "SHORT")
            .map((p: any) => ({
              timestamp: Number(p.timestamp),
              direction: p.direction,
              predicted_price: p.predicted_price,
              target: p.target,
              stop: p.stop,
              confidence: p.confidence,
            }))
        );
      })
      .catch(() => !cancelled && setPredictions([]));

    return () => {
      cancelled = true;
    };
  }, [combo]);

  // ------------------------------------------------------------ run actions
  const estimatedRuntimeSec = useMemo(() => {
    const rows = dataset?.rows;
    if (!rows) return null;
    // measured against the live engine: ~11s per 1000 bars for ensemble
    // (it replays every sub-strategy for contribution), ~3s otherwise
    const perBar = cfg.strategy === "ensemble" || cfg.strategy === "custom" ? 0.011 : 0.003;
    return Math.max(2, Math.round(rows * perBar));
  }, [dataset, cfg.strategy]);

  const stopProgress = useCallback(() => {
    if (progressTimer.current) window.clearInterval(progressTimer.current);
    progressTimer.current = null;
    setProgress(0);
  }, []);

  const startProgress = useCallback(
    (estimateSec: number) => {
      stopProgress();
      const startedAt = Date.now();
      progressTimer.current = window.setInterval(() => {
        const frac = (Date.now() - startedAt) / 1000 / Math.max(2, estimateSec);
        setProgress(Math.min(0.95, frac));
      }, 200);
    },
    [stopProgress]
  );
  useEffect(() => stopProgress, [stopProgress]);

  const onRun = useCallback(async () => {
    const { start, end } = rangeDates(cfg);
    const custom = cfg.strategy === "custom";
    const payload: Record<string, unknown> = {
      strategy: custom ? "ensemble" : cfg.strategy,
      preset: custom && cfg.preset ? cfg.preset : null,
      symbols: [cfg.symbol],
      timeframes: [cfg.timeframe],
      start_date: start,
      end_date: end,
      starting_balance: cfg.capital,
      position_size_usd: cfg.positionSize * Math.max(1, cfg.leverage),
      commission_pct: cfg.commissionPct,
      slippage_bps: cfg.slippageBps,
      funding_rate_pct: cfg.fundingPct,
    };
    if (custom) {
      payload.atr_sl_mult = cfg.atrSlMult;
      payload.atr_tp_mult = cfg.atrTpMult;
      payload.entry_confidence_threshold = cfg.entryThreshold;
    }
    setBusy("run");
    startProgress(estimatedRuntimeSec ?? 15);
    try {
      const res = await runAdvancedBacktest(payload);
      const okCount = res?.run?.summary?.combinations_ok ?? 0;
      if (okCount > 0) {
        const m = res.run.combinations.find((c: any) => c.ok)?.metrics;
        showToast(`Backtest complete — ${m?.total_trades ?? 0} trades, ${m?.total_return_pct ?? 0}% return`);
      } else {
        const err = res?.run?.combinations?.[0]?.error;
        showToast(err || "Backtest produced no successful combinations", "error");
      }
      setOptResult(null);
    } catch (e: any) {
      showToast(e?.message || "Backtest failed", "error");
    } finally {
      stopProgress();
      setBusy(null);
    }
  }, [cfg, estimatedRuntimeSec, runAdvancedBacktest, showToast, startProgress, stopProgress]);

  const onOptimize = useCallback(async () => {
    const { start, end } = rangeDates(cfg);
    setBusy("optimize");
    try {
      const res = await api.backtestOptimize({
        strategy: cfg.strategy === "custom" ? "ensemble" : cfg.strategy,
        symbol: cfg.symbol,
        timeframe: cfg.timeframe,
        start_date: start,
        end_date: end,
        starting_balance: cfg.capital,
        position_size_usd: cfg.positionSize * Math.max(1, cfg.leverage),
      });
      if (res?.ok) {
        setOptResult(res.optimization);
        showToast(`Optimization complete — ${res.optimization?.combinations_tested ?? 0} combinations tested`);
      } else {
        showToast("Optimization failed", "error");
      }
    } catch (e: any) {
      showToast(e?.message || "Optimization failed", "error");
    } finally {
      setBusy(null);
    }
  }, [cfg, showToast]);

  const onWalkForward = useCallback(async () => {
    if (!run?.run_id) return;
    setBusy("walkforward");
    try {
      await runAdvancedWalkForward(run.run_id);
      showToast("Walk-forward validation complete");
    } catch (e: any) {
      showToast(e?.message || "Walk-forward failed", "error");
    } finally {
      setBusy(null);
    }
  }, [run, runAdvancedWalkForward, showToast]);

  const onMonteCarlo = useCallback(async () => {
    if (!run?.run_id) return;
    setBusy("montecarlo");
    try {
      await runAdvancedMonteCarlo(run.run_id, 1000);
      showToast("Monte Carlo simulation complete");
    } catch (e: any) {
      showToast(e?.message || "Monte Carlo failed", "error");
    } finally {
      setBusy(null);
    }
  }, [run, runAdvancedMonteCarlo, showToast]);

  const champion = mlCompareRows?.find((r: any) => r.role === "champion") ?? null;
  const directionNote =
    cfg.direction === "both"
      ? null
      : `Showing ${cfg.direction === "long" ? "LONG" : "SHORT"} trades only (view filter — the engine traded both sides; headline metrics cover the full run).`;

  const running = busy === "run";

  return (
    <div className="bt-page">
      <ControlBar cfg={cfg} onChange={onChange} onRun={onRun} running={running} />

      {failedCombo && (
        <p className="bt-note bt-run-error">
          Last run failed for {failedCombo.symbol} {String(failedCombo.timeframe).toUpperCase()}:{" "}
          {failedCombo.error}
        </p>
      )}

      <div className="bt-main-row">
        <ConfigPanel
          symbol={combo?.symbol ?? cfg.symbol}
          timeframe={combo?.timeframe ?? cfg.timeframe}
          exchange="Binance Futures"
          dataset={dataset}
          datasetLoading={datasetLoading}
          modelVersion={champion ? `${champion.model_name} v${champion.version}` : null}
          strategyLabel={strategyLabel}
          runId={run?.run_id ?? null}
          estimatedRuntimeSec={estimatedRuntimeSec}
          running={running}
          progress={progress}
        />

        <ChartPanel
          symbol={combo?.symbol ?? cfg.symbol}
          timeframe={combo?.timeframe ?? cfg.timeframe}
          candles={candles}
          candlesLoading={candlesLoading || running}
          candlesError={candlesError}
          trades={lensTrades}
          predictions={predictions}
        />

        <KpiPanel metrics={combo?.metrics ?? null} trades={trades} loading={running} />
      </div>

      <PerformanceRow metrics={combo?.metrics ?? null} trades={trades} startingBalance={startingBalance} />

      <TradeHistoryTable
        trades={lensTrades}
        symbol={combo?.symbol ?? cfg.symbol}
        strategy={run?.strategy ?? cfg.strategy}
        runId={run?.run_id ?? null}
        directionNote={directionNote}
      />

      <StrategyAnalytics combo={combo} run={run} trades={trades} />

      <OptimizationPanel
        runId={run?.run_id ?? null}
        busy={busy}
        optResult={optResult}
        walkForward={advWalkForward}
        monteCarlo={advMonteCarlo}
        mlCompareRows={mlCompareRows}
        shap={shap}
        drift={drift}
        recommendations={learningRecommendations?.recommendations || null}
        onOptimize={onOptimize}
        onWalkForward={onWalkForward}
        onMonteCarlo={onMonteCarlo}
      />
    </div>
  );
}
