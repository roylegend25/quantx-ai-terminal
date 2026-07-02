import Card from "../components/Layout/Card";
import AIDecisionCenter from "../components/Dashboard/AIDecisionCenter";
import AIDecisionFlow from "../components/Dashboard/AIDecisionFlow";
import MarketRegimePanel from "../components/Dashboard/MarketRegimePanel";
import StrategyVotes from "../components/Dashboard/StrategyVotes";
import type { AppData } from "../hooks/useAppData";

export default function PredictionsPage(props: AppData) {
  const { prediction, timeframesConsensus, dashboard, symbol } = props;
  const ticker = dashboard?.symbols?.[symbol]?.ticker;
  const timeframes = timeframesConsensus?.timeframes || {};
  const consensus = timeframesConsensus?.consensus;

  return (
    <div className="page-grid">
      <Card title="AI Decision Center" full>
        <AIDecisionCenter symbol={symbol} prediction={prediction} lastUpdated={props.lastUpdated} />
      </Card>

      <Card title="AI Decision Flow" full>
        <AIDecisionFlow prediction={prediction} ticker={ticker} />
      </Card>

      <Card title="Market Regime" wide>
        <MarketRegimePanel prediction={prediction} />
      </Card>

      <Card title="Multi-Timeframe Consensus" wide>
        {consensus ? (
          <>
            <p className="regime-desc">{consensus.reason}</p>
            <div className="analytics-grid" style={{ marginTop: 12 }}>
              {Object.entries(timeframes).map(([tf, data]: [string, any]) => (
                <div className="analytics-tile" key={tf}>
                  <span className="tile-label">{tf.toUpperCase()}</span>
                  <b
                    className={`tile-value ${
                      data.direction === "LONG" ? "green" : data.direction === "SHORT" ? "red" : ""
                    }`}
                  >
                    {data.direction} · {data.confidence}%
                  </b>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="analytics-empty">Waiting for multi-timeframe evaluation.</p>
        )}
      </Card>

      <Card title="Strategy Votes" full>
        <StrategyVotes strategies={prediction?.strategies} />
      </Card>
    </div>
  );
}
