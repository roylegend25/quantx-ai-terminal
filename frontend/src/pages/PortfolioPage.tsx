import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, OctagonX, RefreshCw, ShieldCheck, Wallet } from "lucide-react";
import Card from "../components/Layout/Card";
import EditRiskModal, { type RiskPatch } from "../components/Dashboard/EditRiskModal";
import { api } from "../services/api";
import { fmtLocalDateTime, fmtNum, fmtPct, fmtUsd, toneClass, toneOf } from "../lib/format";
import type { AppData } from "../hooks/useAppData";

const POLL_MS = 10000;

const UNLOCK_PHRASE = "I UNDERSTAND LIVE TRADING RISK";

const UNLOCK_CHECKS: { key: string; label: string }[] = [
  { key: "withdrawal_permission_disabled", label: "I have disabled withdrawal permission on the API key" },
  { key: "ip_whitelisted", label: "I have IP-whitelisted my VM on the API key" },
  { key: "losses_possible_understood", label: "I understand real losses are possible" },
  { key: "tested_on_testnet", label: "I have tested this strategy on testnet" },
  { key: "risk_limits_accepted", label: "I accept the configured risk limits" },
];

const MODE_LABEL: Record<string, string> = {
  PAPER: "PAPER",
  BINANCE_TESTNET: "TESTNET",
  BINANCE_LIVE_LOCKED: "LIVE LOCKED",
  BINANCE_LIVE: "LIVE",
};

function ModeBadge({ mode }: { mode?: string }) {
  const label = MODE_LABEL[mode || "PAPER"] || mode || "…";
  const cls =
    mode === "BINANCE_LIVE" ? "badge badge-red" : mode === "BINANCE_TESTNET" ? "badge badge-green" : "badge";
  return <span className={cls}>{label}</span>;
}

type ConfirmState = { title: string; body: string; danger?: boolean; action: () => Promise<void> } | null;

