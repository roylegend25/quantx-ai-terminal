import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import Card from "../components/Layout/Card";
import EditRiskModal, { type RiskPatch } from "../components/Dashboard/EditRiskModal";
import AutoCardTable from "../components/Responsive/AutoCardTable";
import BinancePositionsTable from "../components/Trading/BinancePositionsTable";
import ServerTradingControlCard from "../components/Trading/ServerTradingControlCard";
import UserLiveConfirmationCard from "../components/Trading/UserLiveConfirmationCard";
import ExecutionModeCard from "../components/Trading/ExecutionModeCard";
import { useBinanceLiveGate } from "../components/Trading/BinanceLiveGate";
import { api } from "../services/api";
import { fmtLocalDateTime, fmtNum, fmtPct, fmtUsd, toneClass, toneOf } from "../lib/format";
import { useTradingStatus, type TradingStatus } from "../components/Trading/TradingShared";
import { useBinanceAccount } from "../hooks/useBinanceAccount";
import type { AppData } from "../hooks/useAppData";

const POLL_MS = 10000;

/** Dynamic status copy for the Binance Real page. This page never shows a
 *  Paper/Binance toggle - it always represents Binance Real Money, so the
 *  copy walks the visitor through the three-step readiness sequence
 *  (server lock -> user live confirmation -> active-mode switch) instead
 *  of referencing a mode switch that no longer lives on this page. */
function statusCopy(status: TradingStatus | null): string {
  if (!status) return "Loading Binance status…";
  if (!status.binance_live_enabled_by_server) {
    return "Real Binance trading is locked by the server. Use Server Trading Control to enable the server lock first.";
  }
  if (!status.binance_live_unlocked_by_user) {
    return "Server live trading is enabled. Complete User Live Confirmation before real orders can be placed.";
  }
  if (status.active_mode !== "BINANCE_LIVE") {
    return "User live confirmation is complete. Switch active execution mode to Binance Live before the bot can place real orders.";
  }
  return "Binance Live Trading is active. Real orders may be placed only when the risk gate approves.";
}

/** Binance Real Money Terminal (Phase 23/24). Binance is the source of
 *  truth: everything shown here is read live from the real account through
 *  the backend's read-only client. Trading actions (close, cancel, TP/SL)
 *  are enabled only while the mode is BINANCE_LIVE - otherwise the account
 *  is view-only. Enabling real trading requires two independent gates:
 *  the admin-only Server Trading Control below (server env lock) and the
 *  per-session live-risk confirmation reached via the mode switch. */
