import { Flame } from "lucide-react";
import Card from "../components/Layout/Card";
import ChartPanel from "../components/Dashboard/ChartPanel";
import PredictionGauge from "../components/Dashboard/PredictionGauge";
import AccountOverviewCard from "../components/Dashboard/AccountOverviewCard";
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

  return (
    <div className="dash-grid">
      <div className="dash-row-1">
        <ChartPanel
          symbol={props.symbol}
          onSymbolChange={props.setSymbol}
          interval={props.interval}
          onIntervalChange={props.setInterval}
          candles={props.candles}
          ticker={ticker}
          prediction={prediction}
        />

        <PredictionGauge prediction={prediction} />

        <div className="stack-col">
          <AccountOverviewCard portfolio={props.portfolio} positions={props.positions} />
          <RiskStatusCard portfolio={props.portfolio} positions={props.positions} history={props.history} />
        </div>
      </div>

      <div className="dash-row-2">
        <Card title="Open Positions">
          <OpenPositionsCard
            positions={props.positions}
            onClose={props.closePaperTrade}
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
    </div>
  );
}
