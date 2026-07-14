import { Pencil, RefreshCw } from "lucide-react";
import { fmtNum, fmtUsd, toneClass, toneOf } from "../../lib/format";
import type { Position } from "../../lib/portfolioStats";
import type { NavKey } from "../../lib/nav";
import { useBinanceSnapshot } from "../../hooks/useBinanceSnapshot";
import MarketAssetIcon from "../../premium/components/MarketAssetIcon";

/** Debug 3.pdf: the Dashboard's "Open Positions" card used to read ONLY
 *  props.positions (GET /api/paper/positions) - it had zero awareness of
 *  Binance real positions, so a genuinely open live position on Binance
 *  showed as "No open positions." on the front page while it was fully
 *  visible on the Binance Real / Positions pages, which read a completely
 *  different source. This component reads BOTH sources, keeps them
 *  visually and structurally separate (never merged into one list/count),
 *  and uses the exact same shared snapshot (useBinanceSnapshot -> GET
 *  /api/binance/snapshot) every other Binance-reading page already
 *  polls - no new independent Binance polling loop. */

const dash = "—";

const PROTECTION_LABELS: Record<string, string> = {
  PROTECTED: "Protected",
  MISSING_TP: "Missing TP",
  MISSING_SL: "Missing SL",
  MISSING_TP_SL: "Missing TP & SL",
};

type Props = {
  paperPositions: Position[];
  onClosePaper: (id: number) => void;
  onEditPaperRisk: (position: Position) => void;
  onNavigate: (key: NavKey) => void;
};

function BinancePositionCard({ p, onNavigate }: { p: any; onNavigate: (key: NavKey) => void }) {
  const protectionLabel = p.protection_status ? PROTECTION_LABELS[p.protection_status] || p.protection_status : "Unknown";
  const protectionTone = p.protection_status === "PROTECTED" ? "green" : p.protection_status ? "red" : "yellow";
  const providerLabel = p.protection_provider === "algo" ? "Algo" : p.protection_provider === "classic" ? "Classic" : "Auto";

  return (
    <div className="pos-card live-danger-card">
      <div className="pos-card-head" style={{ cursor: "default" }}>
        <MarketAssetIcon symbol={p.symbol} size="inline" />
        <b>{p.symbol}</b>
        <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span>
        {p.leverage != null && <span className="lev-badge">{p.leverage}x</span>}
        {p.stale && (
          <span className="badge badge-orange" title="Showing the last confirmed Binance data - API is cooling down">
            <RefreshCw size={10} style={{ verticalAlign: "-1px" }} /> Cached
          </span>
        )}
        <span className={`pos-card-pnl ${toneClass(toneOf(p.unrealized_pnl))}`}>{fmtUsd(p.unrealized_pnl)}</span>
      </div>

      <div className="pos-card-grid">
        <div>
          <span className="tile-label">Size</span>
          <b className="tile-value">{fmtNum(p.quantity, 6)}</b>
        </div>
        <div>
          <span className="tile-label">Entry</span>
          <b className="tile-value">{fmtNum(p.entry_price)}</b>
        </div>
        <div>
          <span className="tile-label">Mark</span>
          <b className="tile-value">{fmtNum(p.mark_price)}</b>
        </div>
        <div>
          <span className="tile-label">Liquidation</span>
          <b className="tile-value orange">{p.liquidation_price != null ? fmtNum(p.liquidation_price) : dash}</b>
        </div>
        <div>
          <span className="tile-label">Margin Used</span>
          <b className="tile-value">{p.margin_used != null ? fmtUsd(p.margin_used) : dash}</b>
        </div>
        <div>
          <span className="tile-label">Margin Type</span>
          <b className="tile-value">{p.margin_type || dash}</b>
        </div>
        <div>
          <span className="tile-label">Live Take Profit</span>
          <b className="tile-value green">{p.tp != null ? fmtNum(p.tp) : dash}</b>
        </div>
        <div>
          <span className="tile-label">Live Stop Loss</span>
          <b className="tile-value red">{p.sl != null ? fmtNum(p.sl) : dash}</b>
        </div>
        <div>
          <span className="tile-label">Protection</span>
          <b className={`tile-value ${protectionTone}`}>{protectionLabel}</b>
        </div>
        <div>
          <span className="tile-label">Protection Provider</span>
          <b className="tile-value">{providerLabel}</b>
        </div>
      </div>

      <div className="pos-actions">
        <button className="mini-btn" onClick={() => onNavigate("binance-real")}>
          Manage Position
        </button>
      </div>
    </div>
  );
}

function PaperPositionCard({
  p,
  onClose,
  onEditRisk,
  onNavigate,
}: {
  p: Position;
  onClose: (id: number) => void;
  onEditRisk: (position: Position) => void;
  onNavigate: (key: NavKey) => void;
}) {
  return (
    <div className="pos-card">
      <div className="pos-card-head" style={{ cursor: "default" }}>
        <MarketAssetIcon symbol={p.symbol} size="inline" />
        <b>{p.symbol}</b>
        <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span>
        {p.leverage != null && <span className="lev-badge">{p.leverage}x</span>}
        <span className={`pos-card-pnl ${toneClass(toneOf(p.pnl))}`}>{fmtUsd(p.pnl)}</span>
      </div>

      <div className="pos-card-grid">
        <div>
          <span className="tile-label">Size</span>
          <b className="tile-value">{p.qty}</b>
        </div>
        <div>
          <span className="tile-label">Entry</span>
          <b className="tile-value">{fmtNum(p.entry)}</b>
        </div>
        <div>
          <span className="tile-label">Mark</span>
          <b className="tile-value">{fmtNum(p.mark)}</b>
        </div>
        <div>
          <span className="tile-label">Paper Take Profit</span>
          <b className="tile-value green">{p.tp != null ? fmtNum(p.tp) : dash}</b>
        </div>
        <div>
          <span className="tile-label">Paper Stop Loss</span>
          <b className="tile-value red">{p.sl != null ? fmtNum(p.sl) : dash}</b>
        </div>
        <div>
          <span className="tile-label">Status</span>
          <b className="tile-value">Open</b>
        </div>
      </div>

      <div className="pos-actions">
        <button className="mini-btn mini-btn-edit" onClick={() => onEditRisk(p)}>
          <Pencil size={11} /> Edit Risk
        </button>
        <button className="mini-btn" onClick={() => onClose(p.id)}>
          Close
        </button>
        <button className="mini-btn" onClick={() => onNavigate("paper-trading")}>
          View in Paper Page
        </button>
      </div>
    </div>
  );
}

