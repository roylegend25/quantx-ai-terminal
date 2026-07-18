import { useState } from "react";
import { Flame } from "lucide-react";
import Card from "../components/Layout/Card";
import EditRiskModal from "../components/Dashboard/EditRiskModal";
import type { Position } from "../lib/portfolioStats";
import PredictionChart from "../components/Charts/PredictionChart";
import PredictionGauge from "../components/Dashboard/PredictionGauge";
import DecisionEngineCard from "../components/Dashboard/DecisionEngineCard";
import DecisionReasoningCard from "../components/Dashboard/DecisionReasoningCard";
import ExecutionPipelineCard from "../components/Dashboard/ExecutionPipelineCard";
import LiveMarginCalculatorCard from "../components/Dashboard/LiveMarginCalculatorCard";
import TradingRecommendationsCard from "../components/Dashboard/TradingRecommendationsCard";
import ModelVotesPanel from "../components/Dashboard/ModelVotesPanel";
import AccountOverviewCard from "../components/Dashboard/AccountOverviewCard";
import TradingModeRow from "../components/Trading/TradingModeRow";
import ServerTradingControlCard from "../components/Trading/ServerTradingControlCard";
import RiskStatusCard from "../components/Dashboard/RiskStatusCard";
import OpenPositionsCard from "../components/Dashboard/OpenPositionsCard";
import CombinedPositionsCard from "../components/Dashboard/CombinedPositionsCard";
import DecisionRiskEnvelopeCard from "../components/Dashboard/DecisionRiskEnvelopeCard";
import PerformanceMiniCard from "../components/Dashboard/PerformanceMiniCard";
import MarketSentimentCard from "../components/Dashboard/MarketSentimentCard";
import LiquidationHeatmapCard from "../components/Dashboard/LiquidationHeatmapCard";
import OrderBookCard from "../components/Dashboard/OrderBookCard";
import RecentTradesCard from "../components/Dashboard/RecentTradesCard";
import { useExecutionPipeline } from "../hooks/useExecutionPipeline";
import { useMarginCalculator } from "../hooks/useMarginCalculator";
import { useBinanceAccount } from "../hooks/useBinanceAccount";
import { AutomaticExecutionBanner, useTradingStatus } from "../components/Trading/TradingShared";
import type { AppData } from "../hooks/useAppData";
import type { NavKey } from "../lib/nav";
import LiveDecisionPanel from "../components/Dashboard/LiveDecisionPanel";

type Props = AppData & { navigate: (key: NavKey) => void };

export default function DashboardPage(props: Props) {
  const { dashboard, symbol, prediction } = props;
  const ticker = dashboard?.symbols?.[symbol]?.ticker;
  const decisionEngine = prediction?.decision_engine ?? null;
  const { status: tradingStatus } = useTradingStatus();
  const liveActive = tradingStatus?.active_mode === "BINANCE_LIVE";
  const { pipeline, loading: pipelineLoading, errored: pipelineErrored, reload: reloadPipeline } =
    useExecutionPipeline(symbol);
  const { data: marginData, loading: marginLoading, errored: marginErrored, reload: reloadMargin } =
    useMarginCalculator(symbol, liveActive);
  const { positions: binancePositions, positionRows: binancePositionRows } = useBinanceAccount(props.showToast);
  const executionOutcome = pipeline
    ? {
        attempted: !!pipeline.execution_attempted,
        ok: pipeline.execution_ok,
        reason: pipeline.final_reason,
        historical: !!pipeline.is_historical_attempt,
      }
    : null;
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
      <AutomaticExecutionBanner status={tradingStatus} />
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

      <LiveDecisionPanel symbol={symbol} timeframe={props.interval} prediction={prediction} />

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
          <DecisionReasoningCard decision={decisionEngine} regime={prediction?.regime} executionOutcome={executionOutcome} />
        </Card>

        <Card title="Current Decision Contributors">
          <ModelVotesPanel
            candidates={decisionEngine?.candidates}
            finalDirection={decisionEngine?.final_signal}
            enginePoints={{ long: decisionEngine?.long_points, short: decisionEngine?.short_points }}
          />
        </Card>
      </div>

      <Card title="Execution Pipeline">
        <ExecutionPipelineCard
          symbol={symbol}
          pipeline={pipeline}
          simulation={marginData?.simulation}
          statusChecklist={marginData?.status_checklist}
          loading={pipelineLoading}
          errored={pipelineErrored}
          onRefresh={reloadPipeline}
          showToast={props.showToast}
          activeMode={tradingStatus?.active_mode}
        />
      </Card>

      <LiveMarginCalculatorCard
        symbol={symbol}
        data={marginData}
        loading={marginLoading}
        errored={marginErrored}
        liveActive={liveActive}
        onRefresh={reloadMargin}
        showToast={props.showToast}
      />

      <TradingRecommendationsCard symbol={symbol} marginData={marginData} liveActive={liveActive} />

      <Card title="Decision Risk Envelope">
        <DecisionRiskEnvelopeCard
          prediction={prediction}
          paperAvailableMargin={props.portfolio?.available_margin}
          marginData={marginData}
          marginErrored={marginErrored}
        />
      </Card>

      <Card title="Combined Positions Overview">
        <CombinedPositionsCard
          paperPositions={props.positions}
          binancePositionRows={binancePositionRows}
          binanceUnavailable={binancePositions?.available === false}
          binanceUnavailableReason={binancePositions?.reason}
          binanceStale={binancePositions?.stale}
        />
      </Card>

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
          <OrderBookCard orderbook={props.orderbook} updatedAt={props.orderbookUpdatedAt} />
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
