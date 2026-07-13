import { Fragment, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Download,
  RotateCw,
  ShieldAlert,
  Target,
  XCircle,
  Zap,
} from "lucide-react";
import Card from "../components/Layout/Card";
import { fmtNum, fmtPct } from "../lib/format";
import { downloadBlob } from "../lib/backtestStats";
import LocalTime from "../components/LocalTime";
import AutoCardTable, { type AutoCardColumn } from "../components/Responsive/AutoCardTable";
import PaperLiveTabs, { type PaperLiveTab } from "../components/Trading/PaperLiveTabs";
import { useTradingStatus } from "../components/Trading/TradingShared";
import { useBinanceLiveGate } from "../components/Trading/BinanceLiveGate";
import { useBinanceAccount } from "../hooks/useBinanceAccount";
import { BINANCE_TRADE_COLUMNS, BinanceTradeDetail } from "../lib/binanceTradeColumns";
import { api } from "../services/api";
import type { AppData } from "../hooks/useAppData";

const STAGE_LABEL: Record<string, string> = {
  intent: "Strategy Intent",
  risk_gate: "Risk Gate Decision",
  router: "Execution Router",
  exchange: "Exchange Order",
  audit: "Audit",
};

type Props = AppData;

const QUALITY_TONE: Record<string, string> = {
  EXCELLENT: "green",
  GOOD: "green",
  FAIR: "yellow",
  POOR: "red",
  PARTIAL: "yellow",
};

function QualityBadge({ quality }: { quality?: string | null }) {
  if (!quality) return <span className="badge">—</span>;
  const tone = QUALITY_TONE[quality] || "";
  return <span className={`badge ${tone === "green" ? "badge-green" : tone === "red" ? "badge-red" : ""}`}>{quality}</span>;
}

function StatusIcon({ status }: { status: string }) {
  if (status === "FILLED") return <CheckCircle2 size={14} className="green" />;
  if (status === "PARTIAL") return <AlertTriangle size={14} className="yellow" />;
  return <XCircle size={14} className="red" />;
}

const EXECUTION_COLUMNS: AutoCardColumn<any>[] = [
  { key: "time", label: "Time", render: (r) => <LocalTime value={r.recorded_at} label="Recorded" /> },
  { key: "symbol", label: "Symbol", render: (r) => <b>{r.symbol}</b> },
  { key: "side", label: "Side", render: (r) => <span className={r.side === "LONG" ? "green" : "red"}>{r.side}</span> },
  { key: "type", label: "Type", render: (r) => r.order_type },
  {
    key: "status",
    label: "Status",
    render: (r) => (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <StatusIcon status={r.status} /> {r.status}
      </span>
    ),
  },
  {
    key: "filled",
    label: "Filled",
    render: (r) => (r.filled_qty != null ? `${fmtNum(r.filled_qty, 4)}/${fmtNum(r.requested_qty, 4)}` : "—"),
  },
  { key: "fillPrice", label: "Fill Price", render: (r) => (r.avg_fill_price != null ? fmtNum(r.avg_fill_price, 2) : "—") },
  {
    key: "slippage",
    label: "Slippage",
    render: (r) => (r.actual_slippage_bps != null ? `${fmtNum(r.actual_slippage_bps, 2)} bps` : "—"),
  },
  { key: "latency", label: "Latency", render: (r) => `${fmtNum(r.latency_ms, 0)}ms` },
  { key: "retries", label: "Retries", render: (r) => r.retries },
  { key: "quality", label: "Quality", render: (r) => <QualityBadge quality={r.execution_quality} /> },
];

const BINANCE_ORDER_COLUMNS: AutoCardColumn<any>[] = [
  { key: "symbol", label: "Symbol", render: (o) => <b>{o.symbol}</b> },
  { key: "side", label: "Side", render: (o) => <span className={o.side === "BUY" ? "green" : "red"}>{o.side}</span> },
  { key: "type", label: "Type", render: (o) => o.type },
  { key: "price", label: "Price", render: (o) => (o.stop_price ? fmtNum(o.stop_price) : o.price ? fmtNum(o.price) : "—") },
  { key: "qty", label: "Qty", render: (o) => (o.close_position ? "ALL" : fmtNum(o.quantity, 6)) },
  { key: "status", label: "Status", render: (o) => o.status },
];

