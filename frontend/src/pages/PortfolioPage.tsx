import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, OctagonX, RefreshCw, ShieldCheck } from "lucide-react";
import Card from "../components/Layout/Card";
import AutoCardTable from "../components/Responsive/AutoCardTable";
import { api } from "../services/api";
import { fmtNum, fmtPct, fmtUsd, toneClass, toneOf } from "../lib/format";
import { ModeBadge, ModeToggle, useTradingStatus } from "../components/Trading/TradingShared";
import type { AppData } from "../hooks/useAppData";
import type { NavKey } from "../lib/nav";

const POLL_MS = 10000;

type Props = AppData & { navigate: (key: NavKey) => void };

/** Portfolio (Phase 23): the two trading spaces side by side - Paper on the
 *  left, Binance Real on the right - plus the execution-mode switch. The two
 *  accounts are fetched from separate endpoints and never merged. */
export default function PortfolioPage(props: Props) {
  const { showToast, navigate } = props;
  const { status, reload } = useTradingStatus(POLL_MS);

  const [paper, setPaper] = useState<any>(null);
  const [paperPositions, setPaperPositions] = useState<any[]>([]);
  const [binance, setBinance] = useState<any>(null);
  const [binancePositions, setBinancePositions] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [p, pp, b, bp] = await Promise.all([
      api.paperSummary().catch(() => null),
      api.paperPortfolioPositions().catch(() => null),
      api.binanceSummary().catch(() => null),
      api.binancePositions().catch(() => null),
    ]);
    if (p) setPaper(p);
    if (pp) setPaperPositions(pp.positions || []);
    if (b) setBinance(b);
    if (bp) setBinancePositions(bp);
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const onChanged = useCallback(async () => {
    await Promise.all([reload(), load()]);
  }, [reload, load]);

  const mode = status?.active_mode || "PAPER";
  const killSwitchOn = !!status?.kill_switch_active;

  const killSwitch = async (active: boolean) => {
    setBusy(true);
    try {
      await api.killSwitch(active, active ? "portfolio emergency stop" : undefined);
      showToast(active ? "Kill switch ACTIVATED — all trading halted" : "Kill switch deactivated", "success");
      await onChanged();
    } catch (e: any) {
      showToast(e?.message || "Kill switch action failed", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-grid">
      {/* ------------- active mode banner ------------- */}
      <Card
        full
        className={mode === "BINANCE_LIVE" ? "live-danger-card" : ""}
        title="Portfolio"
        right={
          <div className="controls">
            <ModeBadge mode={mode} killSwitch={killSwitchOn} />
          </div>
        }
      >
        <div className="portfolio-header-row">
          <div className="portfolio-banner-left">
            <ModeToggle status={status} onChanged={onChanged} showToast={showToast} />
            <p className="regime-desc">
              {mode === "PAPER" && "Bot executes simulated paper orders. The Binance Real account is view-only."}
              {mode === "BINANCE_LIVE_LOCKED" &&
                "Binance Real Money Trading is locked. Enable BINANCE_LIVE_ENABLED=true on the server and complete the risk acknowledgement to allow real orders."}
              {mode === "BINANCE_LIVE" && "⚠ Bot places REAL Binance Futures orders. Real funds are at risk."}
            </p>
          </div>
          <div className="controls">
            <button className="mini-btn" disabled={busy} onClick={async () => { await api.tradingSync().catch(() => null); await onChanged(); showToast("Re-synced", "success"); }}>
              <RefreshCw size={13} /> Sync
            </button>
            {killSwitchOn ? (
              <button className="btn-long" disabled={busy} onClick={() => killSwitch(false)}>
                <ShieldCheck size={14} /> Re-enable Trading
              </button>
            ) : (
              <button
                className="btn-danger"
                disabled={busy}
                onClick={() => {
                  if (window.confirm("EMERGENCY STOP?\n\nStops the bot and blocks ALL new paper and real trades. Open positions are NOT auto-closed.")) {
                    killSwitch(true);
                  }
                }}
              >
                <OctagonX size={14} /> Emergency Stop
              </button>
            )}
          </div>
        </div>
      </Card>

      {/* ------------- two separate terminals, side by side ------------- */}
      <div className="portfolio-compare">
        <Card
          title="Paper Trading Account"
          right={<span className="badge">PAPER</span>}
          className="portfolio-compare-card"
        >
          <div className="kv-grid">
            <div>
              <span className="tile-label">Balance</span>
              <b className="tile-value">{fmtUsd(paper?.balance)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Equity</span>
              <b className="tile-value">{fmtUsd(paper?.equity)}</b>
            </div>
            <div>
              <span className="tile-label">Available Margin</span>
              <b className="tile-value">{fmtUsd(paper?.available_margin)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Margin Used</span>
              <b className="tile-value">{fmtUsd(paper?.margin_used)}</b>
            </div>
            <div>
              <span className="tile-label">Unrealized PnL</span>
              <b className={`tile-value ${toneClass(toneOf(paper?.unrealized_pnl))}`}>{fmtUsd(paper?.unrealized_pnl)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Realized PnL</span>
              <b className={`tile-value ${toneClass(toneOf(paper?.realized_pnl))}`}>{fmtUsd(paper?.realized_pnl)}</b>
            </div>
            <div>
              <span className="tile-label">Daily PnL</span>
              <b className={`tile-value ${toneClass(toneOf(paper?.daily_pnl))}`}>{fmtUsd(paper?.daily_pnl)}</b>
            </div>
            <div className="align-right">
              <span className="tile-label">Win Rate</span>
              <b className="tile-value">{paper?.win_rate != null ? fmtPct(paper.win_rate) : "—"}</b>
            </div>
            <div>
              <span className="tile-label">Open Positions</span>
              <b className="tile-value">
                {paper?.open_positions ?? "—"}
                {paper?.max_open_positions != null ? ` of ${paper.max_open_positions}` : ""}
              </b>
            </div>
            <div className="align-right">
              <span className="tile-label">Notional Exposure</span>
              <b className="tile-value">{fmtUsd(paper?.total_notional_exposure)}</b>
            </div>
          </div>

          <div style={{ marginTop: 12 }}>
            <AutoCardTable
              columns={[
                { key: "symbol", label: "Symbol", render: (p: any) => <b>{p.symbol}</b> },
                { key: "side", label: "Side", render: (p: any) => <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span> },
                { key: "entry", label: "Entry", render: (p: any) => fmtNum(p.entry) },
                { key: "mark", label: "Mark", render: (p: any) => fmtNum(p.mark) },
                { key: "pnl", label: "uPnL", render: (p: any) => <span className={toneClass(toneOf(p.pnl))}>{fmtUsd(p.pnl)}</span> },
              ]}
              rows={paperPositions.slice(0, 5)}
              keyField={(p: any) => p.id}
              titleColumn="symbol"
              statusColumn="side"
              emptyMessage="No open paper positions"
            />
          </div>

          <div className="controls" style={{ marginTop: 12 }}>
            <button className="mini-btn" onClick={() => navigate("paper-trading")}>
              Open Paper Terminal <ArrowRight size={13} />
            </button>
          </div>
        </Card>

        <Card
          title="Binance Real Account"
          className={`portfolio-compare-card ${mode === "BINANCE_LIVE" ? "live-danger-card" : ""}`}
          right={
            <span className={`badge ${mode === "BINANCE_LIVE" ? "badge-red" : mode === "BINANCE_LIVE_LOCKED" ? "badge-orange" : ""}`}>
              {status?.binance_configured
                ? status?.binance_connected
                  ? "API: Connected"
                  : "API: Configured"
                : "API: Not Configured"}
            </span>
          }
        >
          {binance?.available ? (
            <>
              <div className="kv-grid">
                <div>
                  <span className="tile-label">Wallet Balance</span>
                  <b className="tile-value">{fmtUsd(binance?.total_wallet_balance)}</b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Available</span>
                  <b className="tile-value">{fmtUsd(binance?.available_balance)}</b>
                </div>
                <div>
                  <span className="tile-label">Margin Balance</span>
                  <b className="tile-value">{fmtUsd(binance?.margin_balance)}</b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Margin Used</span>
                  <b className="tile-value">{fmtUsd(binance?.margin_used)}</b>
                </div>
                <div>
                  <span className="tile-label">Unrealized PnL</span>
                  <b className={`tile-value ${toneClass(toneOf(binance?.unrealized_pnl))}`}>{fmtUsd(binance?.unrealized_pnl)}</b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Daily PnL</span>
                  <b className={`tile-value ${toneClass(toneOf(binance?.daily_pnl))}`}>{fmtUsd(binance?.daily_pnl)}</b>
                </div>
                <div>
                  <span className="tile-label">Open Positions</span>
                  <b className="tile-value">{binance?.open_positions ?? "—"}</b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Notional Exposure</span>
                  <b className="tile-value">{fmtUsd(binance?.total_notional_exposure)}</b>
                </div>
                <div>
                  <span className="tile-label">Nearest Liq. Distance</span>
                  <b className="tile-value orange">
                    {binance?.nearest_liquidation_distance_pct != null ? fmtPct(binance.nearest_liquidation_distance_pct) : "—"}
                  </b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Trading</span>
                  <b className={`tile-value ${mode === "BINANCE_LIVE" ? "red" : "green"}`}>
                    {mode === "BINANCE_LIVE" ? "LIVE" : "LOCKED"}
                  </b>
                </div>
              </div>

              <div style={{ marginTop: 12 }}>
                <AutoCardTable
                  columns={[
                    { key: "symbol", label: "Symbol", render: (p: any) => <b>{p.symbol}</b> },
                    { key: "side", label: "Side", render: (p: any) => <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span> },
                    { key: "entry", label: "Entry", render: (p: any) => fmtNum(p.entry_price) },
                    { key: "mark", label: "Mark", render: (p: any) => fmtNum(p.mark_price) },
                    { key: "pnl", label: "uPnL", render: (p: any) => <span className={toneClass(toneOf(p.unrealized_pnl))}>{fmtUsd(p.unrealized_pnl)}</span> },
                  ]}
                  rows={(binancePositions?.positions || []).slice(0, 5).map((p: any, i: number) => ({ ...p, _key: p.symbol + i }))}
                  keyField={(p: any) => p._key}
                  titleColumn="symbol"
                  statusColumn="side"
                  emptyMessage="No open Binance positions"
                />
              </div>
            </>
          ) : (
            <p className="regime-desc">
              <AlertTriangle size={13} /> Binance account unavailable{binance?.reason ? ` — ${binance.reason}` : ""}
            </p>
          )}

          <div className="controls" style={{ marginTop: 12 }}>
            <button className="mini-btn" onClick={() => navigate("binance-real")}>
              Open Binance Real Terminal <ArrowRight size={13} />
            </button>
          </div>
        </Card>
      </div>

      {/* ------------- warnings ------------- */}
      {Array.isArray((props.exchangeStatus as any)?.warnings) && (props.exchangeStatus as any).warnings.length > 0 && (
        <Card title="Safety Warnings" full>
          <div className="portfolio-warnings">
            {(props.exchangeStatus as any).warnings.map((w: string) => (
              <p key={w}>
                <AlertTriangle size={13} /> {w}
              </p>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