export default function DashboardOpenPositions({ paperPositions, onClosePaper, onEditPaperRisk, onNavigate }: Props) {
  const { snapshot, loading } = useBinanceSnapshot();

  const realPositions: any[] = Array.isArray(snapshot?.positions)
    ? snapshot.positions.filter((position) => {
        const size = Number(
          position.position_amt ?? position.positionAmt ?? position.size ?? position.quantity ?? 0,
        );
        return Number.isFinite(size) && Math.abs(size) > 0;
      })
    : [];
  const binanceStale = !!snapshot?.stale;
  const binanceRateLimited = !!snapshot?.rate_limited;
  const retryAfter = snapshot?.retry_after_seconds;
  const dataAvailable = !!snapshot && !(snapshot.errors?.length > 0);
  const showingCached = binanceStale || binanceRateLimited;

  const hasBinance = realPositions.length > 0;
  const hasPaper = paperPositions.length > 0;
  const stillLoadingFirstSnapshot = loading && !snapshot;

  const totalRealPnl = realPositions.reduce((sum, p) => sum + (Number(p.unrealized_pnl) || 0), 0);
  const allProtected = hasBinance && realPositions.every((p) => p.protection_status === "PROTECTED");

  let dataStatus = "Live";
  if (stillLoadingFirstSnapshot) dataStatus = "Loading…";
  else if (binanceRateLimited) dataStatus = `Cooling down${retryAfter != null ? ` (retry ${Math.ceil(retryAfter)}s)` : ""}`;
  else if (binanceStale) dataStatus = "Cached";
  else if (!dataAvailable) dataStatus = "Unavailable";

  return (
    <div className="dash-open-positions">
      <div className="kv-grid" style={{ marginBottom: 14 }}>
        <div>
          <span className="tile-label">Binance Real</span>
          <b className="tile-value">{dataAvailable || hasBinance ? realPositions.length : dash}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Paper</span>
          <b className="tile-value">{paperPositions.length}</b>
        </div>
        <div>
          <span className="tile-label">Real Unrealized PnL</span>
          <b className={`tile-value ${hasBinance ? toneClass(toneOf(totalRealPnl)) : ""}`}>
            {hasBinance ? fmtUsd(totalRealPnl) : dash}
          </b>
        </div>
        <div className="align-right">
          <span className="tile-label">Protection</span>
          <b className={`tile-value ${hasBinance ? (allProtected ? "green" : "red") : ""}`}>
            {hasBinance ? (allProtected ? "Protected" : "Action needed") : dash}
          </b>
        </div>
        <div>
          <span className="tile-label">Binance Data</span>
          <b className="tile-value">{dataStatus}</b>
        </div>
      </div>

      {!hasBinance && !hasPaper && dataAvailable && !stillLoadingFirstSnapshot && <p className="analytics-empty">No open positions.</p>}
      {!hasBinance && !hasPaper && stillLoadingFirstSnapshot && <p className="analytics-empty">Loading positions…</p>}

      {hasBinance && (
        <div className="dash-pos-section" style={{ marginBottom: hasPaper ? 18 : 0 }}>
          <div className="card-title dash-pos-section-title">
            <span className="badge badge-red">Binance Real</span>
            Binance Real Positions ({realPositions.length})
            {showingCached && (
              <span className="badge badge-orange" style={{ marginLeft: 6 }}>
                {binanceRateLimited ? "Cooling down — showing cached data" : "Cached"}
                {retryAfter != null ? ` · retry ${Math.ceil(retryAfter)}s` : ""}
              </span>
            )}
          </div>
          <div className="dash-pos-grid">
            {realPositions.map((p) => (
              <BinancePositionCard key={`binance-${p.symbol}`} p={{ ...p, stale: showingCached }} onNavigate={onNavigate} />
            ))}
          </div>
        </div>
      )}

      {!hasBinance && !dataAvailable && !stillLoadingFirstSnapshot && (
        <p className="regime-desc" style={{ marginBottom: hasPaper ? 12 : 0 }}>
          Binance Real: data unavailable{snapshot?.errors?.[0] ? ` — ${snapshot.errors[0]}` : ""}
        </p>
      )}

      {hasPaper && (
        <div className="dash-pos-section">
          <div className="card-title dash-pos-section-title">
            <span className="badge">Paper</span>
            Paper Positions ({paperPositions.length})
          </div>
          <div className="dash-pos-grid">
            {paperPositions.map((p) => (
              <PaperPositionCard key={`paper-${p.id}`} p={p} onClose={onClosePaper} onEditRisk={onEditPaperRisk} onNavigate={onNavigate} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
