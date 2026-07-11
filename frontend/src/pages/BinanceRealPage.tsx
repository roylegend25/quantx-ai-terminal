import { useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import Card from "../components/Layout/Card";
import EditRiskModal, { type RiskPatch } from "../components/Dashboard/EditRiskModal";
import AutoCardTable from "../components/Responsive/AutoCardTable";
import BinancePositionsTable from "../components/Trading/BinancePositionsTable";
import ServerTradingControlCard from "../components/Trading/ServerTradingControlCard";
import { api } from "../services/api";
import { fmtLocalDateTime, fmtNum, fmtPct, fmtUsd, toneClass, toneOf } from "../lib/format";
import { ModeBadge, ModeToggle, useTradingStatus, type TradingStatus } from "../components/Trading/TradingShared";
import { useBinanceAccount } from "../hooks/useBinanceAccount";
import type { AppData } from "../hooks/useAppData";

const POLL_MS = 10000;

/** Dynamic status copy (task: "Binance page status copy") - replaces the
 *  old static ".env" instruction paragraph with UI-driven text reflecting
 *  the two independent gates: the admin server lock (BINANCE_LIVE_ENABLED,
 *  now controlled below via ServerTradingControlCard) and the per-session
 *  user live-risk confirmation (ModeToggle -> LiveUnlockModal). */
function statusCopy(status: TradingStatus | null): string {
  if (!status?.binance_live_enabled_by_server) {
    return "Real Binance trading is locked by the server. Use Server Trading Control below to enable the server lock, then complete the live-risk confirmation.";
  }
  if (!status.binance_live_unlocked_by_user) {
    return "Server live trading is enabled. Complete the live-risk confirmation before real orders can be placed.";
  }
  return "Binance Real Money Trading is active. Real orders may be placed if the risk gate approves.";
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

  const mode = status?.active_mode || "PAPER";
  const isLive = mode === "BINANCE_LIVE";
  const available = summary?.available;

  const onChanged = async () => {
    await Promise.all([reload(), reloadAccount()]);
  };

  const handleSaveRisk = async (id: number, patch: RiskPatch) => {
    await saveRisk(id, { stop_loss: patch.stop_loss, take_profit: patch.take_profit });
    await onChanged();
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
            <ModeBadge mode={mode === "PAPER" ? "BINANCE_LIVE_LOCKED" : mode} killSwitch={status?.kill_switch_active} />
            <span className={`badge ${status?.binance_connected ? "badge-green" : ""}`}>
              {status?.binance_configured
                ? status?.binance_connected
                  ? "Binance API: Connected"
                  : "Binance API: Configured"
                : "Binance API: Not Configured"}
            </span>
          </div>
        }
      >
        <div className="portfolio-header-row">
          <div className="portfolio-banner-left">
            <ModeToggle status={status} onChanged={onChanged} showToast={showToast} />
            <p className="regime-desc">{statusCopy(status)}</p>
          </div>
          <div className="controls">
            <button
              className="mini-btn"
              disabled={busy}
              onClick={async () => {
                await api.tradingSync().catch(() => null);
                await onChanged();
                showToast("Re-synced from Binance", "success");
              }}
            >
              <RefreshCw size={13} /> Sync
            </button>
          </div>
        </div>

        <ol className="binance-steps">
          <li>Server Trading Control below — enable the server live lock (admin).</li>
          <li>Switch the mode above to "Binance Real Money" — complete the live-risk confirmation.</li>
          <li>Real orders become possible once the risk gate approves each trade.</li>
        </ol>
      </Card>

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
        isLive={isLive}
        unavailable={positions?.available === false}
        unavailableReason={positions?.reason}
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
      />

      {/* ---------------- open orders ---------------- */}
      <Card
        title={`Real Open Orders (${orderRows.length})`}
        wide
        right={
          isLive && orderRows.length > 0 ? (
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
          renderActions={(o: any) => (
            <button
              className="mini-btn"
              disabled={busy || !isLive}
              title={!isLive ? "Locked — unlock real trading first" : ""}
              onClick={() =>
                setConfirm({
                  title: `Cancel real order #${o.order_id}?`,
                  body: `${o.type} ${o.side} on ${o.symbol} will be canceled on Binance.`,
                  action: () => run(() => api.binanceCancelOrder(o.symbol, o.order_id), "Order canceled"),
                })
              }
            >
              Cancel
            </button>
          )}
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