export default function PortfolioPage(props: AppData) {
  const { showToast } = props;

  const [summary, setSummary] = useState<any>(null);
  const [balances, setBalances] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [trades, setTrades] = useState<any[]>([]);
  const [status, setStatus] = useState<any>(null);
  const [loadError, setLoadError] = useState("");

  const [confirm, setConfirm] = useState<ConfirmState>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<any>(null);

  // live unlock panel state
  const [unlockText, setUnlockText] = useState("");
  const [acks, setAcks] = useState<Record<string, boolean>>({});
  const [unlocking, setUnlocking] = useState(false);

  const mode: string = summary?.mode || status?.mode || "PAPER";
  const isReal = mode === "BINANCE_TESTNET" || mode === "BINANCE_LIVE";
  const isPaper = mode === "PAPER";
  const killSwitchOn = !!summary?.risk?.kill_switch_active;

  const load = useCallback(async () => {
    const [sum, bal, pos, ord, trd, st] = await Promise.all([
      api.portfolioSummary().catch((e: any) => ({ __error: e?.message })),
      api.portfolioBalances().catch(() => null),
      api.portfolioPositions().catch(() => null),
      api.portfolioOrders().catch(() => null),
      api.portfolioTrades().catch(() => null),
      api.tradingMode().catch(() => null),
    ]);
    if (sum && !sum.__error) {
      setSummary(sum);
      setLoadError("");
    } else if (sum?.__error) {
      setLoadError(sum.__error);
    }
    if (bal) setBalances(bal.balances || []);
    if (pos) setPositions(pos.positions || []);
    if (ord) setOrders(ord.orders || []);
    if (trd) setTrades(trd.trades || []);
    if (st) setStatus(st);
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

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
        await load();
      }
    },
    [showToast, load]
  );

  const switchMode = (target: "paper" | "testnet") =>
    run(
      () => (target === "paper" ? api.enablePaperMode() : api.enableTestnetMode()),
      target === "paper" ? "Paper mode enabled" : "Testnet mode enabled"
    );

  const handleUnlock = async () => {
    setUnlocking(true);
    try {
      await api.requestLiveUnlock(unlockText, acks);
      showToast("LIVE trading unlocked — real funds at risk", "success");
      setUnlockText("");
      setAcks({});
    } catch (e: any) {
      showToast(e?.message || "Unlock refused", "error");
    } finally {
      setUnlocking(false);
      await load();
    }
  };

  const closePosition = (p: any) => {
    const action = () =>
      isPaper
        ? props.closePaperTrade(p.id)
        : run(
            () => api.closeTradingPosition({ position_id: p.id, symbol: p.symbol, confirm: true }),
            `${p.symbol} position closed`
          );
    setConfirm({
      title: `Close ${p.symbol} ${p.side}?`,
      body: isPaper
        ? "Closes this paper position at the current mark price."
        : `Sends a real reduce-only MARKET order to Binance ${mode === "BINANCE_LIVE" ? "LIVE" : "testnet"}.`,
      danger: !isPaper,
      action: async () => {
        await action();
        setConfirm(null);
      },
    });
  };

  const saveRisk = async (id: number, patch: RiskPatch) => {
    if (isPaper) {
      await props.updatePositionRisk(id, patch);
    } else {
      await api.updateTradingPositionRisk(id, { stop_loss: patch.stop_loss, take_profit: patch.take_profit });
      showToast("TP/SL orders updated on Binance", "success");
      await load();
    }
  };

  const unlockReady =
    unlockText.trim() === UNLOCK_PHRASE && UNLOCK_CHECKS.every((c) => acks[c.key]);

  const totalExposure = summary?.total_notional_exposure;
  const risk = summary?.risk || {};

  const normalizedPositions = useMemo(
    () =>
      positions.map((p: any) => ({
        ...p,
        entry: p.entry ?? p.entry_price,
        mark: p.mark ?? p.mark_price,
        qty: p.qty ?? p.quantity,
        pnl: p.pnl ?? p.unrealized_pnl,
      })),
    [positions]
  );

  return (
    <div className="page-grid">
      {/* ---------------- header / mode + emergency controls ---------------- */}
      <Card
        full
        className={mode === "BINANCE_LIVE" ? "live-danger-card" : ""}
        title="Portfolio"
        right={
          <div className="controls">
            <ModeBadge mode={mode} />
            {killSwitchOn && <span className="badge badge-red">KILL SWITCH ACTIVE</span>}
          </div>
        }
      >
        <div className="portfolio-header-row">
          <p className="regime-desc">
            {isPaper && "Simulated paper account — no real orders are placed."}
            {mode === "BINANCE_TESTNET" && "Real orders against Binance Futures TESTNET (fake funds, real exchange)."}
            {mode === "BINANCE_LIVE_LOCKED" &&
              (status?.live_enabled
                ? "Live trading requested but LOCKED — complete the unlock confirmation below."
                : "Live trading is disabled by server configuration (BINANCE_LIVE_ENABLED=false).")}
            {mode === "BINANCE_LIVE" && "⚠ LIVE trading — real funds are at risk on every order."}
            {status && !status.binance_configured && !isPaper && " Binance API keys are not configured."}
          </p>
          <div className="controls">
            <button className="mini-btn" disabled={busy || isPaper} onClick={() => switchMode("paper")}>
              Paper Mode
            </button>
            <button
              className="mini-btn"
              disabled={busy || mode === "BINANCE_TESTNET" || !status?.binance_configured}
              title={!status?.binance_configured ? "Configure BINANCE_API_KEY / BINANCE_API_SECRET first" : ""}
              onClick={() => switchMode("testnet")}
            >
              Testnet Mode
            </button>
            <button
              className="mini-btn"
              disabled={busy}
              onClick={() => run(() => api.tradingSync(), "Positions re-synced")}
              title="Re-sync positions/orders from the exchange"
            >
              <RefreshCw size={13} /> Sync
            </button>
            {killSwitchOn ? (
              <button
                className="btn-long"
                disabled={busy}
                onClick={() =>
                  setConfirm({
                    title: "Deactivate kill switch?",
                    body: "Trading (paper and real, per current mode) will be allowed again.",
                    action: () => run(() => api.killSwitch(false), "Kill switch deactivated"),
                  })
                }
              >
                <ShieldCheck size={14} /> Re-enable Trading
              </button>
            ) : (
              <button
                className="btn-danger"
                disabled={busy}
                onClick={() =>
                  setConfirm({
                    title: "EMERGENCY STOP?",
                    body:
                      "Stops the bot, blocks ALL new trades (paper and real) and cancels resting exchange orders. Open positions are NOT auto-closed.",
                    danger: true,
                    action: () => run(() => api.killSwitch(true, "manual emergency stop"), "Kill switch ACTIVATED"),
                  })
                }
              >
                <OctagonX size={14} /> Emergency Stop
              </button>
            )}
          </div>
        </div>
        {loadError && <p className="risk-modal-error">Portfolio data unavailable: {loadError}</p>}
        {Array.isArray(status?.warnings) && status.warnings.length > 0 && (
          <div className="portfolio-warnings">
            {status.warnings.map((w: string) => (
              <p key={w}>
                <AlertTriangle size={13} /> {w}
              </p>
            ))}
          </div>
        )}
      </Card>

      {/* ---------------- A. account overview ---------------- */}
      <Card title="Account Overview" right={<Wallet size={16} />}>
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
            <span className="tile-label">Unrealized PnL</span>
            <b className={`tile-value ${toneClass(toneOf(summary?.unrealized_pnl))}`}>
              {fmtUsd(summary?.unrealized_pnl)}
            </b>
          </div>
          <div>
            <span className="tile-label">Daily PnL</span>
            <b className={`tile-value ${toneClass(toneOf(summary?.daily_pnl))}`}>{fmtUsd(summary?.daily_pnl)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Realized PnL</span>
            <b className={`tile-value ${toneClass(toneOf(summary?.realized_pnl))}`}>
              {summary?.realized_pnl != null ? fmtUsd(summary.realized_pnl) : "—"}
            </b>
          </div>
          <div>
            <span className="tile-label">Margin Used</span>
            <b className="tile-value">{fmtUsd(summary?.margin_used)}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Free Margin</span>
            <b className="tile-value">{fmtUsd(summary?.free_margin)}</b>
          </div>
          <div>
            <span className="tile-label">Open Positions</span>
            <b className="tile-value">{summary?.open_positions ?? "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Notional Exposure</span>
            <b className="tile-value">{fmtUsd(totalExposure)}</b>
          </div>
          <div>
            <span className="tile-label">Nearest Liq. Distance</span>
            <b className="tile-value orange">
              {summary?.nearest_liquidation_distance_pct != null
                ? fmtPct(summary.nearest_liquidation_distance_pct)
                : "—"}
            </b>
          </div>
          {isPaper && (
            <div className="align-right">
              <span className="tile-label">Win Rate</span>
              <b className="tile-value">{summary?.win_rate != null ? fmtPct(summary.win_rate) : "—"}</b>
            </div>
          )}
        </div>
      </Card>

      {/* ---------------- F. risk summary ---------------- */}
      <Card title="Risk Summary">
        <div className="kv-grid">
          <div>
            <span className="tile-label">Risk Mode</span>
            <b className="tile-value">{(risk.risk_mode || "paper").toUpperCase()}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Kill Switch</span>
            <b className={`tile-value ${killSwitchOn ? "red" : "green"}`}>{killSwitchOn ? "ACTIVE" : "OFF"}</b>
          </div>
          <div>
            <span className="tile-label">Max Daily Loss</span>
            <b className="tile-value">
              {isReal ? fmtUsd(risk.max_daily_loss_usdt) : fmtPct(risk.max_daily_loss_pct)}
            </b>
          </div>
          <div className="align-right">
            <span className="tile-label">Daily Loss Used</span>
            <b className={`tile-value ${toneClass(toneOf(summary?.daily_pnl))}`}>
              {summary?.daily_pnl != null && summary.daily_pnl < 0 ? fmtUsd(-summary.daily_pnl) : fmtUsd(0)}
            </b>
          </div>
          <div>
            <span className="tile-label">Max Open Positions</span>
            <b className="tile-value">{risk.max_open_positions ?? "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Max Notional / Trade</span>
            <b className="tile-value">{fmtUsd(risk.max_notional_per_trade)}</b>
          </div>
          <div>
            <span className="tile-label">Max Leverage</span>
            <b className="tile-value">{risk.max_leverage != null ? `${risk.max_leverage}x` : "—"}</b>
          </div>
          <div className="align-right">
            <span className="tile-label">Live Trading Lock</span>
            <b className={`tile-value ${risk.live_lock_status === "UNLOCKED" ? "red" : "green"}`}>
              {risk.live_lock_status === "UNLOCKED"
                ? "UNLOCKED"
                : risk.live_enabled
                  ? "LOCKED"
                  : "LOCKED (server)"}
            </b>
          </div>
          <div>
            <span className="tile-label">Allowed Symbols</span>
            <b className="tile-value" style={{ fontSize: 13 }}>
              {(risk.allowed_symbols || []).join(", ") || "—"}
            </b>
          </div>
        </div>
      </Card>

      {/* ---------------- B. asset balances ---------------- */}
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
              {balances.length === 0 && (
                <tr>
                  <td colSpan={4}>No balances</td>
                </tr>
              )}
              {balances.map((b: any) => (
                <tr key={b.asset}>
                  <td>
                    <b>{b.asset}</b>
                  </td>
                  <td>{fmtNum(b.available, b.asset === "USDT" ? 2 : 6)}</td>
                  <td>{fmtNum(b.locked, b.asset === "USDT" ? 2 : 6)}</td>
                  <td>{fmtNum(b.total, b.asset === "USDT" ? 2 : 6)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ---------------- C. open positions ---------------- */}
      <Card title={`Open Positions (${normalizedPositions.length})`} full>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Size</th>
                <th>Entry</th>
                <th>Mark</th>
                <th>Liq.</th>
                <th>Margin</th>
                <th>Lev.</th>
                <th>uPnL</th>
                <th>TP</th>
                <th>SL</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {normalizedPositions.length === 0 && (
                <tr>
                  <td colSpan={12}>No open positions</td>
                </tr>
              )}
              {normalizedPositions.map((p: any, i: number) => (
                <tr key={p.id ?? `${p.symbol}-${i}`}>
                  <td>
                    <b>{p.symbol}</b>
                  </td>
                  <td>
                    <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span>
                  </td>
                  <td>{fmtNum(p.qty, 6)}</td>
                  <td>{fmtNum(p.entry)}</td>
                  <td>{fmtNum(p.mark)}</td>
                  <td>{p.liquidation_price != null ? fmtNum(p.liquidation_price) : "—"}</td>
                  <td>{fmtUsd(p.margin_used)}</td>
                  <td>{p.leverage != null ? `${p.leverage}x` : "—"}</td>
                  <td>
                    <span className={toneClass(toneOf(p.pnl))}>{fmtUsd(p.pnl)}</span>
                  </td>
                  <td>{p.tp != null ? fmtNum(p.tp) : "—"}</td>
                  <td>{p.sl != null ? fmtNum(p.sl) : "—"}</td>
                  <td>
                    <div className="controls" style={{ gap: 6 }}>
                      <button
                        className="mini-btn"
                        disabled={busy || p.id == null}
                        title={p.id == null ? "Waiting for position sync…" : "Edit TP/SL"}
                        onClick={() => setEditing(p)}
                      >
                        TP/SL
                      </button>
                      <button className="btn-danger mini" disabled={busy} onClick={() => closePosition(p)}>
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

      {/* ---------------- D. open orders ---------------- */}
      <Card
        title={`Open Orders (${orders.length})`}
        wide
        right={
          isReal && orders.length > 0 ? (
            <button
              className="mini-btn"
              disabled={busy}
              onClick={() =>
                setConfirm({
                  title: "Cancel ALL open Binance orders?",
                  body: "Every resting order (including TP/SL protection) on allowed symbols will be canceled.",
                  danger: true,
                  action: () => run(() => api.cancelAllTradingOrders(), "All orders canceled"),
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
              {orders.length === 0 && (
                <tr>
                  <td colSpan={8}>{isPaper ? "Paper orders fill instantly — nothing resting" : "No open orders"}</td>
                </tr>
              )}
              {orders.map((o: any) => (
                <tr key={o.order_id}>
                  <td>
                    <b>{o.symbol}</b>
                  </td>
                  <td>
                    <span className={o.side === "BUY" ? "green" : "red"}>{o.side}</span>
                  </td>
                  <td>{o.type}</td>
                  <td>{o.stop_price ? fmtNum(o.stop_price) : o.price ? fmtNum(o.price) : "—"}</td>
                  <td>{o.close_position ? "ALL" : fmtNum(o.quantity, 6)}</td>
                  <td>{o.reduce_only || o.close_position ? "Yes" : "No"}</td>
                  <td>{o.status}</td>
                  <td>
                    <button
                      className="mini-btn"
                      disabled={busy}
                      onClick={() =>
                        setConfirm({
                          title: `Cancel order #${o.order_id}?`,
                          body: `${o.type} ${o.side} on ${o.symbol} will be canceled on the exchange.`,
                          action: () =>
                            run(() => api.cancelTradingOrder(o.symbol, o.order_id), "Order canceled"),
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

      {/* ---------------- E. trade history ---------------- */}
      <Card title="Trade History" full>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>{isReal ? "Price" : "Entry → Exit"}</th>
                <th>Qty</th>
                <th>{isReal ? "Fee" : "Strategy / Model"}</th>
                <th>Realized PnL</th>
              </tr>
            </thead>
            <tbody>
              {trades.length === 0 && (
                <tr>
                  <td colSpan={7}>No trades yet</td>
                </tr>
              )}
              {trades.slice(0, 30).map((t: any, i: number) => (
                <tr key={t.trade_id ?? t.id ?? i}>
                  <td>{fmtLocalDateTime(t.time ?? t.closed_at ?? t.opened_at)}</td>
                  <td>
                    <b>{t.symbol}</b>
                  </td>
                  <td>
                    <span className={["BUY", "LONG"].includes(t.side) ? "green" : "red"}>{t.side}</span>
                  </td>
                  <td>
                    {isReal
                      ? fmtNum(t.price)
                      : `${fmtNum(t.entry)} → ${t.exit != null ? fmtNum(t.exit) : "open"}`}
                  </td>
                  <td>{fmtNum(t.quantity ?? t.qty, 6)}</td>
                  <td>
                    {isReal
                      ? `${fmtNum(t.commission, 4)} ${t.commission_asset || ""}`
                      : t.strategy_used || t.champion_model_type || t.decision_mode || "—"}
                  </td>
                  <td>
                    <span className={toneClass(toneOf(t.realized_pnl ?? t.pnl))}>
                      {fmtUsd(t.realized_pnl ?? t.pnl)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ---------------- live trading unlock ---------------- */}
      <Card title="Live Trading Unlock" className={mode === "BINANCE_LIVE" ? "live-danger-card" : ""}>
        {mode === "BINANCE_LIVE" ? (
          <div>
            <p className="risk-modal-error">
              <AlertTriangle size={14} /> LIVE trading is UNLOCKED. Every confirmed order uses real funds.
            </p>
            <div className="controls" style={{ marginTop: 12 }}>
              <button className="btn-danger" disabled={busy} onClick={() => switchMode("testnet")}>
                Lock Live Trading (back to Testnet)
              </button>
            </div>
          </div>
        ) : !status?.live_enabled ? (
          <p className="regime-desc">
            Live trading is disabled by server configuration. Set <code>BINANCE_LIVE_ENABLED=true</code> (plus
            production API keys) in the backend environment to make unlocking possible. Start on testnet first.
          </p>
        ) : (
          <div className="live-unlock-panel">
            <p className="risk-modal-error">
              <AlertTriangle size={14} /> WARNING: unlocking enables REAL-MONEY orders on Binance Futures. Losses
              can exceed your expectations. Start on testnet.
            </p>
            {UNLOCK_CHECKS.map((c) => (
              <label key={c.key} className="live-unlock-check">
                <input
                  type="checkbox"
                  checked={!!acks[c.key]}
                  onChange={(e) => setAcks((prev) => ({ ...prev, [c.key]: e.target.checked }))}
                />
                <span>{c.label}</span>
              </label>
            ))}
            <label className="live-unlock-phrase">
              <span className="tile-label">
                Type <b>{UNLOCK_PHRASE}</b> to confirm
              </span>
              <input
                type="text"
                value={unlockText}
                onChange={(e) => setUnlockText(e.target.value)}
                placeholder={UNLOCK_PHRASE}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <div className="controls">
              <button className="btn-danger" disabled={!unlockReady || unlocking} onClick={handleUnlock}>
                {unlocking ? "Unlocking…" : "Unlock LIVE Trading"}
              </button>
            </div>
          </div>
        )}
      </Card>

      {/* ---------------- modals ---------------- */}
      {editing && (
        <EditRiskModal
          position={editing}
          onSave={saveRisk}
          onClose={() => setEditing(null)}
        />
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
              <button
                className={confirm.danger ? "btn-danger" : "btn-long"}
                disabled={busy}
                onClick={() => confirm.action()}
              >
                {busy ? "Working…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
