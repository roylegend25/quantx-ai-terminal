import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Lock, RefreshCw } from "lucide-react";
import Card from "../components/Layout/Card";
import EditRiskModal, { type RiskPatch } from "../components/Dashboard/EditRiskModal";
import { api } from "../services/api";
import { fmtLocalDateTime, fmtNum, fmtPct, fmtUsd, toneClass, toneOf } from "../lib/format";
import { LiveUnlockModal, ModeBadge, useTradingStatus } from "../components/Trading/TradingShared";
import type { AppData } from "../hooks/useAppData";

const POLL_MS = 10000;

type ConfirmState = { title: string; body: string; action: () => Promise<void> } | null;

/** Binance Real Money Terminal (Phase 23). Binance is the source of truth:
 *  everything shown here is read live from the real account through the
 *  backend's read-only client. Trading actions (close, cancel, TP/SL) are
 *  enabled only while the mode is BINANCE_LIVE - otherwise the account is
 *  view-only and the unlock panel explains why. */
export default function BinanceRealPage(props: AppData) {
  const { showToast } = props;
  const { status, reload } = useTradingStatus(POLL_MS);

  const [summary, setSummary] = useState<any>(null);
  const [balances, setBalances] = useState<any>(null);
  const [positions, setPositions] = useState<any>(null);
  const [orders, setOrders] = useState<any>(null);
  const [trades, setTrades] = useState<any>(null);
  const [income, setIncome] = useState<any>(null);

  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmState>(null);
  const [editing, setEditing] = useState<any>(null);
  const [showUnlock, setShowUnlock] = useState(false);

  const load = useCallback(async () => {
    const [s, b, p, o, t, inc] = await Promise.all([
      api.binanceSummary().catch(() => null),
      api.binanceBalances().catch(() => null),
      api.binancePositions().catch(() => null),
      api.binanceOrders().catch(() => null),
      api.binanceTrades().catch(() => null),
      api.binanceIncome(30).catch(() => null),
    ]);
    if (s) setSummary(s);
    if (b) setBalances(b);
    if (p) setPositions(p);
    if (o) setOrders(o);
    if (t) setTrades(t);
    if (inc) setIncome(inc);
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const onChanged = useCallback(async () => {
    await Promise.all([reload(), load()]);
  }, [reload, load]);

  const run = useCallback(
    async (fn: () => Promise<any>, okMessage: string) => {
      setBusy(true);
      try {
        await fn();
        showToast(okMessage, "success");
      } catch (e: any) {
        showToast(e?.message || "Action failed", "error");
      } finally {
        setBusy(false);
        setConfirm(null);
        await onChanged();
      }
    },
    [showToast, onChanged]
  );

  const mode = status?.active_mode || "PAPER";
  const isLive = mode === "BINANCE_LIVE";
  const available = summary?.available;
  const positionRows = positions?.positions || [];
  const orderRows = orders?.orders || [];
  const tradeRows = trades?.trades || [];
  const incomeRows = income?.income || [];

  const saveRisk = async (id: number, patch: RiskPatch) => {
    await api.binanceUpdatePositionRisk(id, { stop_loss: patch.stop_loss, take_profit: patch.take_profit });
    showToast("Binance Live TP/SL orders updated", "success");
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
          <p className="regime-desc">
            {isLive
              ? "⚠ LIVE — the bot may place real orders and the actions below use real funds."
              : "View-only. Binance Real Money Trading is locked. Enable BINANCE_LIVE_ENABLED=true on the server and complete the risk acknowledgement to allow real orders."}
          </p>
          <div className="controls">
            <button className="mini-btn" disabled={busy} onClick={async () => { await api.tradingSync().catch(() => null); await onChanged(); showToast("Re-synced from Binance", "success"); }}>
              <RefreshCw size={13} /> Sync
            </button>
            {isLive ? (
              <button
                className="btn-danger"
                disabled={busy}
                onClick={() =>
                  setConfirm({
                    title: "Lock live trading?",
                    body: "Execution returns to PAPER immediately and the unlock ceremony re-arms.",
                    action: () => run(() => api.lockBinanceLive(), "Live trading locked — back to paper"),
                  })
                }
              >
                <Lock size={14} /> Lock Live Trading
              </button>
            ) : (
              <button className="btn-danger" disabled={busy || !status} onClick={() => setShowUnlock(true)}>
                Unlock Real Trading…
              </button>
            )}
          </div>
        </div>
      </Card>

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
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Asset</th>
                <th>Available</th>
                <th>Locked</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {(!balances?.balances || balances.balances.length === 0) && (
                <tr>
                  <td colSpan={4}>{balances?.available === false ? balances?.reason || "Unavailable" : "No balances"}</td>
                </tr>
              )}
              {(balances?.balances || []).map((b: any) => (
                <tr key={b.asset}>
                  <td><b>{b.asset}</b></td>
                  <td>{fmtNum(b.available, b.asset === "USDT" ? 2 : 6)}</td>
                  <td>{fmtNum(b.locked, b.asset === "USDT" ? 2 : 6)}</td>
                  <td>{fmtNum(b.total, b.asset === "USDT" ? 2 : 6)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ---------------- positions ---------------- */}
      <Card title={`Real Open Positions (${positionRows.length})`} full className={isLive ? "live-danger-card" : ""}>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Size</th>
                <th>Entry</th>
                <th>Mark</th>
                <th>Liquidation</th>
                <th>Margin</th>
                <th>Type</th>
                <th>Lev.</th>
                <th>uPnL</th>
                <th>Live TP</th>
                <th>Live SL</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {positionRows.length === 0 && (
                <tr>
                  <td colSpan={13}>
                    {positions?.available === false ? positions?.reason || "Unavailable" : "No open Binance positions"}
                  </td>
                </tr>
              )}
              {positionRows.map((p: any, i: number) => (
                <tr key={p.symbol + i}>
                  <td><b>{p.symbol}</b></td>
                  <td><span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span></td>
                  <td>{fmtNum(p.quantity, 6)}</td>
                  <td>{fmtNum(p.entry_price)}</td>
                  <td>{fmtNum(p.mark_price)}</td>
                  <td>{p.liquidation_price != null ? fmtNum(p.liquidation_price) : "—"}</td>
                  <td>{fmtUsd(p.margin_used)}</td>
                  <td>{p.margin_type || "—"}</td>
                  <td>{p.leverage != null ? `${p.leverage}x` : "—"}</td>
                  <td><span className={toneClass(toneOf(p.unrealized_pnl))}>{fmtUsd(p.unrealized_pnl)}</span></td>
                  <td>{p.tp != null ? fmtNum(p.tp) : "—"}</td>
                  <td>{p.sl != null ? fmtNum(p.sl) : "—"}</td>
                  <td>
                    <div className="controls" style={{ gap: 6 }}>
                      <button
                        className="mini-btn"
                        disabled={busy || !isLive || p.id == null}
                        title={!isLive ? "Locked — unlock real trading first" : p.id == null ? "Waiting for sync…" : "Edit Binance Live TP/SL"}
                        onClick={() =>
                          setEditing({
                            ...p,
                            entry: p.entry_price,
                            mark: p.mark_price,
                            qty: p.quantity,
                            trailing_stop: null,
                          })
                        }
                      >
                        TP/SL
                      </button>
                      <button
                        className="btn-danger mini"
                        disabled={busy || !isLive}
                        title={!isLive ? "Locked — unlock real trading first" : "Close on Binance"}
                        onClick={() =>
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
                      >
                        Close
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

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
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Type</th>
                <th>Price</th>
                <th>Qty</th>
                <th>Reduce Only</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {orderRows.length === 0 && (
                <tr>
                  <td colSpan={8}>{orders?.available === false ? orders?.reason || "Unavailable" : "No open Binance orders"}</td>
                </tr>
              )}
              {orderRows.map((o: any) => (
                <tr key={o.order_id}>
                  <td><b>{o.symbol}</b></td>
                  <td><span className={o.side === "BUY" ? "green" : "red"}>{o.side}</span></td>
                  <td>{o.type}</td>
                  <td>{o.stop_price ? fmtNum(o.stop_price) : o.price ? fmtNum(o.price) : "—"}</td>
                  <td>{o.close_position ? "ALL" : fmtNum(o.quantity, 6)}</td>
                  <td>{o.reduce_only || o.close_position ? "Yes" : "No"}</td>
                  <td>{o.status}</td>
                  <td>
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
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ---------------- income ---------------- */}
      <Card title="Income / Fees / Funding">
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Type</th>
                <th>Symbol</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {incomeRows.length === 0 && (
                <tr>
                  <td colSpan={4}>{income?.available === false ? income?.reason || "Unavailable" : "No income rows"}</td>
                </tr>
              )}
              {incomeRows.slice(0, 15).map((r: any, i: number) => (
                <tr key={i}>
                  <td>{fmtLocalDateTime(r.time)}</td>
                  <td>{r.income_type}</td>
                  <td>{r.symbol || "—"}</td>
                  <td><span className={toneClass(toneOf(r.income))}>{fmtNum(r.income, 6)} {r.asset}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ---------------- trade history ---------------- */}
      <Card title="Real Trade History" full>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Label</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Price</th>
                <th>Qty</th>
                <th>Fee</th>
                <th>Realized PnL</th>
              </tr>
            </thead>
            <tbody>
              {tradeRows.length === 0 && (
                <tr>
                  <td colSpan={8}>{trades?.available === false ? trades?.reason || "Unavailable" : "No real trades yet"}</td>
                </tr>
              )}
              {tradeRows.slice(0, 30).map((t: any) => (
                <tr key={t.trade_id}>
                  <td>{fmtLocalDateTime(t.time)}</td>
                  <td>
                    <span className={`badge ${t.label === "BOT_TRADE" ? "badge-green" : ""}`} style={{ fontSize: 10 }}>
                      {t.label === "BOT_TRADE" ? "BOT" : "SYNCED"}
                    </span>
                  </td>
                  <td><b>{t.symbol}</b></td>
                  <td><span className={t.side === "BUY" ? "green" : "red"}>{t.side}</span></td>
                  <td>{fmtNum(t.price)}</td>
                  <td>{fmtNum(t.quantity, 6)}</td>
                  <td>{fmtNum(t.commission, 4)} {t.commission_asset || ""}</td>
                  <td><span className={toneClass(toneOf(t.realized_pnl))}>{fmtUsd(t.realized_pnl)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ---------------- modals ---------------- */}
      {editing && <EditRiskModal position={editing} onSave={saveRisk} onClose={() => setEditing(null)} />}

      {showUnlock && status && (
        <LiveUnlockModal status={status} onClose={() => setShowUnlock(false)} onChanged={onChanged} showToast={showToast} />
      )}

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
