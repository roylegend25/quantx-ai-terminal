import Card from "../components/Layout/Card";
import BacktestCard from "../components/Dashboard/BacktestCard";
import type { AppData } from "../hooks/useAppData";

export default function BacktestingPage(props: AppData) {
  const { backtest, runBacktest, symbol } = props;

  return (
    <div className="page-grid">
      <Card title={`Backtest Lab (${symbol})`} full>
        <BacktestCard backtest={backtest} runBacktest={() => runBacktest("5m")} />
      </Card>
    </div>
  );
}
