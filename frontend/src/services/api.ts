import { authFetch } from "./auth";

const API = "";

async function getJson<T = any>(url: string): Promise<T> {
  const res = await authFetch(`${API}${url}`);
  return res.json();
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

export const api = {
  dashboard: () => getJson("/api/dashboard"),
  prediction: (symbol: string, interval: string) =>
    getJson(`/api/prediction/${symbol}?interval=${interval}`),
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
  timeframes: (symbol: string) => getJson(`/api/timeframes/${symbol}`),
  strategyWeights: () => getJson("/api/strategy/weights"),
  botStatus: () => getJson("/api/bot/status"),
  botAction: (action: string) => postJson(`/api/bot/${action}`),
  portfolio: () => getJson("/api/paper/portfolio"),
  positions: () => getJson("/api/paper/positions"),
  history: () => getJson("/api/paper/history"),
  openPaperTrade: (symbol: string, side: "LONG" | "SHORT", usdtSize = 1000) =>
    postJson(`/api/paper/open?symbol=${symbol}&side=${side}&usdt_size=${usdtSize}`),
  closePaperTrade: (id: number) => postJson(`/api/paper/close/${id}`),
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
  riskSettingsGet: () => getJson("/api/risk/settings"),
  riskSettingsUpdate: (patch: Record<string, unknown>) => putJson("/api/risk/settings", patch),
  riskSettingsReset: () => postJson("/api/risk/settings/reset"),
};
