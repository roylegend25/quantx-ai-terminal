import { useState } from "react";
import { Flame } from "lucide-react";
import Card from "../../components/Layout/Card";
import EditRiskModal from "../../components/Dashboard/EditRiskModal";
import type { Position } from "../../lib/portfolioStats";
import PredictionChart from "../../components/Charts/PredictionChart";
import DecisionEngineCard from "../../components/Dashboard/DecisionEngineCard";
import DecisionReasoningCard from "../../components/Dashboard/DecisionReasoningCard";
import ExecutionPipelineCard from "../../components/Dashboard/ExecutionPipelineCard";
import LiveMarginCalculatorCard from "../../components/Dashboard/LiveMarginCalculatorCard";
import TradingRecommendationsCard from "../../components/Dashboard/TradingRecommendationsCard";
import ModelVotesPanel from "../../components/Dashboard/ModelVotesPanel";
import AccountOverviewCard from "../../components/Dashboard/AccountOverviewCard";
import TradingModeRow from "../../components/Trading/TradingModeRow";
import ServerTradingControlCard from "../../components/Trading/ServerTradingControlCard";
import RiskStatusCard from "../../components/Dashboard/RiskStatusCard";
import OpenPositionsCard from "../../components/Dashboard/OpenPositionsCard";
import PerformanceMiniCard from "../../components/Dashboard/PerformanceMiniCard";
import MarketSentimentCard from "../../components/Dashboard/MarketSentimentCard";
import LiquidationHeatmapCard from "../../components/Dashboard/LiquidationHeatmapCard";
import OrderBookCard from "../../components/Dashboard/OrderBookCard";
import RecentTradesCard from "../../components/Dashboard/RecentTradesCard";
import { useExecutionPipeline } from "../../hooks/useExecutionPipeline";
import { useMarginCalculator } from "../../hooks/useMarginCalculator";
import { AutomaticExecutionBanner, useTradingStatus } from "../../components/Trading/TradingShared";
import type { AppData } from "../../hooks/useAppData";
import type { NavKey } from "../../lib/nav";
import PremiumPageShell from "../components/PremiumPageShell";
import ChartCard from "../components/ChartCard";
import MarketAssetIcon from "../components/MarketAssetIcon";
import PremiumTooltip from "../components/PremiumTooltip";

type Props = AppData & { navigate: (key: NavKey) => void };

/** Bespoke Tier-A dashboard composition. Reuses every Tier-B leaf
 * (PredictionChart/ProChartCanvas, execution pipeline, margin calculator,
 * risk/account cards, positions) completely unmodified - only the
 * surrounding chrome and grid are new. See plan section "Per-page
 * mechanism". */
export default function PremiumDashboardPage(props: Props) {
  const { dashboard, symbol, prediction } = props;
  const ticker = dashboard?.symbols?.[symbol]?.ticker;
  const decisionEngine = prediction?.decision_engine ?? null;
  const { status: tradingStatus } = useTradingStatus();
  const liveActive = tradingStatus?.active_mode === "BINANCE_LIVE";
  const { pipeline, loading: pipelineLoading, errored: pipelineErrored, reload: reloadPipeline } =
    useExecutionPipeline(symbol);
  const { data: marginData, loading: marginLoading, errored: marginErrored, reload: reloadMargin } =
    useMarginCalculator(symbol, liveActive);
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
  const symbolTrades = [
    ...props.positions.filter((p: any) => p.symbol === symbol).map((p: any) => ({ ...p, status: "OPEN", mark: p.mark })),
    ...props.history.filter((t: any) => t.symbol === symbol),
  ];

  return (
    <PremiumPageShell
      title="Dashboard"
      subtitle={
        <>
          AI-powered predictions for {symbol}
          <PremiumTooltip label="Forecast, confidence bands, and decision pipeline all come from the same live Champion model." />
        </>
      }
      right={<MarketAssetIcon symbol={symbol} size="card" />}
    >
      <div className="dash-grid">
        <AutomaticExecutionBanner status={tradingStatus} />
        <div className="dash-row-1">
          {/* Chrome only - PredictionChart already renders its own rich
             header (symbol/interval selector, indicators, hit-rate stats)
             AND the single authoritative decision summary
             (DecisionSummaryTiles), so this wraps it without a second,
             duplicate title or a second metric row. */}
          <ChartCard>
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
          </ChartCard>

          <div className="stack-col">
            <Card title="Account Overview">
              <AccountOverviewCard portfolio={props.portfolio} positions={props.positions} />
            </Card>
            <Card title="Live Risk Status">
              <RiskStatusCard portfolio={props.portfolio} positions={props.positions} history={props.history} />
            </Card>
            <Card title="Performance (30D)">
              <PerformanceMiniCard portfolio={props.portfolio} history={props.history} />
            </Card>
          </div>
        </div>

        <TradingModeRow showToast={props.showToast} />

        <ServerTradingControlCard showToast={props.showToast} />

        <div className="dash-row-decision">
          <Card title="Active Decision Engine">
            <DecisionEngineCard status={props.championStatus} decision={decisionEngine} symbol={symbol} interval={props.interval} />
          </Card>

          <Card title="Why Bot Decided This">
            <DecisionReasoningCard decision={decisionEngine} regime={prediction?.regime} executionOutcome={executionOutcome} />
          </Card>

          <Card title="Model & Strategy Votes">
            <ModelVotesPanel candidates={decisionEngine?.candidates} finalDirection={decisionEngine?.final_signal} />
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

        <div className="dash-row-2">
          <Card title="Open Positions">
            <OpenPositionsCard positions={props.positions} onClose={props.closePaperTrade} onEditRisk={setEditingPosition} onViewAll={props.navigate} />
          </Card>

          <Card title="Market Sentiment">
            <MarketSentimentCard marketContext={props.marketContext} />
          </Card>

          <Card title="Recent Trades">
            <RecentTradesCard trades={props.trades} />
          </Card>
        </div>

        <div className="dash-row-3">
          <Card title="Liquidation Heatmap" right={<Flame size={16} />}>
            <LiquidationHeatmapCard symbol={symbol} />
          </Card>

          <Card title={`Order Book (${symbol})`}>
            <OrderBookCard orderbook={props.orderbook} updatedAt={props.orderbookUpdatedAt} />
          </Card>
        </div>

        {editingPosition && (
          <EditRiskModal position={editingPosition} onSave={props.updatePositionRisk} onClose={() => setEditingPosition(null)} />
        )}
      </div>
    </PremiumPageShell>
  );
}
