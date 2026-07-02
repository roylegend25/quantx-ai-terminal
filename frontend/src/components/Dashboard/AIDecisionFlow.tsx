type Signal = "bullish" | "bearish" | "neutral";

type StrategyResult = {
  direction: string;
  confidence: number;
  reason: string;
};

type Prediction = {
  direction?: string;
  confidence?: number;
  regime?: string;
  features?: {
    rsi?: number;
  };
  strategies?: Record<string, StrategyResult>;
  risk?: {
    allowed: boolean;
    reason: string;
  };
};

type Ticker = {
  priceChangePercent?: string | number;
};

type Props = {
  prediction: Prediction | null;
  ticker?: Ticker | null;
};

type FlowNodeData = {
  key: string;
  title: string;
  status: string;
  confidence: number;
  signal: Signal;
};

const STRATEGY_DEFS = [
  { key: "trend", title: "Trend Strategy" },
  { key: "momentum", title: "Momentum Strategy" },
  { key: "mean_reversion", title: "Mean Reversion" },
  { key: "breakout", title: "Breakout" },
];

function signalFromDirection(direction?: string): Signal {
  if (direction === "LONG") return "bullish";
  if (direction === "SHORT") return "bearish";
  return "neutral";
}

function toneClass(signal: Signal): string {
  if (signal === "bullish") return "green";
  if (signal === "bearish") return "red";
  return "yellow";
}

function FlowStage({ node, active }: { node: FlowNodeData; active: boolean }) {
  const tone = toneClass(node.signal);
  const confidence = Math.min(100, Math.max(0, node.confidence));

  return (
    <div className={`flow-node ${active ? "flow-active" : "flow-idle"}`}>
      <div className="flow-node-head">
        <span className={`flow-dot ${tone} ${active ? "flow-pulse" : ""}`} />
        <h4>{node.title}</h4>
      </div>
      <p className="flow-status">{node.status}</p>
      <div className="flow-meta">
        <span className={`flow-signal ${tone}`}>{node.signal.toUpperCase()}</span>
        <b className="flow-confidence">{confidence.toFixed(0)}%</b>
      </div>
      <progress value={confidence} max={100} />
    </div>
  );
}

function FlowConnector() {
  return (
    <div className="flow-connector" aria-hidden="true">
      <span className="flow-line" />
      <span className="flow-arrow">▼</span>
    </div>
  );
}

export default function AIDecisionFlow({ prediction, ticker }: Props) {
  const change = Number(ticker?.priceChangePercent ?? 0);
  const marketActive = !!ticker;
  const marketNode: FlowNodeData = {
    key: "market",
    title: "Market Data",
    status: marketActive ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}% 24h` : "Waiting for feed",
    confidence: Math.min(100, Math.abs(change) * 10),
    signal: change > 0.05 ? "bullish" : change < -0.05 ? "bearish" : "neutral",
  };

  const rsi = prediction?.features?.rsi;
  const featuresReady = typeof rsi === "number";
  const featureNode: FlowNodeData = {
    key: "features",
    title: "Feature Engine",
    status: featuresReady
      ? `RSI ${rsi!.toFixed(1)} · Regime ${prediction?.regime ?? "—"}`
      : "Waiting for features",
    confidence: featuresReady ? Math.min(100, Math.abs(rsi! - 50) * 2) : 0,
    signal: !featuresReady ? "neutral" : rsi! > 55 ? "bullish" : rsi! < 45 ? "bearish" : "neutral",
  };

  const strategyNodes: FlowNodeData[] = STRATEGY_DEFS.map((s) => {
    const data = prediction?.strategies?.[s.key];
    return {
      key: s.key,
      title: s.title,
      status: data?.reason ?? "No signal yet",
      confidence: data?.confidence ?? 0,
      signal: data ? signalFromDirection(data.direction) : "neutral",
    };
  });

  const ensembleSignal = signalFromDirection(prediction?.direction);
  const ensembleNode: FlowNodeData = {
    key: "ensemble",
    title: "Ensemble AI",
    status: prediction ? `Regime: ${prediction.regime ?? "—"}` : "Awaiting strategy votes",
    confidence: prediction?.confidence ?? 0,
    signal: ensembleSignal,
  };

  const riskAllowed = prediction?.risk?.allowed ?? false;
  const riskNode: FlowNodeData = {
    key: "risk",
    title: "Risk Manager",
    status: prediction?.risk?.reason ?? "Awaiting ensemble decision",
    confidence: prediction?.confidence ?? 0,
    signal: prediction ? (riskAllowed ? ensembleSignal : "neutral") : "neutral",
  };

  const finalDirection = prediction ? (riskAllowed ? prediction.direction : "NO_TRADE") : undefined;
  const finalNode: FlowNodeData = {
    key: "final",
    title: "Final Decision",
    status: finalDirection ?? "Awaiting pipeline",
    confidence: prediction?.confidence ?? 0,
    signal: signalFromDirection(finalDirection),
  };

  return (
    <div className="flow-pipeline">
      <FlowStage node={marketNode} active={marketActive} />
      <FlowConnector />

      <FlowStage node={featureNode} active={featuresReady} />
      <FlowConnector />

      <div className="flow-strategy-row">
        {strategyNodes.map((node) => (
          <FlowStage node={node} active={!!prediction?.strategies?.[node.key]} key={node.key} />
        ))}
      </div>
      <FlowConnector />

      <FlowStage node={ensembleNode} active={!!prediction} />
      <FlowConnector />

      <FlowStage node={riskNode} active={!!prediction} />
      <FlowConnector />

      <FlowStage node={finalNode} active={!!prediction} />
    </div>
  );
}
