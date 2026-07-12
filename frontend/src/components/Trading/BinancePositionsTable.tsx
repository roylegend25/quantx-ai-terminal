import { useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, RefreshCw, ShieldAlert } from "lucide-react";
import Card from "../Layout/Card";
import AutoCardTable from "../Responsive/AutoCardTable";
import ProtectPositionModal from "./ProtectPositionModal";
import { fmtNum, fmtUsd, toneClass, toneOf } from "../../lib/format";
import { api } from "../../services/api";
import type { BinanceLiveGate } from "./BinanceLiveGate";

/** Real Binance open-positions table + partial/full close, TP/SL edit and
 *  cancel, and sync actions - shared by BinanceRealPage.tsx and
 *  PositionsPage's Binance Live tab so both render the exact same data and
 *  actions instead of duplicating the column config or the close/edit
 *  flow. Never reads or writes paper data. Every action is gated through
 *  the shared BinanceLiveGate - a locked gate disables every button with
 *  the specific reason, it never hides them.
 *
 *  Protection status (Phase 27) is read directly off each position row -
 *  the backend derives it fresh from Binance's own open orders on every
 *  fetch (app.trading.protection), never from local state - so "Missing
 *  TP & SL" here is always ground truth, not a guess. */

const PROTECTION_LABELS: Record<string, string> = {
  PROTECTED: "Protected",
  MISSING_TP: "Missing TP",
  MISSING_SL: "Missing SL",
  MISSING_TP_SL: "Missing TP & SL",
};

function ProtectionBadge({ status }: { status?: string }) {
  const label = status ? PROTECTION_LABELS[status] || "Unknown" : "Unknown";
  const tone = status === "PROTECTED" ? "green" : status ? "red" : "yellow";
  return <span className={tone}>{label}</span>;
}

/** Phase 26 Algo Order Provider: which order surface (classic POST
 *  /fapi/v1/order vs algo POST /fapi/v1/algoOrder) this position's TP/SL
 *  actually lives on - written by the backend the moment protection is
 *  placed (app.trading.protection_provider), never guessed on the UI
 *  side. Undetected yet (no protection placed through this app for the
 *  symbol) reads as "Auto". */
function ProviderBadge({ provider }: { provider?: string | null }) {
  const label = provider === "algo" ? "Algo" : provider === "classic" ? "Classic" : "Auto";
  return <span className={provider ? "" : "tile-label"}>{label}</span>;
}

type Props = {
  title: string;
  full?: boolean;
  className?: string;
  positionRows: any[];
  busy: boolean;
  gate: BinanceLiveGate;
  unavailable?: boolean;
  unavailableReason?: string | null;
  /** Phase 29: true when this data is a cached snapshot served during a
   *  Binance rate limit rather than a fresh read - the rows themselves are
   *  still the last known good positions, never wiped to empty. */
  stale?: boolean;
  retryAfterSeconds?: number | null;
  onEdit: (position: any) => void;
  onRequestClose: (position: any) => void;
  onPartialClose: (position: any, quantity: number) => void;
  onCancelProtective: (position: any, kind: "sl" | "tp") => void;
  onSync?: () => void;
  showToast?: (message: string, tone?: "success" | "error") => void;
};

