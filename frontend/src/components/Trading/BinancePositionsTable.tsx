import Card from "../Layout/Card";
import AutoCardTable from "../Responsive/AutoCardTable";
import { fmtNum, fmtUsd, toneClass, toneOf } from "../../lib/format";

/** Real Binance open-positions table + TP/SL-edit + close-with-confirmation
 *  actions, extracted from BinanceRealPage.tsx so PositionsPage's Binance
 *  Live tab can show the exact same data/actions without duplicating the
 *  column config or re-implementing the close/edit flow. Never reads or
 *  writes paper data. */

type Props = {
  title: string;
  full?: boolean;
  className?: string;
  positionRows: any[];
  busy: boolean;
  isLive: boolean;
  unavailable?: boolean;
  unavailableReason?: string | null;
  onEdit: (position: any) => void;
  onRequestClose: (position: any) => void;
};

export default function BinancePositionsTable({
  title,
  full,
  className,
  positionRows,
  busy,
  isLive,
  unavailable,
  unavailableReason,
  onEdit,
  onRequestClose,
}: Props) {
  return (
    <Card title={title} full={full} className={className}>
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
        ]}
        rows={positionRows.map((p: any, i: number) => ({ ...p, _key: p.symbol + i }))}
        keyField={(p: any) => p._key}
        titleColumn="symbol"
        statusColumn="side"
        renderActions={(p: any) => (
          <>
            <button
              className="mini-btn"
              disabled={busy || !isLive || p.id == null}
              title={!isLive ? "Locked — unlock real trading first" : p.id == null ? "Waiting for sync…" : "Edit Binance Live TP/SL"}
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
            <button
              className="btn-danger mini"
              disabled={busy || !isLive}
              title={!isLive ? "Locked — unlock real trading first" : "Close on Binance"}
              onClick={() => onRequestClose(p)}
            >
              Close
            </button>
          </>
        )}
        emptyMessage={unavailable ? unavailableReason || "Unavailable" : "No open Binance positions"}
      />
    </Card>
  );
}
