import { authFetch } from "./auth";
import { API_BASE_URL } from "./baseUrl";

const API = API_BASE_URL;

async function getJson<T = any>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await authFetch(`${API}${url}`, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || `Request failed (${res.status})`);
  }
  return data;
}

async function postJson<T = any>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await authFetch(`${API}${url}`, { method: "POST", ...options });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || `Request failed (${res.status})`);
  }
  return data;
}

async function putJson<T = any>(url: string, body: Record<string, unknown>): Promise<T> {
  const res = await authFetch(`${API}${url}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || `Request failed (${res.status})`);
  }
  return data;
}

async function patchJson<T = any>(url: string, body: Record<string, unknown>): Promise<T> {
  const res = await authFetch(`${API}${url}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.detail || data?.message || `Request failed (${res.status})`) as Error & { field?: string };
    if (data?.field) err.field = data.field;
    throw err;
  }
  return data;
}

/** Like putJson, but surfaces structured 409 CONFIRMATION_REQUIRED detail
 *  (code/message/lowered_fields) instead of stringifying it - used by the
 *  Bot Settings risk-lowering confirmation flow. */
async function putJsonDetailed<T = any>(url: string, body: Record<string, unknown>): Promise<T> {
  const res = await authFetch(`${API}${url}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail?.message || data?.message || `Request failed (${res.status})`;
    const err = new Error(message) as Error & { status?: number; detail?: any };
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return data;
}

/** Like postJson with a body, surfacing structured 409 detail the same way. */
async function postJsonDetailed<T = any>(url: string, body: Record<string, unknown>): Promise<T> {
  const res = await authFetch(`${API}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail?.message || data?.message || `Request failed (${res.status})`;
    const err = new Error(message) as Error & { status?: number; detail?: any };
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return data;
}

export const api = {
  dashboard: () => getJson("/api/dashboard"),
  liveDecision: (symbol: string, timeframe: string) =>
    getJson(`/api/dashboard/live-decision?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`),
  sourceHealth: (symbol: string, timeframe: string, decisionId: string) => getJson(`/api/analysis/source-health?symbol=${symbol}&timeframe=${timeframe}&decision_id=${decisionId}`),
  predictionResolutionSummary: (params = "") => getJson(`/api/analysis/prediction-resolution-summary${params ? `?${params}` : ""}`),
  predictionCycleStatus: () => getJson("/api/analysis/prediction-cycle"),
  startPredictionCycle: (label?: string, idempotencyKey?: string) =>
    postJson("/api/analysis/prediction-cycle", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: label ?? null, idempotency_key: idempotencyKey ?? null }),
    }),
  prediction: (symbol: string, interval: string, signal?: AbortSignal) =>
    getJson(`/api/prediction/${symbol}?timeframe=${interval}`, { signal }),
  predictionHistory: (symbol: string, timeframe?: string, limit = 500) => {
    const params = new URLSearchParams({ symbol, limit: String(limit) });
    if (timeframe) params.set("timeframe", timeframe);
    return getJson(`/api/prediction/history?${params.toString()}`);
  },
  candles: (symbol: string, interval: string, limit = 220) =>
    getJson(`/api/market/${symbol}/candles?interval=${interval}&limit=${limit}`),
  orderbook: (symbol: string, limit = 10) => getJson(`/api/orderbook/${symbol}?limit=${limit}`),
  trades: (symbol: string, limit = 20) => getJson(`/api/trades/${symbol}?limit=${limit}`),
  marketContext: (symbol: string) => getJson(`/api/market/context?symbol=${symbol}`),
  liquidationHeatmap: (symbol: string) => getJson(`/api/market/${symbol}/liquidation-heatmap`),
  hyperliquidLargeTrades: (coins = "BTC,ETH", minNotional = 50000) =>
    getJson(`/api/market/hyperliquid/large-trades?coins=${encodeURIComponent(coins)}&min_notional=${minNotional}`),
  timeframes: (symbol: string) => getJson(`/api/timeframes/${symbol}`),
  tradingHorizon: (symbol: string, chartTimeframe?: string) =>
    getJson(`/api/timeframes/${encodeURIComponent(symbol)}/horizon${chartTimeframe ? `?chart_timeframe=${encodeURIComponent(chartTimeframe)}` : ""}`),
  tradingProfile: () => getJson("/api/bot/trading-profile"),
  updateTradingProfile: (body: { trading_profile: string; strict_timeframe_unanimity: boolean; auto_profile_enabled: boolean; expected_revision: number }) =>
    patchJson("/api/bot/trading-profile", body),
  strategyWeights: () => getJson("/api/strategy/weights"),
  botStatus: () => getJson("/api/bot/status"),
  botAction: (action: string) => postJson(`/api/bot/${action}`),
  decisionEngine: () => getJson("/api/bot/decision-engine"),
  switchDecisionEngine: (engine: "active_drive_v1" | "active_drive_v2") => patchJson("/api/bot/decision-engine", { engine, acknowledged: true }),
  engineComparison: () => getJson("/api/bot/decision-engine/comparison"),
  setEngineComparison: (enabled: boolean) => patchJson("/api/bot/decision-engine/comparison", { enabled }),
  portfolio: () => getJson("/api/paper/portfolio"),
  positions: () => getJson("/api/paper/positions"),
  history: () => getJson("/api/paper/history"),
  openPaperTrade: (symbol: string, side: "LONG" | "SHORT", usdtSize = 1000, leverage = 1) =>
    postJson(`/api/paper/open?symbol=${symbol}&side=${side}&usdt_size=${usdtSize}&leverage=${leverage}`),
  closePaperTrade: (id: number, quantity?: number) =>
    postJson(`/api/paper/positions/${id}/close`, {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(quantity != null ? { quantity } : {}),
    }),
  updatePositionRisk: (id: number, patch: { stop_loss?: number | null; take_profit?: number | null; trailing_stop?: number | null }) =>
    patchJson(`/api/paper/positions/${id}/risk`, patch),
  resetPaperTrading: () => postJson(`/api/paper/reset`),
  runBacktest: (symbol: string, interval = "5m") =>
    getJson(`/api/backtest/run?symbol=${symbol}&interval=${interval}`),
  downloadHistory: (symbol: string, interval = "5m", limit = 1000) =>
    getJson(`/api/backtest/download?symbol=${symbol}&interval=${interval}&limit=${limit}`),
  researchExperiments: () => getJson("/api/research/experiments"),
  researchBenchmark: (symbol: string, interval = "5m") =>
    getJson(`/api/research/benchmark?symbol=${symbol}&interval=${interval}`),
  researchMonteCarlo: (symbol: string, interval = "5m", simulations = 1000) =>
    getJson(`/api/research/montecarlo?symbol=${symbol}&interval=${interval}&simulations=${simulations}`),
  mlDatasetInfo: () => getJson("/api/ml/dataset/info"),
  mlModels: () => getJson("/api/ml/models"),
  mlPerformance: () => getJson("/api/ml/performance"),
  systemStatus: () => getJson("/api/health/status"),
  stressScenarios: () => getJson("/api/stress/scenarios"),
  stressReport: () => getJson("/api/stress/report"),
  runStressTest: async (scenarioId?: string) => {
    const qs = scenarioId ? `?scenario_id=${encodeURIComponent(scenarioId)}` : "";
    const res = await authFetch(`${API}/api/stress/run${qs}`, { method: "POST" });
    return res.json();
  },
  exchangeStatus: () => getJson("/api/exchange/status"),
  exchangeRiskCheck: () => getJson("/api/exchange/risk-check"),
  exchangeBalances: (exchange: string) => getJson(`/api/exchange/balances?exchange=${exchange}`),
  exchangePositions: (exchange: string) => getJson(`/api/exchange/positions?exchange=${exchange}`),
  exchangeOpenOrders: (exchange: string) => getJson(`/api/exchange/open-orders?exchange=${exchange}`),
  executionStatus: () => getJson("/api/execution/status"),
  executionMetrics: () => getJson("/api/execution/metrics"),
  modelsList: () => getJson("/api/models"),
  modelsChampion: (modelName?: string) =>
    getJson(`/api/models/champion${modelName ? `?model_name=${encodeURIComponent(modelName)}` : ""}`),
  modelsHistory: (modelName: string) => getJson(`/api/models/history?model_name=${encodeURIComponent(modelName)}`),
  modelsExperiments: () => getJson("/api/models/experiments"),
  modelsDrift: () => getJson("/api/models/drift"),
  modelsTrain: async (modelName: string, algorithm?: string) => {
    const res = await authFetch(`${API}/api/models/train`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_name: modelName, algorithm: algorithm || modelName }),
    });
    return res.json();
  },
  modelsPromote: async (modelId: string) => {
    const res = await authFetch(`${API}/api/models/promote/${encodeURIComponent(modelId)}`, { method: "POST" });
    return res.json();
  },
  modelsRollback: async (modelId: string) => {
    const res = await authFetch(`${API}/api/models/rollback/${encodeURIComponent(modelId)}`, { method: "POST" });
    return res.json();
  },
  modelsArchive: async (modelId: string) => {
    const res = await authFetch(`${API}/api/models/archive/${encodeURIComponent(modelId)}`, { method: "POST" });
    return res.json();
  },
  // ---- AI Model Lab (/api/ml) ----
  mlAlgorithms: () => getJson("/api/ml/algorithms"),
  mlOverview: () => getJson("/api/ml/overview"),
  mlTrain: (body: Record<string, unknown>) =>
    postJson("/api/ml/train", { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  mlJobs: (status?: string, limit = 30) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (status) params.set("status", status);
    return getJson(`/api/ml/jobs?${params.toString()}`);
  },
  mlJob: (jobId: string) => getJson(`/api/ml/jobs/${encodeURIComponent(jobId)}`),
  mlCancelJob: (jobId: string) => postJson(`/api/ml/jobs/${encodeURIComponent(jobId)}/cancel`),
  mlRegistry: (status?: string) =>
    getJson(`/api/ml/registry${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  mlRegistryDetail: (modelId: string) => getJson(`/api/ml/registry/${encodeURIComponent(modelId)}`),
  mlRegistryDelete: async (modelId: string) => {
    const res = await authFetch(`${API}/api/ml/registry/${encodeURIComponent(modelId)}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.detail || `Delete failed (${res.status})`);
    return data;
  },
  mlDownloadUrl: (modelId: string) => `${API}/api/ml/registry/${encodeURIComponent(modelId)}/download`,
  mlPromote: (modelId: string, force = false) =>
    postJson(`/api/ml/promote/${encodeURIComponent(modelId)}${force ? "?force=true" : ""}`),
  mlRollback: (modelName: string) => postJson(`/api/ml/rollback/${encodeURIComponent(modelName)}`),
  mlCompare: () => getJson("/api/ml/compare"),
  mlDrift: () => getJson("/api/ml/drift"),
  mlDriftHistory: (days = 30) => getJson(`/api/ml/drift/history?days=${days}`),
  mlDriftScan: (symbol = "BTCUSDT") =>
    postJson("/api/ml/drift/scan", { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol }) }),
  mlLive: (symbol?: string) => getJson(`/api/ml/live${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`),
  mlInference: (limit = 50, symbol?: string) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (symbol) params.set("symbol", symbol);
    return getJson(`/api/ml/inference?${params.toString()}`);
  },
  mlPerformanceHistory: (days = 30) => getJson(`/api/ml/history?days=${days}`),
  mlShap: (modelId: string) => getJson(`/api/ml/shap/${encodeURIComponent(modelId)}`),
  mlExplain: (predictionId?: number, symbol?: string) =>
    predictionId != null
      ? getJson(`/api/ml/explain/${predictionId}`)
      : getJson(`/api/ml/explain${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`),
  mlHpoStart: (body: Record<string, unknown>) =>
    postJson("/api/ml/hpo", { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  mlHpoResults: () => getJson("/api/ml/hpo/results"),
  mlSettings: () => getJson("/api/ml/settings"),
  mlSettingsUpdate: (patch: Record<string, unknown>) => putJson("/api/ml/settings", patch),
  mlRetrainNow: () => postJson("/api/ml/retrain"),
  mlSchedule: () => getJson("/api/ml/schedule"),
  mlScheduleUpdate: (patch: Record<string, unknown>) => putJson("/api/ml/schedule", patch),
  mlTrainNow: (algorithms?: string[]) =>
    postJson("/api/ml/train-now", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ algorithms: algorithms ?? null }),
    }),
  mlPreflight: () => getJson("/api/ml/preflight"),
  mlChampion: () => getJson("/api/ml/champion"),
  mlCompareModel: (modelId: string) => getJson(`/api/ml/compare/${encodeURIComponent(modelId)}`),
  mlRollbackCurrent: (modelName?: string) =>
    postJson("/api/ml/rollback", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_name: modelName ?? null }),
    }),
  mlNotifications: (limit = 50, unreadOnly = false) =>
    getJson(`/api/ml/notifications?limit=${limit}${unreadOnly ? "&unread_only=true" : ""}`),
  mlNotificationRead: (id: number) => postJson(`/api/ml/notifications/${id}/read`),
  mlNotificationsReadAll: () => postJson("/api/ml/notifications/read-all"),
  labExperiments: (strategy?: string, symbol?: string) => {
    const params = new URLSearchParams();
    if (strategy) params.set("strategy", strategy);
    if (symbol) params.set("symbol", symbol);
    const qs = params.toString();
    return getJson(`/api/research/lab/experiments${qs ? `?${qs}` : ""}`);
  },
  labRun: async (payload: Record<string, unknown>) => {
    const res = await authFetch(`${API}/api/research/lab/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return res.json();
  },
  labExperiment: (experimentId: string) => getJson(`/api/research/lab/experiment/${encodeURIComponent(experimentId)}`),
  labCompare: (symbol: string, timeframe = "5m") =>
    getJson(`/api/research/lab/compare?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`),
  labMonteCarlo: (experimentId: string, simulations = 1000) =>
    getJson(`/api/research/lab/montecarlo/${encodeURIComponent(experimentId)}?simulations=${simulations}`),
  labWalkForward: (experimentId: string, trainBars = 500, validateBars = 150) =>
    getJson(
      `/api/research/lab/walkforward/${encodeURIComponent(experimentId)}?train_bars=${trainBars}&validate_bars=${validateBars}`
    ),
  logsRecent: (opts: { sinceId?: number; level?: string; category?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.sinceId != null) params.set("since_id", String(opts.sinceId));
    if (opts.level) params.set("level", opts.level);
    if (opts.category) params.set("category", opts.category);
    params.set("limit", String(opts.limit ?? 200));
    return getJson(`/api/logs/recent?${params.toString()}`);
  },
  // ---- Phase 20: data engine (/api/data) ----
  dataSources: () => getJson("/api/data/sources"),
  dataDownload: (body: Record<string, unknown>) =>
    postJson("/api/data/download", { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  dataJobs: (limit = 20) => getJson(`/api/data/jobs?limit=${limit}`),
  dataQuality: (symbol?: string, timeframe?: string) => {
    const params = new URLSearchParams();
    if (symbol) params.set("symbol", symbol);
    if (timeframe) params.set("timeframe", timeframe);
    const qs = params.toString();
    return getJson(`/api/data/quality${qs ? `?${qs}` : ""}`);
  },
  dataGaps: (symbol: string, timeframe: string) =>
    getJson(`/api/data/gaps?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`),
  dataCandles: (symbol: string, timeframe: string, limit = 500, startDate?: string, endDate?: string) => {
    const params = new URLSearchParams({ symbol, timeframe, limit: String(limit) });
    if (startDate) params.set("start_date", startDate);
    if (endDate) params.set("end_date", endDate);
    return getJson(`/api/data/candles?${params.toString()}`);
  },
  dataFeatures: (symbol: string, timeframe: string, refresh = true) =>
    getJson(`/api/data/features?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&refresh=${refresh}`),
  // ---- Phase 20: advanced backtesting (/api/backtest) ----
  backtestPresets: () => getJson("/api/backtest/presets"),
  backtestRunAdvanced: (body: Record<string, unknown>) =>
    postJson("/api/backtest/run-advanced", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  backtestRuns: (limit = 20) => getJson(`/api/backtest/results?limit=${limit}`),
  backtestResult: (runId: string) => getJson(`/api/backtest/results/${encodeURIComponent(runId)}`),
  backtestCompareAdvanced: (symbol: string, timeframe: string) =>
    getJson(`/api/backtest/compare?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`),
  backtestWalkforwardAdvanced: (runId: string, trainBars = 500, validateBars = 150) =>
    getJson(
      `/api/backtest/walkforward/${encodeURIComponent(runId)}?train_bars=${trainBars}&validate_bars=${validateBars}`
    ),
  backtestMontecarloAdvanced: (runId: string, simulations = 1000) =>
    getJson(`/api/backtest/montecarlo/${encodeURIComponent(runId)}?simulations=${simulations}`),
  backtestOptimize: (body: Record<string, unknown>) =>
    postJson("/api/backtest/optimize", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  // ---- Phase 20: learning loop (/api/learning) ----
  learningEvaluate: (symbol?: string) =>
    postJson("/api/learning/evaluate", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(symbol ? { symbol } : {}),
    }),
  learningPerformance: () => getJson("/api/learning/performance"),
  learningStrategyWeights: () => getJson("/api/learning/strategy-weights"),
  learningTrainChallenger: (algorithm = "lightgbm") =>
    postJson("/api/learning/train-challenger", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ algorithm }),
    }),
  learningRecommendations: () => getJson("/api/learning/recommendations"),
  // ---- Phase 23: separated paper/Binance portfolios + trading control ----
  paperSummary: () => getJson("/api/portfolio/paper/summary"),
  paperPortfolioPositions: () => getJson("/api/portfolio/paper/positions"),
  paperPortfolioOrders: () => getJson("/api/portfolio/paper/orders"),
  paperPortfolioTrades: () => getJson("/api/portfolio/paper/trades"),
  binanceSummary: () => getJson("/api/portfolio/binance/summary"),
  binanceBalances: () => getJson("/api/portfolio/binance/balances"),
  binancePositions: () => getJson("/api/portfolio/binance/positions"),
  binanceOrders: () => getJson("/api/portfolio/binance/orders"),
  binanceTrades: () => getJson("/api/portfolio/binance/trades"),
  binanceIncome: (limit = 50) => getJson(`/api/portfolio/binance/income?limit=${limit}`),
  // ---- Phase 29: one shared snapshot instead of N separate polling loops ----
  binanceSnapshot: () => getJson("/api/binance/snapshot"),
  binanceSnapshotRefresh: () => postJson("/api/binance/snapshot/refresh"),
  binanceRateLimitStatus: () => getJson("/api/binance/rate-limit-status"),
  portfolioAudit: (limit = 50) => getJson(`/api/portfolio/audit?limit=${limit}`),
  botTradesPaper: (limit = 100) => getJson(`/api/bot/trades/paper?limit=${limit}`),
  botTradesBinance: (limit = 100) => getJson(`/api/bot/trades/binance?limit=${limit}`),
  tradingMode: () => getJson("/api/trading/mode"),
  setTradingMode: (mode: "PAPER" | "BINANCE_LIVE") =>
    postJson("/api/trading/mode", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    }),
  unlockBinanceLive: (body: {
    password: string;
    confirmation: string;
    second_confirmation: string;
    account_confirmation: string;
    symbol_confirmation: string;
    acknowledgements: Record<string, boolean>;
  }) =>
    postJson("/api/trading/binance/unlock-live", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  lockBinanceLive: () => postJson("/api/trading/binance/lock-live"),
  binanceStoredCredentialStatus: () => getJson("/api/admin/binance-credentials"),
  saveBinanceCredential: (body: {
    password: string;
    api_key: string;
    api_secret: string;
    label?: string;
    environment: "live" | "testnet";
  }) =>
    postJson("/api/admin/binance-credentials", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  testBinanceCredential: (body: { api_key?: string; api_secret?: string } = {}) =>
    postJson("/api/admin/binance-credentials/test", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteBinanceCredential: (password: string) =>
    authFetch(`${API}/api/admin/binance-credentials`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }).then(async (res) => {
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail || data?.message || `Request failed (${res.status})`);
      return data;
    }),
  liveLeaseStatus: () => getJson("/api/trading/binance/live-lease-status"),
  binanceClosePosition: (body: Record<string, unknown>) =>
    postJson("/api/trading/binance/close-position", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, confirm: true }),
    }),
  binanceUpdatePositionRisk: (id: number, patch: { stop_loss?: number | null; take_profit?: number | null }) =>
    patchJson(`/api/trading/binance/positions/${id}/risk`, patch),
  // Accepts the full normalized order-row identifier set (Debug 1.pdf
  // section 2/6) - orderId alone still works for the pre-existing Cancel
  // SL/TP callers, which only ever knew one generic id and rely on the
  // backend's tracked-algo-id inference (see execution_router.cancel_order).
  binanceCancelOrder: (params: {
    symbol: string;
    orderId?: number | string | null;
    clientOrderId?: string | null;
    algoId?: number | string | null;
    clientAlgoId?: string | null;
    provider?: "classic" | "algo" | null;
  }) =>
    postJson("/api/trading/binance/cancel-order", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: params.symbol,
        // MUST be sent as a JSON string, never Number(...) - live-verified
        // root cause: this account's real Binance order ids run up to 19
        // digits (e.g. 8389766233056201670), past Number.MAX_SAFE_INTEGER
        // (2^53-1, 16 digits). The browser's own JSON.parse (inside
        // postJson/getJson's res.json()) already silently rounds any raw
        // JSON *number* past that threshold to the nearest representable
        // double the moment the orders list response is parsed - BEFORE
        // this function ever runs - corrupting order_id from ...201670 to
        // ...201728. Sending that corrupted id back made Binance correctly
        // reject the cancel with "Unknown order sent." every time, which
        // is exactly why row Cancel failed while Cancel All (which never
        // round-trips an order_id through JSON) kept working. The backend
        // now serializes these ids as JSON strings too (see
        // BinanceOrder.to_dict()), so `params.orderId` is already the
        // untouched original string by the time it gets here - String()
        // is just a defensive no-op for any caller that still passes a
        // small numeric id directly (e.g. a literal in a test).
        order_id: params.orderId != null ? String(params.orderId) : null,
        client_order_id: params.clientOrderId ?? null,
        algo_id: params.algoId != null ? String(params.algoId) : null,
        client_algo_id: params.clientAlgoId ?? null,
        provider: params.provider ?? null,
      }),
    }),
  binanceCancelAllOrders: (symbol?: string) =>
    postJson("/api/trading/binance/cancel-all-orders", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: symbol ?? null, confirm: true }),
    }),
  // ---- Phase 24: admin server-config (allowlisted .env editor) ----
  adminServerConfig: () => getJson("/api/admin/server-config"),
  adminSetBinanceLive: (enabled: boolean, typedConfirmation = "", acknowledgements: Record<string, boolean> = {}) =>
    postJson("/api/admin/server-config/binance-live", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, typed_confirmation: typedConfirmation, acknowledgements }),
    }),
  adminUpdateRiskLimits: (limits: Record<string, unknown>) =>
    patchJson("/api/admin/server-config/risk-limits", limits),
  adminReloadConfig: () => postJson("/api/admin/server-config/reload"),
  adminPreviewConfidenceThreshold: (proposed: number) =>
    getJson(`/api/admin/server-config/confidence-threshold/preview?proposed=${proposed}`),
  adminUpdateConfidenceThreshold: (minConfidence: number) =>
    patchJson("/api/admin/server-config/confidence-threshold", { min_confidence: minConfidence }),
  killSwitch: (active: boolean, reason?: string, cancelOrders = true) =>
    postJson("/api/trading/kill-switch", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active, reason: reason ?? null, cancel_orders: cancelOrders }),
    }),
  tradingSync: () => postJson("/api/trading/sync"),
  liveReadiness: () => getJson("/api/trading/live-readiness"),
  binanceRiskStatus: () => getJson("/api/trading/binance/risk-status"),
  binanceDecisionStatus: () => getJson("/api/trading/binance/decision-status"),
  binanceProtectionStatus: () => getJson("/api/trading/binance/protection-status"),
  binanceExecutionLog: (limit = 50) => getJson(`/api/trading/binance/execution-log?limit=${limit}`),
  binanceExecutionPipeline: (symbol?: string) =>
    getJson(`/api/trading/binance/execution-pipeline${symbol ? `?symbol=${symbol}` : ""}`),
  binanceExecutionLogs: (limit = 100) => getJson(`/api/trading/binance/execution-logs?limit=${limit}`),
  binanceTestOrder: (body: { symbol?: string; confirm: boolean }) =>
    postJson("/api/trading/binance/test-order", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  binanceMarginCalculator: (symbol?: string) =>
    getJson(`/api/trading/binance/margin-calculator${symbol ? `?symbol=${symbol}` : ""}`),
  binanceSymbolRecommendations: () => getJson("/api/trading/binance/symbol-recommendations"),
  binanceProtectPosition: (symbol: string, body: { take_profit: number; stop_loss: number; mode?: string }) =>
    postJson(`/api/trading/binance/positions/${symbol}/protect`, {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "full_position", ...body }),
    }),
  binanceProtectionWatchdog: () => getJson("/api/trading/binance/protection-watchdog"),
  binanceTradingDiagnostics: (symbol?: string) =>
    getJson(`/api/trading/binance/diagnostics${symbol ? `?symbol=${symbol}` : ""}`),
  binanceTestStopOrder: (body: { symbol?: string; side: string; stop_price?: number; quantity?: number }) =>
    postJson("/api/trading/binance/diagnostics/test-stop-order", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  riskSettingsGet: (scope: string = "paper") => getJson(`/api/risk/settings?scope=${scope}`),
  riskSettingsUpdate: (patch: Record<string, unknown>, scope: string = "paper") =>
    putJsonDetailed(`/api/risk/settings?scope=${scope}`, patch),
  riskSettingsReset: (scope: string = "paper") => postJson(`/api/risk/settings/reset?scope=${scope}`),
  riskSettingsCopy: (fromScope: string, toScope: string, reason?: string, confirm = false) =>
    postJsonDetailed("/api/risk/settings/copy", { from_scope: fromScope, to_scope: toScope, reason, confirm }),
  riskSettingsAudit: (scope: string = "paper", limit = 200) =>
    getJson(`/api/risk/settings/audit?scope=${scope}&limit=${limit}`),
  riskSettingsBinanceSafety: () => getJson("/api/risk/settings/binance-safety"),
  riskSettingsPreviewImpact: (scope: string, patch: Record<string, unknown>, windowDays = 14) =>
    postJson("/api/risk/settings/preview-impact", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, patch, window_days: windowDays }),
    }),

  indicatorPerformance: (params: { symbol?: string; timeframe?: string; status?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.symbol) qs.set("symbol", params.symbol);
    if (params.timeframe) qs.set("timeframe", params.timeframe);
    if (params.status) qs.set("status", params.status);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return getJson(`/api/indicators/performance${suffix}`);
  },
  indicatorHistory: (id: number) => getJson(`/api/indicators/${id}/history`),
  indicatorAction: (id: number, action: string, reason?: string) =>
    postJson(`/api/indicators/${id}/action`, {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, reason }),
    }),
  indicatorNotifications: (limit = 50, unreadOnly = false) =>
    getJson(`/api/indicators/notifications?limit=${limit}${unreadOnly ? "&unread_only=true" : ""}`),
  indicatorNotificationRead: (id: number) => postJson(`/api/indicators/notifications/${id}/read`),
  indicatorNotificationsReadAll: () => postJson("/api/indicators/notifications/read-all"),
  indicatorGovernanceSettingsGet: () => getJson("/api/indicators/governance-settings"),
  indicatorGovernanceSettingsUpdate: (patch: Record<string, unknown>) =>
    putJson("/api/indicators/governance-settings", patch),
  resolverHealth: () => getJson("/api/predictions/resolver/health"),
  unresolvedSummary: (symbol?: string) => getJson(`/api/predictions/unresolved-summary${symbol ? `?symbol=${symbol}` : ""}`),
  catchupProgress: () => getJson("/api/predictions/catchup-progress"),
  lifecycleHealth: () => getJson("/api/predictions/lifecycle-health"),
  predictionResultsLatest: (params: {
    limit?: number; symbol?: string; timeframe?: string; model?: string;
    resolved?: boolean; outcome?: string; source_exchange?: string;
  } = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") q.set(k, String(v)); });
    const qs = q.toString();
    return getJson(`/api/predictions/results/latest${qs ? `?${qs}` : ""}`);
  },
  predictionAccuracySummary: () => getJson("/api/predictions/accuracy-summary"),
  predictionProviderHealth: () => getJson("/api/predictions/provider-health"),
  triggerPredictionCatchup: (limit = 200) => postJson(`/api/predictions/resolver/catchup?limit=${limit}`),
  binanceCredentialStatus: () => getJson("/api/exchange/binance/credential-status"),
};
