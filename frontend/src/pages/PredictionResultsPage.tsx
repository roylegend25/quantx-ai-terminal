import { useCallback, useEffect, useMemo, useState } from "react";
import { CartesianGrid, Line, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import Card from "../components/Layout/Card";
import { api } from "../services/api";
import { fmtLocalDateTime } from "../lib/format";

type ResultRow = {
  prediction_id: string;
  symbol: string;
  direction: string;
  predicted_at: string | null;
  due_at: string | null;
  resolved_at: string | null;
  timeframe: string;
  horizon_seconds: number;
  entry_price: number | null;
  resolved_price: number | null;
  actual_return: number | null;
  outcome: string;
  correct: boolean | null;
  model_strategy: string;
  source_type: string;
  engine: string;
  original_source: string;
  resolution_source: string | null;
  resolution_exchange: string | null;
  fallback_used: boolean | null;
  resolution_confidence: number | null;
  unresolved_reason: string | null;
  confidence: number;
  status: string;
};

type AccuracyRow = {
  key: string;
  total_predictions: number;
  resolved_eligible: number;
  correct: number;
  wrong: number;
  neutral: number;
  directional_accuracy: number | null;
  coverage_rate: number | null;
};

type AccuracySummary = {
  combined: AccuracyRow | null;
  by_symbol: Record<string, AccuracyRow>;
};

type ResolverHealth = {
  healthy: boolean;
  total_due: number;
  total_overdue: number;
  btc: { due: number; overdue: number };
  eth: { due: number; overdue: number };
  last_success: string | null;
  last_error: string | null;
};

const OUTCOME_META: Record<string, { dot: string; label: string; cls: string }> = {
  correct: { dot: "\u{1F7E2}", label: "Correct", cls: "green" },
  wrong: { dot: "\u{1F534}", label: "Wrong", cls: "red" },
  neutral: { dot: "\u{1F7E1}", label: "Neutral", cls: "yellow" },
  unresolved_not_due: { dot: "⚪", label: "Not due yet", cls: "" },
  unresolved_due: { dot: "⚪", label: "Awaiting resolution", cls: "" },
  overdue_provider_error: { dot: "\u{1F7E0}", label: "Overdue / provider issue", cls: "orange" },
};

const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"];
const OUTCOMES = ["correct", "wrong", "neutral", "unresolved_not_due", "overdue_provider_error"];

function pctFmt(v: number | null | undefined): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function OutcomeDot({ outcome }: { outcome: string }) {
  const meta = OUTCOME_META[outcome] ?? OUTCOME_META.unresolved_due;
  return <span title={meta.label} aria-label={meta.label}>{meta.dot}</span>;
}

export default function PredictionResultsPage() {
  const [results, setResults] = useState<ResultRow[]>([]);
  const [accuracy, setAccuracy] = useState<AccuracySummary | null>(null);
  const [health, setHealth] = useState<ResolverHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [symbolFilter, setSymbolFilter] = useState<"All" | "BTC" | "ETH">("All");
  const [timeframeFilter, setTimeframeFilter] = useState<string>("");
  const [resolvedFilter, setResolvedFilter] = useState<"" | "resolved" | "unresolved">("");
  const [outcomeFilter, setOutcomeFilter] = useState<string>("");
  const [modelFilter, setModelFilter] = useState<string>("");
  const [exchangeFilter, setExchangeFilter] = useState<string>("");

  const load = useCallback(async () => {
    try {
      const [resultsRes, accuracyRes, healthRes] = await Promise.all([
        api.predictionResultsLatest({
          limit: 10,
          symbol: symbolFilter === "All" ? undefined : symbolFilter,
          timeframe: timeframeFilter || undefined,
          model: modelFilter || undefined,
          resolved: resolvedFilter === "" ? undefined : resolvedFilter === "resolved",
          outcome: outcomeFilter || undefined,
          source_exchange: exchangeFilter || undefined,
        }),
        api.predictionAccuracySummary(),
        api.resolverHealth(),
      ]);
      setResults((resultsRes as { results: ResultRow[] }).results);
      setAccuracy(accuracyRes as AccuracySummary);
      setHealth(healthRes as ResolverHealth);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load prediction results");
    } finally {
      setLoading(false);
    }
  }, [symbolFilter, timeframeFilter, resolvedFilter, outcomeFilter, modelFilter, exchangeFilter]);

  useEffect(() => {
    load();
    const id = setInterval(load, 20000);
    return () => clearInterval(id);
  }, [load]);

  const timelineData = useMemo(
    () =>
      [...results]
        .filter((r) => r.predicted_at)
        .reverse()
        .map((r, i) => ({
          index: i,
          label: r.predicted_at ? fmtLocalDateTime(r.predicted_at) : "",
          actual_return: r.actual_return != null ? r.actual_return * 100 : null,
          outcome: r.outcome,
          symbol: r.symbol,
        })),
    [results]
  );

  const btcAcc = accuracy?.by_symbol?.BTCUSDT;
  const ethAcc = accuracy?.by_symbol?.ETHUSDT;
  const combinedAcc = accuracy?.combined;

  return (
    <div className="page-grid analysis-page">
      <Card title="Prediction Results" full>
        <div className="engine-metric-grid" style={{ gridTemplateColumns: "repeat(4, minmax(0,1fr))" }}>
          <div>
            <div className="engine-metric-value">{pctFmt(btcAcc?.directional_accuracy)}</div>
            <span>BTC accuracy ({btcAcc?.resolved_eligible ?? 0} resolved)</span>
          </div>
          <div>
            <div className="engine-metric-value">{pctFmt(ethAcc?.directional_accuracy)}</div>
            <span>ETH accuracy ({ethAcc?.resolved_eligible ?? 0} resolved)</span>
          </div>
          <div>
            <div className="engine-metric-value">{pctFmt(combinedAcc?.directional_accuracy)}</div>
            <span>Combined directional accuracy</span>
          </div>
          <div>
            <div className="engine-metric-value">{pctFmt(combinedAcc?.coverage_rate)}</div>
            <span>Resolved coverage</span>
          </div>
          <div>
            <div className="engine-metric-value">{health?.total_overdue ?? "—"}</div>
            <span>Overdue unresolved (BTC {health?.btc.overdue ?? 0} / ETH {health?.eth.overdue ?? 0})</span>
          </div>
          <div>
            <div className={`engine-metric-value ${health?.healthy ? "green" : "orange"}`}>
              {health ? (health.healthy ? "Healthy" : "Degraded") : "—"}
            </div>
            <span>Resolver status</span>
          </div>
          <div>
            <div className="engine-metric-value">{health?.last_success ? fmtLocalDateTime(health.last_success) : "—"}</div>
            <span>Last successful resolution</span>
          </div>
          <div>
            <div className={`engine-metric-value ${health?.last_error ? "red" : "green"}`}>
              {health?.last_error ? "Error" : "OK"}
            </div>
            <span>Data-source health</span>
          </div>
        </div>
      </Card>

      <Card title="Filters" full>
        <div className="chip-row" style={{ flexWrap: "wrap", gap: 8 }}>
          {(["All", "BTC", "ETH"] as const).map((s) => (
            <button key={s} className={symbolFilter === s ? "active" : ""} onClick={() => setSymbolFilter(s)}>
              {s}
            </button>
          ))}
          <select value={timeframeFilter} onChange={(e) => setTimeframeFilter(e.target.value)}>
            <option value="">All timeframes</option>
            {TIMEFRAMES.map((tf) => (
              <option key={tf} value={tf}>{tf}</option>
            ))}
          </select>
          <select value={resolvedFilter} onChange={(e) => setResolvedFilter(e.target.value as typeof resolvedFilter)}>
            <option value="">Resolved + Unresolved</option>
            <option value="resolved">Resolved only</option>
            <option value="unresolved">Unresolved only</option>
          </select>
          <select value={outcomeFilter} onChange={(e) => setOutcomeFilter(e.target.value)}>
            <option value="">All outcomes</option>
            {OUTCOMES.map((o) => (
              <option key={o} value={o}>{OUTCOME_META[o]?.label ?? o}</option>
            ))}
          </select>
          <input
            placeholder="Model / strategy name"
            value={modelFilter}
            onChange={(e) => setModelFilter(e.target.value)}
            style={{ minWidth: 160 }}
          />
          <select value={exchangeFilter} onChange={(e) => setExchangeFilter(e.target.value)}>
            <option value="">All source exchanges</option>
            <option value="binance">Binance</option>
            <option value="bybit">Bybit</option>
            <option value="okx">OKX</option>
            <option value="hyperliquid">Hyperliquid</option>
          </select>
        </div>
      </Card>

      <Card title="Latest 10 Predictions — Timeline" full>
        {timelineData.length ? (
          <ResponsiveContainer width="100%" height={180}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="index" tick={{ fontSize: 10, fill: "#8b90a8" }} tickFormatter={(i) => timelineData[i]?.label ?? ""} />
              <YAxis dataKey="actual_return" tick={{ fontSize: 10, fill: "#8b90a8" }} unit="%" />
              <ZAxis range={[80, 80]} />
              <Tooltip
                contentStyle={{ background: "rgba(12,13,24,0.95)", border: "1px solid rgba(124,92,255,0.35)", borderRadius: 10, fontSize: 12 }}
                formatter={(value: unknown) => [`${typeof value === "number" ? value.toFixed(2) : value}%`, "Actual return"]}
                labelFormatter={(i) => timelineData[Number(i)]?.label ?? ""}
              />
              <Scatter
                data={timelineData}
                fill="#7c5cff"
                shape={(props: any) => {
                  const meta = OUTCOME_META[props.payload.outcome] ?? OUTCOME_META.unresolved_due;
                  const color = meta.cls === "green" ? "#2ecc71" : meta.cls === "red" ? "#e74c3c" : meta.cls === "yellow" ? "#f1c40f" : meta.cls === "orange" ? "#ff9f43" : "#8b90a8";
                  return <circle cx={props.cx} cy={props.cy} r={5} fill={props.payload.actual_return == null ? "none" : color} stroke={color} strokeWidth={2} />;
                }}
              />
              <Line type="monotone" dataKey="actual_return" stroke="rgba(124,92,255,0.3)" dot={false} connectNulls={false} />
            </ScatterChart>
          </ResponsiveContainer>
        ) : (
          <p className="analytics-empty">No predictions to chart yet.</p>
        )}
      </Card>

      <Card title="Latest 10 Predictions" full>
        {loading && <p className="analytics-empty">Loading…</p>}
        {error && <p className="analytics-empty red">{error}</p>}
        {!loading && !error && results.length === 0 && <p className="analytics-empty">No predictions match these filters.</p>}
        {!loading && !error && results.length > 0 && (
          <div className="analysis-source-grid">
            {results.map((r) => (
              <article className="analysis-source-card" key={r.prediction_id}>
                <div className="vote-head">
                  <b>
                    <OutcomeDot outcome={r.outcome} /> {r.symbol} · {r.direction}
                  </b>
                  <span className={`chip ${r.direction === "LONG" ? "green" : r.direction === "SHORT" ? "red" : "yellow"}`}>{r.timeframe}</span>
                </div>
                <div className="engine-metric-grid" style={{ gridTemplateColumns: "repeat(2, minmax(0,1fr))", fontSize: 12 }}>
                  <span>Predicted: {r.predicted_at ? fmtLocalDateTime(r.predicted_at) : "—"}</span>
                  <span>Due: {r.due_at ? fmtLocalDateTime(r.due_at) : "—"}</span>
                  <span>Resolved: {r.resolved_at ? fmtLocalDateTime(r.resolved_at) : "—"}</span>
                  <span>Entry: {r.entry_price != null ? r.entry_price.toFixed(2) : "—"}</span>
                  <span>Resolved price: {r.resolved_price != null ? r.resolved_price.toFixed(2) : "—"}</span>
                  <span>Return: {r.actual_return != null ? `${(r.actual_return * 100).toFixed(2)}%` : "—"}</span>
                  <span>Model/strategy: {r.model_strategy}</span>
                  <span>Original source: {r.original_source}</span>
                  <span>Resolution source: {r.resolution_source ?? "—"}{r.fallback_used ? " (fallback)" : ""}</span>
                  <span>Confidence: {(r.confidence * 100).toFixed(0)}%</span>
                  <span>Status: {r.unresolved_reason ?? r.status}</span>
                  <span>Engine: {r.engine}</span>
                </div>
              </article>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
