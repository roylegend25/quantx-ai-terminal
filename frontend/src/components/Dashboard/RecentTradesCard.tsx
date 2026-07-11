import { memo } from "react";
import AutoCardTable, { type AutoCardColumn } from "../Responsive/AutoCardTable";

type Trade = {
  id: number;
  price: number;
  qty: number;
  time: number;
  side: "BUY" | "SELL";
};

type Props = {
  trades: Trade[];
  rows?: number;
};

const COLUMNS: AutoCardColumn<Trade>[] = [
  { key: "time", label: "Time", render: (t) => new Date(t.time).toLocaleTimeString() },
  { key: "side", label: "Side", render: (t) => <span className={t.side === "BUY" ? "green" : "red"}>{t.side}</span> },
  { key: "price", label: "Price", render: (t) => t.price.toFixed(1) },
  { key: "size", label: "Size", render: (t) => t.qty.toFixed(3) },
];

function RecentTradesCard({ trades, rows = 8 }: Props) {
  return (
    <AutoCardTable
      columns={COLUMNS}
      rows={trades.slice(0, rows)}
      keyField={(t) => t.id}
      titleColumn="time"
      statusColumn="side"
    />
  );
}

export default memo(RecentTradesCard);
