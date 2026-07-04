import Card from "../components/Layout/Card";
import LiquidationHeatmapCard from "../components/Dashboard/LiquidationHeatmapCard";
import OrderBookCard from "../components/Dashboard/OrderBookCard";
import RecentTradesCard from "../components/Dashboard/RecentTradesCard";
import MarketSentimentCard from "../components/Dashboard/MarketSentimentCard";
import type { AppData } from "../hooks/useAppData";

export default function MarketPage(props: AppData) {
  const { symbol, orderbook, trades, marketContext } = props;

  return (
    <div className="page-grid">
      <Card title="Market Sentiment" full>
        <MarketSentimentCard marketContext={marketContext} />
      </Card>

      <Card title="Liquidation Heatmap">
        <LiquidationHeatmapCard symbol={symbol} />
      </Card>

      <Card title={`Order Book (${symbol})`}>
        <OrderBookCard orderbook={orderbook} rows={10} />
      </Card>

      <Card title="Recent Trades">
        <RecentTradesCard trades={trades} rows={14} />
      </Card>
    </div>
  );
}
