import { authFetch } from "./auth";

const API = "";

async function getJson<T = any>(url: string): Promise<T> {
  const res = await authFetch(`${API}${url}`);
  return res.json();
}

export const api = {
  dashboard: () => getJson("/api/dashboard"),
  prediction: (symbol: string, interval: string) =>
    getJson(`/api/prediction/${symbol}?interval=${interval}`),
  candles: (symbol: string, interval: string, limit = 220) =>
    getJson(`/api/market/${symbol}/candles?interval=${interval}&limit=${limit}`),
  orderbook: (symbol: string, limit = 10) => getJson(`/api/orderbook/${symbol}?limit=${limit}`),
  trades: (symbol: string, limit = 20) => getJson(`/api/trades/${symbol}?limit=${limit}`),
  marketContext: (symbol: string) => getJson(`/api/market/context?symbol=${symbol}`),
  timeframes: (symbol: string) => getJson(`/api/timeframes/${symbol}`),
  strategyWeights: () => getJson("/api/strategy/weights"),
  botStatus: () => getJson("/api/bot/status"),
  botAction: async (action: string) => {
    const res = await authFetch(`${API}/api/bot/${action}`, { method: "POST" });
    return res.json();
  },
  portfolio: () => getJson("/api/paper/portfolio"),
  positions: () => getJson("/api/paper/positions"),
  history: () => getJson("/api/paper/history"),
  openPaperTrade: async (symbol: string, side: "LONG" | "SHORT", usdtSize = 1000) => {
    const res = await authFetch(
      `${API}/api/paper/open?symbol=${symbol}&side=${side}&usdt_size=${usdtSize}`,
      { method: "POST" }
    );
    return res.json();
  },
  closePaperTrade: async (id: number) => {
    const res = await authFetch(`${API}/api/paper/close/${id}`, { method: "POST" });
    return res.json();
  },
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
};
