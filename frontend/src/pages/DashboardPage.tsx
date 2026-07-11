import { useState } from "react";
import { Flame } from "lucide-react";
import Card from "../components/Layout/Card";
import EditRiskModal from "../components/Dashboard/EditRiskModal";
import type { Position } from "../lib/portfolioStats";
import PredictionChart from "../components/Charts/PredictionChart";
import PredictionGauge from "../components/Dashboard/PredictionGauge";
import DecisionEngineCard from "../components/Dashboard/DecisionEngineCard";
import DecisionReasoningCard from "../components/Dashboard/DecisionReasoningCard";
import ModelVotesPanel from "../components/Dashboard/ModelVotesPanel";
import AccountOverviewCard from "../components/Dashboard/AccountOverviewCard";
import TradingModeRow from "../components/Trading/TradingModeRow";
import ServerTradingControlCard from "../components/Trading/ServerTradingControlCard";
import RiskStatusCard from "../components/Dashboard/RiskStatusCard";
import OpenPositionsCard from "../components/Dashboard/OpenPositionsCard";
import PerformanceMiniCard from "../components/Dashboard/PerformanceMiniCard";
import MarketSentimentCard from "../components/Dashboard/MarketSentimentCard";
import LiquidationHeatmapCard from "../components/Dashboard/LiquidationHeatmapCard";
import OrderBookCard from "../components/Dashboard/OrderBookCard";
import RecentTradesCard from "../components/Dashboard/RecentTradesCard";
import type { AppData } from "../hooks/useAppData";
import type { NavKey } from "../lib/nav";

type Props = AppData & { navigate: (key: NavKey) => void };

export default function DashboardPage(props: Props) {
  const { dashboard, symbol, prediction } = props;
  const ticker = dashboard?.symbols?.[symbol]?.ticker;
  const decisionEngine = prediction?.decision_engine ?? null;
  const [editingPosition, setEditingPosition] = useState<Position | null>(null);
  const editPositionById = (id: number) => {
    const p = props.positions.find((x: Position) => x.id === id);
    if (p) setEditingPosition(p);
  };
  // Paper trades for this symbol, drawn as entry/exit/SL/TP markers on the
  // chart - open positions and closed history both.
  const symbolTrades = [
    ...props.positions.filter((p: any) => p.symbol === symbol).map((p: any) => ({ ...p, status: "OPEN", mark: p.mark })),
    ...props.history.filter((t: any) => t.symbol === symbol),
  ];

  return (
    <div className="dash-grid">
      <div className="dash-row-1">
        <PredictionChart
          symbol={props.symbol}
          onSymbolChange={props.setSymbol}
          interval={props.interval}
          onIntervalChange={props.setInterval}
          candles={props.candles}
          ticker={ticker}
          prediction={prediction}
          trades={symbolTrades}
          onEditPosition={editPositionById}
        />

        <div className="stack-col">
          <PredictionGauge prediction={prediction} lastUpdated={props.lastUpdated} />
          <AccountOverviewCard portfolio={props.portfolio} positions={props.positions} />
          <RiskStatusCard portfolio={props.portfolio} positions={props.positions} history={props.history} />
        </div>
      </div>

      <TradingModeRow showToast={props.showToast} />

      <ServerTradingControlCard showToast={props.showToast} />

      <div className="dash-row-decision">
        <Card title="Active Decision Engine">
          <DecisionEngineCard
            status={props.championStatus}
            decision={decisionEngine}
            symbol={symbol}
            interval={props.interval}
          />
        </Card>

        <Card title="Why Bot Decided This">
          <DecisionReasoningCard decision={decisionEngine} regime={prediction?.regime} />
        </Card>

        <Card title="Model & Strategy Votes">
          <ModelVotesPanel votes={decisionEngine?.model_votes} finalDirection={decisionEngine?.final_direction} />
        </Card>
      </div>

      <div className="dash-row-2">
        <Card title="Open Positions">
          <OpenPositionsCard
            positions={props.positions}
            onClose={props.closePaperTrade}
            onEditRisk={setEditingPosition}
            onViewAll={props.navigate}
          />
        </Card>

        <Card
          title="Performance (30D)"
        >
          <PerformanceMiniCard portfolio={props.portfolio} history={props.history} />
        </Card>

        <Card title="Market Sentiment">
          <MarketSentimentCard marketContext={props.marketContext} />
        </Card>
      </div>

      <div className="dash-row-3">
        <Card title="Liquidation Heatmap" right={<Flame size={16} />}>
          <LiquidationHeatmapCard symbol={symbol} />
        </Card>

        <Card title={`Order Book (${symbol})`}>
          <OrderBookCard orderbook={props.orderbook} />
        </Card>

        <Card title="Recent Trades">
          <RecentTradesCard trades={props.trades} />
        </Card>
      </div>

      {editingPosition && (
        <EditRiskModal
          position={editingPosition}
          onSave={props.updatePositionRisk}
          onClose={() => setEditingPosition(null)}
        />
      )}
    </div>
  );
}
