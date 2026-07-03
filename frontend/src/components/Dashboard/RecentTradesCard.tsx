import { memo } from "react";

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

function RecentTradesCard({ trades, rows = 8 }: Props) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Side</th>
            <th>Price</th>
            <th>Size</th>
          </tr>
        </thead>
        <tbody>
          {trades.slice(0, rows).map((t) => (
            <tr key={t.id}>
              <td>{new Date(t.time).toLocaleTimeString()}</td>
              <td className={t.side === "BUY" ? "green" : "red"}>{t.side}</td>
              <td>{t.price.toFixed(1)}</td>
              <td>{t.qty.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(RecentTradesCard);
