import { useState } from "react";
import Card from "../components/Layout/Card";
import OpenPositionsCard from "../components/Dashboard/OpenPositionsCard";
import EditRiskModal, { type RiskPatch } from "../components/Dashboard/EditRiskModal";
import AutoCardTable, { type AutoCardColumn } from "../components/Responsive/AutoCardTable";
import PaperLiveTabs, { type PaperLiveTab } from "../components/Trading/PaperLiveTabs";
import BinancePositionsTable from "../components/Trading/BinancePositionsTable";
import CloseAllPositionsModal from "../components/Trading/CloseAllPositionsModal";
import { useBinanceLiveGate } from "../components/Trading/BinanceLiveGate";
import { api } from "../services/api";
import { useTradingStatus } from "../components/Trading/TradingShared";
import { useBinanceAccount } from "../hooks/useBinanceAccount";
import { fmtLocalDateTime, fmtNum, fmtTradeDuration, fmtUsd, toneClass, toneOf } from "../lib/format";
import type { AppData } from "../hooks/useAppData";
import type { Position } from "../lib/portfolioStats";
import type { NavKey } from "../lib/nav";

const ENGINE_LABEL: Record<string, string> = {
  champion_ml: "Champion ML",
  strategy_ensemble: "Strategy Ensemble",
  fallback: "Fallback",
  manual: "Manual",
};

type Props = AppData & { navigate: (key: NavKey) => void };

const SYMBOL_FILTERS = ["ALL", "BTCUSDT", "ETHUSDT"] as const;
type SymbolFilter = (typeof SYMBOL_FILTERS)[number];

const RESET_CONFIRM_TEXT =
  "This will delete all paper trades, close paper positions, and reset balance to $10,000. This does NOT affect live funds.";

type RiskGate = { allowed: boolean; reason: string };

function computeRiskGate(prediction: any, openCount: number, executionStatus: any): RiskGate {
  const predRisk = prediction?.risk;

  if (predRisk && predRisk.allowed === false) {
    return { allowed: false, reason: predRisk.reason || "Risk checks blocked" };
  }

  const maxOpen = executionStatus?.safety?.max_open_positions;
  if (typeof maxOpen === "number" && openCount >= maxOpen) {
    return { allowed: false, reason: "Maximum open positions reached" };
  }

  if (executionStatus?.circuit_breaker?.open) {
    return { allowed: false, reason: "Circuit breaker open - too many recent execution failures" };
  }

  if (predRisk) {
    return { allowed: !!predRisk.allowed, reason: predRisk.reason || "Risk checks passed" };
  }

  return { allowed: false, reason: "Waiting for prediction data" };
}