const AUDIT_COLUMNS: AutoCardColumn<any>[] = [
  { key: "time", label: "Time", render: (e) => <LocalTime value={e.created_at} label="Recorded" /> },
  { key: "event", label: "Event", render: (e) => e.event },
  { key: "mode", label: "Mode", render: (e) => e.mode || "—" },
  { key: "symbol", label: "Symbol", render: (e) => e.symbol || "—" },
  {
    key: "detail",
    label: "Detail",
    hideOnCard: true,
    render: (e) => <span className="mll-dim">{e.detail ? JSON.stringify(e.detail) : "—"}</span>,
  },
];

/** Execution Health / Safety Config / Last Execution / Performance metrics
 *  are read from the paper-only execution engine (this backend never
 *  tracks Binance orders through the same pipeline - see ExecutionRouter,
 *  a completely separate module). The Binance Live tab is built entirely
 *  from already-existing, already-tested endpoints instead of inventing
 *  new backend execution instrumentation: open orders, the bot's Binance
 *  trade journal, and the shared trading audit log. */
export default function ExecutionPage({ executionStatus, executionMetrics, showToast }: Props) {
  const breaker = executionStatus?.circuit_breaker;
  const safety = executionStatus?.safety;
  const m = executionMetrics;
  const recent: any[] = m?.recent_executions || [];
  const last = m?.last_execution;
  const quality = m?.quality_breakdown || {};

  const healthy = executionStatus?.engine === "operational" && !breaker?.open;

  const [tab, setTab] = useState<PaperLiveTab>("paper");
  const { status: liveStatus } = useTradingStatus();
  const gate = useBinanceLiveGate(liveStatus);
  const { orders, orderRows, reload: reloadOrders } = useBinanceAccount(showToast);
  const [binanceBotTrades, setBinanceBotTrades] = useState<any[]>([]);
  const [auditEvents, setAuditEvents] = useState<any[]>([]);
  const [executionLog, setExecutionLog] = useState<any>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [cancelingKey, setCancelingKey] = useState<string | number | null>(null);
  const [syncing, setSyncing] = useState(false);

  const loadExecutionLog = () => api.binanceExecutionLog(50).then((r: any) => setExecutionLog(r)).catch(() => {});

  const runAction = async (fn: () => Promise<any>, okMessage: string) => {
    setActionBusy(true);
    try {
      await fn();
      showToast(okMessage, "success");
    } catch (e: any) {
      showToast(e?.message || "Action failed", "error");
    } finally {
      setActionBusy(false);
      await Promise.all([reloadOrders(), loadExecutionLog()]);
    }
  };

  // Row-scoped wrapper (Debug 1.pdf section 6): shows "Canceling..." only
  // on the clicked row, and refreshes orders immediately from the
  // response instead of waiting on the normal poll cadence.
  const runRowCancel = async (rowKey: string | number, fn: () => Promise<any>, okMessage: string) => {
    setCancelingKey(rowKey);
    try {
      await runAction(fn, okMessage);
    } finally {
      setCancelingKey(null);
    }
  };

  const handleRetrySync = async () => {
    setSyncing(true);
    try {
      await api.tradingSync();
      showToast("Execution state re-synced", "success");
    } catch (e: any) {
      showToast(e?.message || "Sync failed", "error");
    } finally {
      setSyncing(false);
      await reloadOrders();
    }
  };

  const handleExportExecutionLog = () => {
    const events: any[] = executionLog?.events || [];
    const lines = ["time,stage,event,mode,symbol,detail"];
    for (const e of events) {
      const detail = e.detail ? JSON.stringify(e.detail).replace(/"/g, "'") : "";
      lines.push(`${e.created_at ?? ""},${e.stage},${e.event},${e.mode ?? ""},${e.symbol ?? ""},"${detail}"`);
    }
    downloadBlob(`binance_execution_log_${new Date().toISOString().slice(0, 10)}.csv`, lines.join("\n"), "text/csv");
  };

  useEffect(() => {
    api.botTradesBinance().then((res) => setBinanceBotTrades(res?.trades || [])).catch(() => {});
    api.portfolioAudit(50).then((res) => setAuditEvents(res?.events || [])).catch(() => {});
    loadExecutionLog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="page-grid">
      <Card title="Execution" full right={<PaperLiveTabs active={tab} onChange={setTab} />}>
        <p className="regime-desc">
          {tab === "paper"
            ? "Simulated fills from the paper execution engine - latency, slippage and rejection stats are modeled, not from a real exchange."
            : "Real Binance order/trade activity, read live from the account, plus the shared trading audit log."}
        </p>
        <div className="chip-row">
          <span className="chip">Strategy Intent</span>
          <ArrowRight size={13} className="exec-route-arrow" />
          <span className="chip">Risk Gate</span>
          <ArrowRight size={13} className="exec-route-arrow" />
          <span className="chip">Execution Router</span>
          <ArrowRight size={13} className="exec-route-arrow" />
          <span className="chip">{tab === "paper" ? "Paper Provider" : "Binance Live Provider"}</span>
          <ArrowRight size={13} className="exec-route-arrow" />
          <span className="chip">Order Result</span>
        </div>
      </Card>

      {tab === "binance" ? (
        <>
          <Card
            title="Binance Connection"
            wide
            right={
              <button className="mini-btn" disabled={syncing} onClick={handleRetrySync}>
                <RotateCw size={13} /> {syncing ? "Syncing…" : "Retry Sync"}
              </button>
            }
          >
            <div className="kv-grid">
              <div>
                <span className="tile-label">Execution Provider</span>
                <b className="tile-value">{liveStatus?.active_mode === "BINANCE_LIVE" ? "Binance Live" : "None (locked)"}</b>
              </div>
              <div className="align-right">
                <span className="tile-label">API Status</span>
                <b className="tile-value">{liveStatus?.binance_configured ? "Configured" : "Not Configured"}</b>
              </div>
              <div>
                <span className="tile-label">Connected</span>
                <b className={`tile-value ${liveStatus?.binance_connected ? "green" : "red"}`}>
                  {liveStatus?.binance_connected ? "Yes" : "No"}
                </b>
              </div>
              <div className="align-right">
                <span className="tile-label">Active Mode</span>
                <b className="tile-value">{liveStatus?.active_mode || "—"}</b>
              </div>
              <div>
                <span className="tile-label">Kill Switch</span>
                <b className={`tile-value ${liveStatus?.kill_switch_active ? "red" : "green"}`}>
                  {liveStatus?.kill_switch_active ? "ACTIVE" : "OFF"}
                </b>
              </div>
            </div>
            {(orders?.available === false || liveStatus?.reason) && (
              <p className="regime-desc" style={{ marginTop: 12 }}>
                <AlertTriangle size={13} /> {orders?.reason || liveStatus?.reason}
              </p>
            )}
          </Card>

          <Card
            title="Execution Pipeline"
            full
            right={
              <button className="mini-btn" onClick={handleExportExecutionLog}>
                <Download size={13} /> Export Log
              </button>
            }
          >
            <div className="chip-row" style={{ marginBottom: 14 }}>
              {["intent", "risk_gate", "router", "exchange"].map((stage, i, arr) => (
                <Fragment key={stage}>
                  <span className="chip">{STAGE_LABEL[stage]}</span>
                  {i < arr.length - 1 && <ArrowRight size={13} className="exec-route-arrow" />}
                </Fragment>
              ))}
            </div>
            {executionLog ? (
              <div className="kv-grid">
                {["intent", "risk_gate", "router", "exchange"].map((stage) => {
                  const entry = executionLog.last_by_stage?.[stage];
                  return (
                    <div key={stage}>
                      <span className="tile-label">Last {STAGE_LABEL[stage]}</span>
                      {entry ? (
                        <>
                          <b className="tile-value" style={{ fontSize: 13 }}>
                            {entry.symbol ? `${entry.symbol} — ` : ""}
                            {entry.event.replace(/_/g, " ")}
                          </b>
                          <span className="mll-dim" style={{ fontSize: 11 }}>
                            <LocalTime value={entry.created_at} label="Recorded" />
                            {entry.detail?.reason ? ` — ${entry.detail.reason}` : ""}
                          </span>
                        </>
                      ) : (
                        <b className="tile-value" style={{ fontSize: 13 }}>—</b>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="analytics-empty">No execution events recorded yet.</p>
            )}
            <p className="regime-desc" style={{ marginTop: 12 }}>
              Manual retries of a rejected order are intentionally not offered here — resubmit through normal bot/
              manual flow so the risk gate re-evaluates current conditions.
            </p>
          </Card>

          <Card title={`Real Open Orders (${orderRows.length})`} full
            right={
              orderRows.length > 0 ? (
                <button
                  className="mini-btn"
                  disabled={actionBusy || !gate.canCancelOrders}
                  title={!gate.canCancelOrders ? gate.cancelDisabledReason ?? "" : ""}
                  onClick={() => runAction(() => api.binanceCancelAllOrders(), "All Binance orders canceled")}
                >
                  Cancel All
                </button>
              ) : undefined
            }
          >
            <AutoCardTable
              columns={BINANCE_ORDER_COLUMNS}
              rows={orderRows}
              keyField={(o: any) => o.order_id}
              titleColumn="symbol"
              statusColumn="side"
              renderActions={(o: any) => {
                const rowKey = o.order_id ?? o.client_order_id ?? o.algo_id ?? o.client_algo_id;
                const missingId = rowKey == null;
                const isCanceling = cancelingKey !== null && cancelingKey === rowKey;
                return (
                  <button
                    className="mini-btn"
                    disabled={actionBusy || !gate.canCancelOrders || missingId}
                    title={
                      missingId
                        ? "Cannot cancel: missing order identifier"
                        : !gate.canCancelOrders
                          ? gate.cancelDisabledReason ?? ""
                          : "Cancel this order"
                    }
                    onClick={() =>
                      runRowCancel(
                        rowKey,
                        () =>
                          api.binanceCancelOrder({
                            symbol: o.symbol,
                            orderId: o.order_id,
                            clientOrderId: o.client_order_id,
                            algoId: o.algo_id,
                            clientAlgoId: o.client_algo_id,
                            provider: o.provider,
                          }),
                        "Order canceled"
                      )
                    }
                  >
                    {isCanceling ? "Canceling…" : "Cancel"}
                  </button>
                );
              }}
              emptyMessage={orders?.available === false ? orders?.reason || "Unavailable" : "No open Binance orders"}
            />
          </Card>

          <Card title="Recent Bot Fills" full>
            <AutoCardTable
              columns={BINANCE_TRADE_COLUMNS}
              rows={binanceBotTrades.slice(0, 30)}
              keyField={(t: any) => t.id}
              titleColumn="symbol"
              statusColumn="side"
              renderDetail={(t: any) => <BinanceTradeDetail t={t} />}
              emptyMessage="No Binance bot fills yet"
            />
          </Card>

          <Card title="Trading Audit Log" full>
            <AutoCardTable
              columns={AUDIT_COLUMNS}
              rows={auditEvents}
              keyField={(e: any) => e.id}
              titleColumn="event"
              statusColumn="mode"
              emptyMessage="No audit events recorded yet"
            />
          </Card>
        </>
      ) : (
      <>
      <Card title="Execution Health" wide>
        <div className="analytics-grid">
          <div className="analytics-tile status-tile">
            <span className="tile-label">Engine</span>
            <b className={`tile-value ${healthy ? "green" : "red"}`} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {healthy ? <CheckCircle2 size={16} /> : <ShieldAlert size={16} />}
              {healthy ? "Healthy" : breaker?.open ? "Circuit Breaker Open" : "Unavailable"}
            </b>
          </div>
          <div className="analytics-tile status-tile">
            <span className="tile-label">Mode</span>
            <b className="tile-value">{(executionStatus?.mode || "paper").toUpperCase()}</b>
          </div>
          <div className="analytics-tile status-tile">
            <span className="tile-label">Circuit Breaker</span>
            <b className={`tile-value ${breaker?.open ? "red" : "green"}`}>
              {breaker ? (breaker.open ? "TRIPPED" : "CLOSED") : "—"}
            </b>
          </div>
          <div className="analytics-tile status-tile">
            <span className="tile-label">Consecutive Failures</span>
            <b className="tile-value">
              {breaker ? `${breaker.consecutive_failures} / ${breaker.threshold}` : "—"}
            </b>
          </div>
        </div>
      </Card>

      <Card title="Safety Configuration">
        <div className="kv-grid">
          <div>
            <span className="tile-label">Max Signal Age</span>
            <b className="tile-value">{safety?.max_signal_age_seconds != null ? `${safety.max_signal_age_seconds}s` : "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Duplicate Window</span>
            <b className="tile-value">{safety?.duplicate_window_seconds != null ? `${safety.duplicate_window_seconds}s` : "—"}</b>
          </div>
          <div>
            <span className="tile-label">Max Open Positions</span>
            <b className="tile-value">{safety?.max_open_positions ?? "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Max Risk / Trade</span>
            <b className="tile-value">{fmtPct(safety?.max_risk_per_trade_pct, 2)}</b>
          </div>
        </div>
      </Card>

      <Card title="Last Execution" right={<Clock size={16} className="green" />}>
        {!last ? (
          <p className="analytics-empty">No executions recorded yet.</p>
        ) : (
          <div className="kv-grid">
            <div>
              <span className="tile-label">Symbol / Side</span>
              <b className="tile-value">{last.symbol} {last.side}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Status</span>
              <b className="tile-value" style={{ display: "flex", alignItems: "center", gap: 6, justifyContent: "flex-end" }}>
                <StatusIcon status={last.status} /> {last.status}
              </b>
            </div>
            <div>
              <span className="tile-label">Fill Price</span>
              <b className="tile-value">{last.avg_fill_price != null ? fmtNum(last.avg_fill_price, 2) : "—"}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Quality</span>
              <b className="tile-value"><QualityBadge quality={last.execution_quality} /></b>
            </div>
            <div>
              <span className="tile-label">When</span>
              <b className="tile-value"><LocalTime value={last.recorded_at} label="Recorded" /></b>
            </div>
            <div className="align-right">
              <span className="tile-label">Reason</span>
              <b className="tile-value" style={{ fontWeight: 400, fontSize: 12 }}>{last.reason || "—"}</b>
            </div>
          </div>
        )}
      </Card>

      <Card title="Performance" full>
        <div className="analytics-grid">
          <div className="analytics-tile">
            <span className="tile-label">
              <Zap size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Avg Latency
            </span>
            <b className="tile-value">{m?.avg_latency_ms != null ? `${fmtNum(m.avg_latency_ms, 0)}ms` : "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">
              <Target size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Avg Slippage
            </span>
            <b className="tile-value">{m?.avg_slippage_bps != null ? `${fmtNum(m.avg_slippage_bps, 2)} bps` : "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Expected Slippage</span>
            <b className="tile-value">
              {m?.avg_expected_slippage_bps != null ? `${fmtNum(m.avg_expected_slippage_bps, 2)} bps` : "—"}
            </b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">
              <RotateCw size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
              Total Retries
            </span>
            <b className="tile-value">{m?.total_retries ?? "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Partial Fill Rate</span>
            <b className="tile-value">{fmtPct(m?.partial_fill_rate, 1)}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Successful Orders</span>
            <b className="tile-value green">{m?.successful_orders ?? "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Rejected Orders</span>
            <b className="tile-value red">{m?.rejected_orders ?? "—"}</b>
          </div>
          <div className="analytics-tile">
            <span className="tile-label">Total Orders</span>
            <b className="tile-value">{m?.total_orders ?? "—"}</b>
          </div>
        </div>

        {Object.keys(quality).length > 0 && (
          <div className="indicators-row">
            <span className="tile-label">Order Quality Breakdown</span>
            <div className="chip-row">
              {Object.entries(quality).map(([q, count]) => (
                <span className="chip" key={q}>
                  <Activity size={14} /> {q}: {count as number}
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>

      <Card title="Recent Executions" full>
        <AutoCardTable
          columns={EXECUTION_COLUMNS}
          rows={recent}
          keyField={(r) => r.order_id}
          titleColumn="symbol"
          statusColumn="status"
          emptyMessage="No executions recorded yet - the scheduler routes each paper trade through this engine."
        />
      </Card>
      </>
      )}
    </div>
  );
}