export default function BinanceRealPage(props: AppData) {
  const { showToast } = props;
  const { status, reload } = useTradingStatus(POLL_MS);
  const {
    summary,
    balances,
    positions,
    orders,
    trades,
    income,
    balanceRows,
    positionRows,
    orderRows,
    tradeRows,
    incomeRows,
    busy,
    confirm,
    setConfirm,
    run,
    saveRisk,
    reload: reloadAccount,
  } = useBinanceAccount(showToast);

  const [editing, setEditing] = useState<any>(null);
  const [readiness, setReadiness] = useState<any>(null);
  const [cancelingKey, setCancelingKey] = useState<string | number | null>(null);

  const mode = status?.active_mode || "PAPER";
  const isLive = mode === "BINANCE_LIVE";
  const available = summary?.available;
  const gate = useBinanceLiveGate(status, { balanceAvailable: available ? summary?.available_balance > 0 : undefined });

  const loadReadiness = () => api.liveReadiness().then((r: any) => setReadiness(r)).catch(() => {});

  const onChanged = async () => {
    await Promise.all([reload(), reloadAccount(), loadReadiness()]);
  };

  useEffect(() => {
    let cancelled = false;
    const load = () => api.liveReadiness().then((r: any) => !cancelled && setReadiness(r)).catch(() => {});
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const handleSaveRisk = async (id: number, patch: RiskPatch) => {
    await saveRisk(id, { stop_loss: patch.stop_loss, take_profit: patch.take_profit });
    await onChanged();
  };

  const handleSync = async () => {
    await api.tradingSync().catch(() => null);
    await onChanged();
    showToast("Re-synced from Binance", "success");
  };

  const handlePartialClose = (p: any, quantity: number) =>
    run(
      () => api.binanceClosePosition({ symbol: p.symbol, position_id: p.id, quantity }),
      `${p.symbol} partially closed on Binance`
    );

  const handleCancelProtective = (p: any, kind: "sl" | "tp") => {
    const orderId = kind === "sl" ? p.sl_order_id : p.tp_order_id;
    if (orderId == null) return;
    // sl_order_id/tp_order_id is one generic id that may live on either
    // surface (protection_provider) - no `provider` sent here, the
    // backend infers classic vs algo from its own tracked ids.
    return run(() => api.binanceCancelOrder({ symbol: p.symbol, orderId }), `${kind.toUpperCase()} order canceled`);
  };

  return (
    <div className="page-grid">
      {/* ---------------- header ---------------- */}
      <Card
        full
        title="Binance Real Money Terminal"
        className={isLive ? "live-danger-card" : ""}
        right={
          <div className="controls">
            <span className={`badge ${isLive ? "badge-red" : status?.binance_live_enabled_by_server && status?.binance_live_unlocked_by_user ? "badge-orange" : ""}`}>
              Binance Real — {isLive ? "Active" : status?.binance_live_enabled_by_server && status?.binance_live_unlocked_by_user ? "Ready" : "Locked"}
            </span>
            <span className={`badge ${status?.binance_connected ? "badge-green" : ""}`}>
              {status?.binance_configured
                ? status?.binance_connected
                  ? "Binance API: Connected"
                  : "Binance API: Configured"
                : "Binance API: Not Configured"}
            </span>
            <span className={`badge ${isLive ? "badge-red" : ""}`}>Active Mode: {isLive ? "BINANCE_LIVE" : "PAPER"}</span>
            <span className={`badge ${status?.binance_live_enabled_by_server ? "badge-red" : ""}`}>
              Server Live Lock: {status?.binance_live_enabled_by_server ? "ON" : "OFF"}
            </span>
            <span className={`badge ${status?.binance_live_unlocked_by_user ? "badge-red" : ""}`}>
              User Live Unlock: {status?.binance_live_unlocked_by_user ? "Unlocked" : "Locked"}
            </span>
            <span className={`badge ${status?.kill_switch_active ? "badge-red" : ""}`}>
              Kill Switch: {status?.kill_switch_active ? "Active" : "Off"}
            </span>
          </div>
        }
      >
        <div className="portfolio-header-row">
          <div className="portfolio-banner-left">
            <p className="regime-desc">
              Complete the server lock, user live confirmation, and active-mode switch before real Binance orders can
              be placed.
            </p>
            <p className="regime-desc">{statusCopy(status)}</p>
          </div>
          <div className="controls">
            <button className="mini-btn" disabled={busy} onClick={handleSync}>
              <RefreshCw size={13} /> Sync
            </button>
          </div>
        </div>
      </Card>

      {/* ---------------- live readiness checklist ---------------- */}
      <Card title="Live Readiness Checklist" full className={readiness?.ok ? "live-danger-card" : ""}>
        {readiness ? (
          <>
            <div className="kv-grid">
              {readiness.steps.map((s: any) => (
                <div key={s.key}>
                  <span className="tile-label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {s.passed ? <CheckCircle2 size={13} className="green" /> : <XCircle size={13} className="red" />}
                    {s.label}
                  </span>
                  <b className={`tile-value ${s.passed ? "green" : "red"}`} style={{ fontSize: 13 }}>
                    {s.detail}
                  </b>
                </div>
              ))}
            </div>
            <p className="regime-desc" style={{ marginTop: 12 }}>
              {readiness.ok
                ? "All readiness checks pass — real orders may be placed if the per-trade risk gate approves."
                : `Blocked: ${readiness.blocked_reason}`}
            </p>
          </>
        ) : (
          <p className="regime-desc">Loading readiness checklist…</p>
        )}
      </Card>

      {/* ---------------- user live confirmation ---------------- */}
      <UserLiveConfirmationCard status={status} onChanged={onChanged} showToast={showToast} />

      {/* ---------------- execution mode ---------------- */}
      <ExecutionModeCard status={status} onChanged={onChanged} showToast={showToast} />

      {/* ---------------- server trading control (Phase 24) ---------------- */}
      <ServerTradingControlCard showToast={showToast} />

      {/* ---------------- account overview ---------------- */}
      <Card title="Real Account Overview" className={isLive ? "live-danger-card" : ""}>
        {available ? (
          <div className="kv-grid">
            <div>
              <span className="tile-label">Wallet Balance</span>
              <b className="tile-value">{fmtUsd(summary?.total_wallet_balance)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Available</span>
              <b className="tile-value">{fmtUsd(summary?.available_balance)}</b>
            </div>
            <div>
              <span className="tile-label">Margin Balance</span>
              <b className="tile-value">{fmtUsd(summary?.margin_balance)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Margin Used</span>
              <b className="tile-value">{fmtUsd(summary?.margin_used)}</b>
            </div>
            <div>
              <span className="tile-label">Free Margin</span>
              <b className="tile-value">{fmtUsd(summary?.free_margin)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Unrealized PnL</span>
              <b className={`tile-value ${toneClass(toneOf(summary?.unrealized_pnl))}`}>{fmtUsd(summary?.unrealized_pnl)}</b>
            </div>
            <div>
              <span className="tile-label">Daily Realized PnL</span>
              <b className={`tile-value ${toneClass(toneOf(summary?.daily_pnl))}`}>{fmtUsd(summary?.daily_pnl)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Notional Exposure</span>
              <b className="tile-value">{fmtUsd(summary?.total_notional_exposure)}</b>
            </div>
            <div>
              <span className="tile-label">Nearest Liq. Distance</span>
              <b className="tile-value orange">
                {summary?.nearest_liquidation_distance_pct != null ? fmtPct(summary.nearest_liquidation_distance_pct) : "—"}
              </b>
            </div>
            <div className="align-right">
              <span className="tile-label">Open Positions</span>
              <b className="tile-value">{summary?.open_positions ?? "—"}</b>
            </div>
          </div>
        ) : (
          <p className="regime-desc">
            <AlertTriangle size={13} /> Binance account unavailable{summary?.reason ? ` — ${summary.reason}` : ""}
          </p>
        )}
      </Card>

      {/* ---------------- risk limits ---------------- */}
      <Card title="Real Trading Limits">
        <div className="kv-grid">
          <div>
            <span className="tile-label">Allowed Symbols</span>
            <b className="tile-value" style={{ fontSize: 13 }}>{(status?.allowed_symbols || []).join(", ") || "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Max Leverage</span>
            <b className="tile-value">{status?.max_leverage != null ? `${status.max_leverage}x` : "—"}</b>
          </div>
          <div>
            <span className="tile-label">Max Notional / Trade</span>
            <b className="tile-value">{fmtUsd(status?.max_notional_per_trade)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Max Daily Loss</span>
            <b className="tile-value">{fmtUsd(status?.max_daily_loss_usdt)}</b>
          </div>
          <div>
            <span className="tile-label">Server Live Lock</span>
            <b className={`tile-value ${status?.binance_live_enabled_by_server ? "red" : "green"}`}>
              {status?.binance_live_enabled_by_server ? "OPEN" : "ENGAGED"}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">User Unlock</span>
            <b className={`tile-value ${status?.binance_live_unlocked_by_user ? "red" : "green"}`}>
              {status?.binance_live_unlocked_by_user ? "UNLOCKED" : "LOCKED"}
            </b>
          </div>
        </div>
      </Card>

      {/* ---------------- balances ---------------- */}
      <Card title="Asset Balances">
        <AutoCardTable
          columns={[
            { key: "asset", label: "Asset", render: (b: any) => <b>{b.asset}</b> },
            { key: "available", label: "Available", render: (b: any) => fmtNum(b.available, b.asset === "USDT" ? 2 : 6) },
            { key: "locked", label: "Locked", render: (b: any) => fmtNum(b.locked, b.asset === "USDT" ? 2 : 6) },
            { key: "total", label: "Total", render: (b: any) => fmtNum(b.total, b.asset === "USDT" ? 2 : 6) },
          ]}
          rows={balanceRows}
          keyField={(b: any) => b.asset}
          titleColumn="asset"
          emptyMessage={balances?.available === false ? balances?.reason || "Unavailable" : "No balances"}
        />
      </Card>

      {/* ---------------- positions ---------------- */}
      <BinancePositionsTable
        title={`Real Open Positions (${positionRows.length})`}
        full
        className={isLive ? "live-danger-card" : ""}
        positionRows={positionRows}
        busy={busy}
        gate={gate}
        unavailable={positions?.available === false}
        unavailableReason={positions?.reason}
        stale={positions?.stale}
        retryAfterSeconds={positions?.retry_after_seconds}
        showToast={showToast}
        onEdit={setEditing}
        onRequestClose={(p: any) =>
          setConfirm({
            title: `Close REAL ${p.symbol} ${p.side}?`,
            body: "Sends a real reduce-only MARKET order to Binance. This uses real funds.",
            action: () =>
              run(
                () => api.binanceClosePosition({ symbol: p.symbol, position_id: p.id }),
                `${p.symbol} position closed on Binance`
              ),
          })
        }
        onPartialClose={handlePartialClose}
        onCancelProtective={handleCancelProtective}
        onSync={handleSync}
      />

      {/* ---------------- open orders ---------------- */}
      <Card
        title={`Real Open Orders (${orderRows.length})`}
        wide
        right={
          gate.canCancelOrders && orderRows.length > 0 ? (
            <button
              className="mini-btn"
              disabled={busy}
              onClick={() =>
                setConfirm({
                  title: "Cancel ALL open Binance orders?",
                  body: "Every resting real order (including TP/SL protection) on allowed symbols will be canceled.",
                  action: () => run(() => api.binanceCancelAllOrders(), "All Binance orders canceled"),
                })
              }
            >
              Cancel All
            </button>
          ) : undefined
        }
      >
        <AutoCardTable
          columns={[
            { key: "symbol", label: "Symbol", render: (o: any) => <b>{o.symbol}</b> },
            { key: "side", label: "Side", render: (o: any) => <span className={o.side === "BUY" ? "green" : "red"}>{o.side}</span> },
            { key: "type", label: "Type", render: (o: any) => o.type },
            { key: "price", label: "Price", render: (o: any) => (o.stop_price ? fmtNum(o.stop_price) : o.price ? fmtNum(o.price) : "—") },
            { key: "qty", label: "Qty", render: (o: any) => (o.close_position ? "ALL" : fmtNum(o.quantity, 6)) },
            { key: "reduceOnly", label: "Reduce Only", render: (o: any) => (o.reduce_only || o.close_position ? "Yes" : "No") },
            { key: "status", label: "Status", render: (o: any) => o.status },
          ]}
          rows={orderRows}
          keyField={(o: any) => o.order_id}
          titleColumn="symbol"
          statusColumn="side"
          renderActions={(o: any) => {
            const rowKey = o.order_id ?? o.client_order_id ?? o.algo_id ?? o.client_algo_id;
            const missingId = rowKey == null;
            const disabledReason = missingId ? "Cannot cancel: missing order identifier" : gate.cancelDisabledReason ?? "";
            const isCanceling = cancelingKey !== null && cancelingKey === rowKey;
            return (
              <button
                className="mini-btn"
                disabled={busy || !gate.canCancelOrders || missingId}
                title={!gate.canCancelOrders || missingId ? disabledReason : ""}
                onClick={() =>
                  setConfirm({
                    title: `Cancel real order #${o.order_id ?? o.client_order_id}?`,
                    body: `${o.type} ${o.side} on ${o.symbol} will be canceled on Binance.`,
                    action: async () => {
                      setCancelingKey(rowKey);
                      try {
                        await run(
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
                        );
                      } finally {
                        setCancelingKey(null);
                      }
                    },
                  })
                }
              >
                {isCanceling ? "Canceling…" : "Cancel"}
              </button>
            );
          }}
          emptyMessage={orders?.available === false ? orders?.reason || "Unavailable" : "No open Binance orders"}
        />
      </Card>

      {/* ---------------- income ---------------- */}
      <Card title="Income / Fees / Funding">
        <AutoCardTable
          columns={[
            { key: "time", label: "Time", render: (r: any) => fmtLocalDateTime(r.time) },
            { key: "type", label: "Type", render: (r: any) => r.income_type },
            { key: "symbol", label: "Symbol", render: (r: any) => r.symbol || "—" },
            {
              key: "amount",
              label: "Amount",
              render: (r: any) => (
                <span className={toneClass(toneOf(r.income))}>
                  {fmtNum(r.income, 6)} {r.asset}
                </span>
              ),
            },
          ]}
          rows={incomeRows.slice(0, 15).map((r: any, i: number) => ({ ...r, _key: i }))}
          keyField={(r: any) => r._key}
          titleColumn="symbol"
          statusColumn="type"
          emptyMessage={income?.available === false ? income?.reason || "Unavailable" : "No income rows"}
        />
      </Card>

      {/* ---------------- trade history ---------------- */}
      <Card title="Real Trade History" full>
        <AutoCardTable
          columns={[
            { key: "time", label: "Time", render: (t: any) => fmtLocalDateTime(t.time) },
            {
              key: "label",
              label: "Label",
              render: (t: any) => (
                <span className={`badge ${t.label === "BOT_TRADE" ? "badge-green" : ""}`} style={{ fontSize: 10 }}>
                  {t.label === "BOT_TRADE" ? "BOT" : "SYNCED"}
                </span>
              ),
            },
            { key: "symbol", label: "Symbol", render: (t: any) => <b>{t.symbol}</b> },
            { key: "side", label: "Side", render: (t: any) => <span className={t.side === "BUY" ? "green" : "red"}>{t.side}</span> },
            { key: "price", label: "Price", render: (t: any) => fmtNum(t.price) },
            { key: "qty", label: "Qty", render: (t: any) => fmtNum(t.quantity, 6) },
            { key: "fee", label: "Fee", render: (t: any) => `${fmtNum(t.commission, 4)} ${t.commission_asset || ""}` },
            { key: "pnl", label: "Realized PnL", render: (t: any) => <span className={toneClass(toneOf(t.realized_pnl))}>{fmtUsd(t.realized_pnl)}</span> },
          ]}
          rows={tradeRows.slice(0, 30)}
          keyField={(t: any) => t.trade_id}
          titleColumn="symbol"
          statusColumn="side"
          emptyMessage={trades?.available === false ? trades?.reason || "Unavailable" : "No real trades yet"}
        />
      </Card>

      {/* ---------------- modals ---------------- */}
      {editing && <EditRiskModal position={editing} onSave={handleSaveRisk} onClose={() => setEditing(null)} />}

      {confirm && (
        <div className="modal-overlay" onClick={() => !busy && setConfirm(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>{confirm.title}</h3>
            <p className="regime-desc">{confirm.body}</p>
            <div className="modal-actions">
              <button className="mini-btn" disabled={busy} onClick={() => setConfirm(null)}>
                Cancel
              </button>
              <button className="btn-danger" disabled={busy} onClick={() => confirm.action()}>
                {busy ? "Working…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