function JournalDetail({ t }: { t: any }) {
  return (
    <div className="journal-detail">
      <div className="journal-detail-grid">
        <div>
          <span className="tile-label">Decision Engine</span>
          <b className="tile-value">{t.decision_mode ? ENGINE_LABEL[t.decision_mode] ?? t.decision_mode : "Not recorded"}</b>
        </div>
        <div>
          <span className="tile-label">Model</span>
          <b className="tile-value">{t.champion_model_type ?? "—"}</b>
        </div>
        <div>
          <span className="tile-label">Strategy</span>
          <b className="tile-value">{t.strategy_used ? t.strategy_used.replace(/_/g, " ") : "—"}</b>
        </div>
        <div>
          <span className="tile-label">Confidence</span>
          <b className="tile-value">
            {typeof t.confidence === "number" ? `${t.confidence.toFixed(1)}%` : "—"}
            {typeof t.required_confidence === "number" ? ` (needs ${t.required_confidence.toFixed(1)}%)` : ""}
          </b>
        </div>
        <div>
          <span className="tile-label">Market Regime</span>
          <b className="tile-value">{t.regime ? t.regime.replace(/_/g, " ") : "—"}</b>
        </div>
        <div>
          <span className="tile-label">Timeframe</span>
          <b className="tile-value">{t.timeframe ?? "—"}</b>
        </div>
        <div>
          <span className="tile-label">Risk Reason</span>
          <b className="tile-value">{t.risk_reason ?? "—"}</b>
        </div>
        <div>
          <span className="tile-label">Why Trade Was Closed</span>
          <b className="tile-value">
            {t.close_reason ? t.close_reason.replace(/_/g, " ") : t.status === "OPEN" ? "Still open" : "—"}
          </b>
        </div>
        <div>
          <span className="tile-label">Leverage</span>
          <b className="tile-value">{t.leverage != null ? `${t.leverage}x` : "—"}</b>
        </div>
        <div>
          <span className="tile-label">Margin Used</span>
          <b className="tile-value">{t.margin_used != null ? fmtUsd(t.margin_used) : "—"}</b>
        </div>
        <div>
          <span className="tile-label">Notional</span>
          <b className="tile-value">{t.notional_value != null ? fmtUsd(t.notional_value) : "—"}</b>
        </div>
        <div>
          <span className="tile-label">Take Profit (last set)</span>
          <b className="tile-value">
            <span className={t.tp != null ? "green" : ""}>{t.tp != null ? fmtNum(t.tp) : "—"}</span>
          </b>
        </div>
        <div>
          <span className="tile-label">Stop Loss (last set)</span>
          <b className="tile-value">
            <span className={t.sl != null ? "red" : ""}>{t.sl != null ? fmtNum(t.sl) : "—"}</span>
          </b>
        </div>
        <div>
          <span className="tile-label">Est. Liquidation</span>
          <b className="tile-value orange">
            {t.liquidation_price != null ? fmtNum(t.liquidation_price) : t.leverage === 1 ? "None at 1x" : "—"}
          </b>
        </div>
        <div>
          <span className="tile-label">Realized PnL</span>
          <b className={`tile-value ${toneClass(toneOf(t.realized_pnl ?? t.pnl))}`}>
            {fmtUsd(t.realized_pnl ?? (t.status === "CLOSED" ? t.pnl : null))}
          </b>
        </div>
      </div>
      <div className="journal-detail-reasons">
        <span className="tile-label">Why Trade Was Opened</span>
        {Array.isArray(t.decision_reasons) && t.decision_reasons.length > 0 ? (
          <ul>
            {t.decision_reasons.map((r: string, i: number) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : (
          <p className="regime-desc">
            {t.decision_mode === "manual"
              ? "Opened manually from the UI/API - no automated decision context."
              : "No decision context recorded for this trade (opened before decision tracking existed)."}
          </p>
        )}
      </div>
    </div>
  );
}

const JOURNAL_COLUMNS: AutoCardColumn<any>[] = [
  { key: "symbol", label: "Symbol", render: (t) => <b>{t.symbol}</b> },
  { key: "side", label: "Side", render: (t) => <span className={t.side === "LONG" ? "green" : "red"}>{t.side}</span> },
  { key: "status", label: "Status", render: (t) => t.status },
  { key: "entry", label: "Entry", render: (t) => t.entry ?? "—" },
  { key: "exit", label: "Exit", render: (t) => t.exit ?? "—" },
  { key: "pnl", label: "PnL", render: (t) => <span className={toneClass(toneOf(t.pnl))}>{fmtUsd(t.pnl)}</span> },
  {
    key: "engine",
    label: "Engine",
    render: (t) => (
      <span className={t.decision_mode === "champion_ml" ? "green" : ""}>
        {t.decision_mode ? ENGINE_LABEL[t.decision_mode] ?? t.decision_mode : "—"}
      </span>
    ),
  },
  { key: "confidence", label: "Confidence", render: (t) => (typeof t.confidence === "number" ? `${t.confidence.toFixed(0)}%` : "—") },
  { key: "opened", label: "Opened Time", render: (t) => fmtLocalDateTime(t.opened_at) },
  { key: "closed", label: "Closed Time", render: (t) => (t.closed_at ? fmtLocalDateTime(t.closed_at) : "Open") },
  { key: "duration", label: "Duration", render: (t) => fmtTradeDuration(t.opened_at, t.closed_at) },
];

export default function PositionsPage(props: Props) {
  const { positions, history, symbol, prediction, botStatus, executionStatus, showToast } = props;

  const [tab, setTab] = useState<PaperLiveTab>("paper");
  const [symbolFilter, setSymbolFilter] = useState<SymbolFilter>("ALL");
  const [showResetModal, setShowResetModal] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [leverage, setLeverage] = useState(1);
  const [editingPosition, setEditingPosition] = useState<Position | null>(null);
  const [editingBinance, setEditingBinance] = useState<any>(null);

  const { status: liveStatus } = useTradingStatus();
  const isLive = liveStatus?.active_mode === "BINANCE_LIVE";
  const {
    positions: binancePositions,
    positionRows: binancePositionRows,
    busy: binanceBusy,
    confirm: binanceConfirm,
    setConfirm: setBinanceConfirm,
    run: runBinance,
    saveRisk: saveBinanceRisk,
  } = useBinanceAccount(showToast);
  const gate = useBinanceLiveGate(liveStatus);
  const [showCloseAll, setShowCloseAll] = useState(false);

  const filteredPositions =
    symbolFilter === "ALL" ? positions : positions.filter((p) => p.symbol === symbolFilter);
  const filteredHistory =
    symbolFilter === "ALL" ? history : history.filter((t) => t.symbol === symbolFilter);

  const botStatusLabel = (botStatus?.status || "loading").toUpperCase();
  const botRunning = botStatusLabel === "RUNNING";
  const riskGate = computeRiskGate(prediction, positions.length, executionStatus);

  const handleConfirmReset = async () => {
    setResetting(true);
    try {
      await props.resetPaperTrading();
    } finally {
      setResetting(false);
      setShowResetModal(false);
    }
  };

  const handleSaveBinanceRisk = async (id: number, patch: RiskPatch) => {
    await saveBinanceRisk(id, { stop_loss: patch.stop_loss, take_profit: patch.take_profit });
  };

  const handlePartialClose = (p: any, quantity: number) =>
    runBinance(
      () => api.binanceClosePosition({ symbol: p.symbol, position_id: p.id, quantity }),
      `${p.symbol} partially closed on Binance`
    );

  const handleCancelProtective = (p: any, kind: "sl" | "tp") => {
    const orderId = kind === "sl" ? p.sl_order_id : p.tp_order_id;
    if (orderId == null) return;
    return runBinance(
      () => api.binanceCancelOrder({ symbol: p.symbol, orderId }),
      `${kind.toUpperCase()} order canceled`
    );
  };

  const handleSyncBinance = () => runBinance(() => api.tradingSync(), "Re-synced from Binance");

  const handleCloseAllPositions = async () => {
    setShowCloseAll(false);
    let ok = 0;
    let failed = 0;
    await runBinance(async () => {
      for (const p of binancePositionRows) {
        try {
          await api.binanceClosePosition({ symbol: p.symbol, position_id: p.id });
          ok += 1;
        } catch {
          failed += 1;
        }
      }
      if (failed > 0) throw new Error(`Closed ${ok}, ${failed} failed — check Open Positions`);
    }, `Closed ${ok} Binance position${ok === 1 ? "" : "s"}`);
  };

  return (
    <div className="page-grid">
      <Card
        title="Positions"
        full
        right={<PaperLiveTabs active={tab} onChange={setTab} binanceLabel={`Binance Live Trading (${binancePositionRows.length})`} />}
      >
        <p className="regime-desc">
          {tab === "paper"
            ? "Simulated positions the paper engine is tracking - TP/SL and liquidation estimates are modeled, not read from an exchange."
            : "Real open positions on Binance Futures, read live from the account. Edit/close actions only work while Binance Real Money mode is active."}
        </p>
      </Card>

      {tab === "binance" ? (
        <>
          <BinancePositionsTable
            title={`Real Open Positions (${binancePositionRows.length})`}
            full
            className={isLive ? "live-danger-card" : ""}
            positionRows={binancePositionRows}
            busy={binanceBusy}
            gate={gate}
            unavailable={binancePositions?.available === false}
            unavailableReason={binancePositions?.reason}
            stale={binancePositions?.stale}
            retryAfterSeconds={binancePositions?.retry_after_seconds}
            showToast={showToast}
            onEdit={setEditingBinance}
            onRequestClose={(p: any) =>
              setBinanceConfirm({
                title: `Close REAL ${p.symbol} ${p.side}?`,
                body: "Sends a real reduce-only MARKET order to Binance. This uses real funds.",
                action: () =>
                  runBinance(
                    () => api.binanceClosePosition({ symbol: p.symbol, position_id: p.id }),
                    `${p.symbol} position closed on Binance`
                  ),
              })
            }
            onPartialClose={handlePartialClose}
            onCancelProtective={handleCancelProtective}
            onSync={handleSyncBinance}
          />

          {binancePositionRows.length > 0 && (
            <Card title="Danger Zone">
              <p className="regime-desc">Close every open Binance position at once. This uses real funds.</p>
              <div className="controls" style={{ marginTop: 12 }}>
                <button
                  className="btn-danger"
                  disabled={binanceBusy || !gate.canClosePositions}
                  title={!gate.canClosePositions ? gate.disabledReason ?? "" : ""}
                  onClick={() => setShowCloseAll(true)}
                >
                  Close All Binance Positions
                </button>
              </div>
            </Card>
          )}

          {showCloseAll && (
            <CloseAllPositionsModal
              positionCount={binancePositionRows.length}
              busy={binanceBusy}
              onClose={() => setShowCloseAll(false)}
              onConfirm={handleCloseAllPositions}
            />
          )}
        </>
      ) : (
      <>
      <Card title="Open a Paper Trade" wide>
        <p className="regime-desc">
          Opens a simulated {symbol} position using the paper trading engine ($1,000 notional
          {leverage > 1 ? `, $${(1000 / leverage).toFixed(0)} margin at ${leverage}x` : ""}).
        </p>
        <div className="controls" style={{ marginTop: 14 }}>
          <label className="filter-select-wrap">
            <span className="tile-label">Leverage</span>
            <select className="filter-select" value={leverage} onChange={(e) => setLeverage(Number(e.target.value))}>
              {[1, 2, 3, 5, 10, 20].map((l) => (
                <option key={l} value={l}>
                  {l}x
                </option>
              ))}
            </select>
          </label>
          <button className="btn-long" onClick={() => props.openPaperTrade("LONG", leverage)}>
            Open Long
          </button>
          <button className="btn-short" onClick={() => props.openPaperTrade("SHORT", leverage)}>
            Open Short
          </button>
          <button className="btn-danger" onClick={() => setShowResetModal(true)}>
            Reset Paper Trading
          </button>
        </div>
      </Card>

      <Card title="Positions Summary">
        <div className="kv-grid">
          <div>
            <span className="tile-label">Open Positions</span>
            <b
              className={`tile-value ${
                typeof props.portfolio?.max_open_positions === "number" &&
                positions.length >= props.portfolio.max_open_positions
                  ? "yellow"
                  : ""
              }`}
            >
              {typeof props.portfolio?.max_open_positions === "number"
                ? `${positions.length} of ${props.portfolio.max_open_positions}`
                : positions.length}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">Closed Trades</span>
            <b className="tile-value">{history.filter((t) => t.status === "CLOSED").length}</b>
          </div>
          <div>
            <span className="tile-label">Total Margin Used</span>
            <b className="tile-value">{fmtUsd(props.portfolio?.total_margin_used)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Available Margin</span>
            <b className="tile-value">{fmtUsd(props.portfolio?.available_margin)}</b>
          </div>
        </div>
        <div className="controls" style={{ marginTop: 14 }}>
          <label className="filter-select-wrap">
            <span className="tile-label">Symbol</span>
            <select
              className="filter-select"
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value as SymbolFilter)}
            >
              {SYMBOL_FILTERS.map((s) => (
                <option key={s} value={s}>
                  {s === "ALL" ? "All Symbols" : s}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {showResetModal && (
        <div className="modal-overlay" onClick={() => !resetting && setShowResetModal(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>Reset Paper Trading</h3>
            <p>{RESET_CONFIRM_TEXT}</p>
            <div className="modal-actions">
              <button className="mini-btn" onClick={() => setShowResetModal(false)} disabled={resetting}>
                Cancel
              </button>
              <button className="btn-danger" onClick={handleConfirmReset} disabled={resetting}>
                {resetting ? "Resetting…" : "Confirm Reset"}
              </button>
            </div>
          </div>
        </div>
      )}

      <Card title="Bot Trading Status" full>
        <div className="kv-grid">
          <div>
            <span className="tile-label">Bot</span>
            <b className={`tile-value ${botRunning ? "green" : botStatusLabel === "PAUSED" ? "yellow" : "red"}`}>
              {botStatusLabel}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">Open Positions</span>
            <b className="tile-value">{positions.length}</b>
          </div>
          <div>
            <span className="tile-label">Prediction Direction</span>
            <b
              className={`tile-value ${
                prediction?.direction === "LONG" ? "green" : prediction?.direction === "SHORT" ? "red" : ""
              }`}
            >
              {prediction?.direction ?? "—"}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">Confidence</span>
            <b className="tile-value">{typeof prediction?.confidence === "number" ? `${prediction.confidence}%` : "—"}</b>
          </div>
          <div>
            <span className="tile-label">Risk</span>
            <b className={`tile-value ${riskGate.allowed ? "green" : "red"}`}>
              {riskGate.allowed ? "ALLOWED" : "BLOCKED"}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">Last Prediction</span>
            <b className="tile-value">{fmtLocalDateTime(prediction?.computed_at)}</b>
          </div>
          <div>
            <span className="tile-label">Risk Reason</span>
            <b className="tile-value">{riskGate.reason}</b>
          </div>
        </div>

        {!riskGate.allowed && (
          <p className={`status-note ${botRunning ? "status-note-warn" : ""}`}>
            {botRunning
              ? `Bot is running but waiting because: ${riskGate.reason}`
              : `Bot is ${botStatusLabel.toLowerCase()} — no trades will be taken until it is started.`}
          </p>
        )}
      </Card>

      <Card title="Open Positions" full>
        <OpenPositionsCard
          positions={filteredPositions}
          onClose={props.closePaperTrade}
          onEditRisk={setEditingPosition}
          onViewAll={props.navigate}
          compact={false}
        />
      </Card>

      {editingPosition && (
        <EditRiskModal
          position={editingPosition}
          onSave={props.updatePositionRisk}
          onClose={() => setEditingPosition(null)}
        />
      )}

      <Card title="Trade Journal" full>
        <AutoCardTable
          columns={JOURNAL_COLUMNS}
          rows={filteredHistory.slice(0, 30)}
          keyField={(t) => t.id}
          titleColumn="symbol"
          statusColumn="status"
          renderDetail={(t) => <JournalDetail t={t} />}
          emptyMessage="No trade history yet."
        />
      </Card>
      </>
      )}

      {editingBinance && (
        <EditRiskModal position={editingBinance} onSave={handleSaveBinanceRisk} onClose={() => setEditingBinance(null)} />
      )}

      {binanceConfirm && (
        <div className="modal-overlay" onClick={() => !binanceBusy && setBinanceConfirm(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>{binanceConfirm.title}</h3>
            <p className="regime-desc">{binanceConfirm.body}</p>
            <div className="modal-actions">
              <button className="mini-btn" disabled={binanceBusy} onClick={() => setBinanceConfirm(null)}>
                Cancel
              </button>
              <button className="btn-danger" disabled={binanceBusy} onClick={() => binanceConfirm.action()}>
                {binanceBusy ? "Working…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