function PartialCloseModal({
  position,
  busy,
  onClose,
  onConfirm,
}: {
  position: any;
  busy: boolean;
  onClose: () => void;
  onConfirm: (quantity: number) => void;
}) {
  const maxQty = Number(position.quantity) || 0;
  const [qty, setQty] = useState(String((maxQty / 2).toFixed(6)));
  const parsed = Number(qty);
  const valid = Number.isFinite(parsed) && parsed > 0 && parsed < maxQty;

  return createPortal(
    <div className="modal-overlay" onClick={() => !busy && onClose()}>
      <div className="modal-card risk-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Partial close {position.symbol} {position.side}?</h3>
        <p className="risk-modal-error">
          Sends a real reduce-only MARKET order for part of this position. Full size: {fmtNum(maxQty, 6)}.
        </p>
        <label>
          <span className="tile-label">Quantity to close</span>
          <input
            type="number"
            min={0}
            max={maxQty}
            step="any"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            autoFocus
          />
          {!valid && <small style={{ color: "var(--c-red, #e5484d)" }}>Enter a quantity between 0 and {fmtNum(maxQty, 6)}</small>}
        </label>
        <div className="modal-actions">
          <button className="mini-btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button className="btn-danger" disabled={!valid || busy} onClick={() => onConfirm(parsed)}>
            {busy ? "Closing…" : "Partial Close"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

export default function BinancePositionsTable({
  title,
  full,
  className,
  positionRows,
  busy,
  gate,
  unavailable,
  unavailableReason,
  stale,
  retryAfterSeconds,
  onEdit,
  onRequestClose,
  onPartialClose,
  onCancelProtective,
  onSync,
  showToast,
}: Props) {
  const [partialTarget, setPartialTarget] = useState<any>(null);
  const [protectTarget, setProtectTarget] = useState<any>(null);
  const [protecting, setProtecting] = useState(false);
  const disabledTitle = gate.disabledReason ?? "";
  const unprotected = positionRows.filter((p: any) => p.protection_status && p.protection_status !== "PROTECTED");

  async function handleProtectConfirm(takeProfit: number, stopLoss: number) {
    if (!protectTarget) return;
    setProtecting(true);
    try {
      const result = await api.binanceProtectPosition(protectTarget.symbol, { take_profit: takeProfit, stop_loss: stopLoss });
      if (result.protection_status === "PROTECTED") {
        showToast?.(`${protectTarget.symbol} is now protected`, "success");
        setProtectTarget(null);
      } else {
        showToast?.(`Protection failed: ${result.errors?.join("; ") || result.protection_status}`, "error");
      }
      await onSync?.();
    } catch (e: any) {
      showToast?.(e?.message || "Failed to protect position", "error");
    } finally {
      setProtecting(false);
    }
  }

  return (
    <Card
      title={title}
      full={full}
      className={className}
      right={
        <div className="controls">
          <span className="badge">Source: Binance Real</span>
          {onSync && (
            <button className="mini-btn" disabled={busy} onClick={onSync}>
              <RefreshCw size={13} /> Sync
            </button>
          )}
        </div>
      }
    >
      {stale && (
        <div className="regime-focus" style={{ marginBottom: 14 }}>
          <span className="tile-label">
            <RefreshCw size={13} /> Binance API Cooling Down — showing cached data
            {retryAfterSeconds != null ? ` (retry in ${Math.ceil(retryAfterSeconds)}s)` : ""}
          </span>
        </div>
      )}

      {unprotected.length > 0 && (
        <div className="regime-focus blocked" style={{ marginBottom: 14 }}>
          <span className="tile-label">
            <ShieldAlert size={13} /> Live position is unprotected. Add TP/SL immediately.
          </span>
          <p className="regime-desc">
            {unprotected.map((p: any) => `${p.symbol} (${PROTECTION_LABELS[p.protection_status] || p.protection_status})`).join(", ")}
          </p>
        </div>
      )}

      <AutoCardTable
        columns={[
          { key: "symbol", label: "Symbol", render: (p: any) => <b>{p.symbol}</b> },
          { key: "side", label: "Side", render: (p: any) => <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span> },
          { key: "size", label: "Size", render: (p: any) => fmtNum(p.quantity, 6) },
          { key: "entry", label: "Entry", render: (p: any) => fmtNum(p.entry_price) },
          { key: "mark", label: "Mark", render: (p: any) => fmtNum(p.mark_price) },
          { key: "liq", label: "Liquidation", render: (p: any) => (p.liquidation_price != null ? fmtNum(p.liquidation_price) : "—") },
          { key: "margin", label: "Margin", render: (p: any) => fmtUsd(p.margin_used) },
          { key: "type", label: "Type", render: (p: any) => p.margin_type || "—" },
          { key: "lev", label: "Lev.", render: (p: any) => (p.leverage != null ? `${p.leverage}x` : "—") },
          { key: "pnl", label: "uPnL", render: (p: any) => <span className={toneClass(toneOf(p.unrealized_pnl))}>{fmtUsd(p.unrealized_pnl)}</span> },
          { key: "tp", label: "Live TP", render: (p: any) => (p.tp != null ? fmtNum(p.tp) : "—") },
          { key: "sl", label: "Live SL", render: (p: any) => (p.sl != null ? fmtNum(p.sl) : "—") },
          { key: "protection", label: "Protection Status", render: (p: any) => <ProtectionBadge status={p.protection_status} /> },
          { key: "provider", label: "Protection Provider", render: (p: any) => <ProviderBadge provider={p.protection_provider} /> },
        ]}
        rows={positionRows.map((p: any, i: number) => ({ ...p, _key: p.symbol + i }))}
        keyField={(p: any) => p._key}
        titleColumn="symbol"
        statusColumn="side"
        renderActions={(p: any) => (
          <>
            {p.protection_status && p.protection_status !== "PROTECTED" && (
              <button
                className="btn-danger mini"
                disabled={busy}
                title="Place TP/SL on this unprotected position now"
                onClick={() => setProtectTarget(p)}
              >
                <AlertTriangle size={12} /> Auto-Protect
              </button>
            )}
            <button
              className="mini-btn"
              disabled={busy || !gate.canEditRisk || p.id == null}
              title={!gate.canEditRisk ? disabledTitle : p.id == null ? "Waiting for sync…" : "Edit Binance Live TP/SL"}
              onClick={() =>
                onEdit({
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
            {p.sl_order_id != null && (
              <button
                className="mini-btn"
                disabled={busy || !gate.canCancelOrders}
                title={!gate.canCancelOrders ? disabledTitle : "Cancel the resting stop-loss order"}
                onClick={() => onCancelProtective(p, "sl")}
              >
                Cancel SL
              </button>
            )}
            {p.tp_order_id != null && (
              <button
                className="mini-btn"
                disabled={busy || !gate.canCancelOrders}
                title={!gate.canCancelOrders ? disabledTitle : "Cancel the resting take-profit order"}
                onClick={() => onCancelProtective(p, "tp")}
              >
                Cancel TP
              </button>
            )}
            <button
              className="mini-btn"
              disabled={busy || !gate.canClosePositions}
              title={!gate.canClosePositions ? disabledTitle : "Partial close on Binance"}
              onClick={() => setPartialTarget(p)}
            >
              Partial
            </button>
            <button
              className="btn-danger mini"
              disabled={busy || !gate.canClosePositions}
              title={!gate.canClosePositions ? disabledTitle : "Close on Binance"}
              onClick={() => onRequestClose(p)}
            >
              Close
            </button>
          </>
        )}
        emptyMessage={unavailable ? unavailableReason || "Unavailable" : "No open Binance positions"}
      />

      {partialTarget && (
        <PartialCloseModal
          position={partialTarget}
          busy={busy}
          onClose={() => setPartialTarget(null)}
          onConfirm={(quantity) => {
            onPartialClose(partialTarget, quantity);
            setPartialTarget(null);
          }}
        />
      )}

      {protectTarget && (
        <ProtectPositionModal
          position={protectTarget}
          busy={protecting}
          onClose={() => setProtectTarget(null)}
          onConfirm={handleProtectConfirm}
        />
      )}
    </Card>
  );
}
