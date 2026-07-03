import { memo } from "react";

type Props = {
  orderbook: any;
  rows?: number;
};

function OrderBookCard({ orderbook, rows = 6 }: Props) {
  const asks = (orderbook?.asks || []).slice(0, rows).slice().reverse();
  const bids = (orderbook?.bids || []).slice(0, rows);
  const bestBid = orderbook?.bids?.[0]?.price;
  const bestAsk = orderbook?.asks?.[0]?.price;
  const mid = bestBid && bestAsk ? (bestBid + bestAsk) / 2 : null;
  const spreadPct = mid && orderbook?.spread ? ((orderbook.spread / mid) * 100).toFixed(2) : "0.00";

  return (
    <div className="orderbook-3col">
      <div className="ob-col">
        <div className="ob-col-head red">Price (USDT)</div>
        {asks.map((x: any, i: number) => (
          <div className="book-row red" key={"a" + i}>
            <span>{x.price.toFixed(1)}</span>
            <b>{x.qty.toFixed(3)}</b>
          </div>
        ))}
      </div>

      <div className="ob-mid">
        <b>{mid ? mid.toFixed(1) : "—"}</b>
        <span>Spread</span>
        <span>
          {orderbook?.spread ?? "—"} ({spreadPct}%)
        </span>
      </div>

      <div className="ob-col">
        <div className="ob-col-head green">Price (USDT)</div>
        {bids.map((x: any, i: number) => (
          <div className="book-row green" key={"b" + i}>
            <span>{x.price.toFixed(1)}</span>
            <b>{x.qty.toFixed(3)}</b>
          </div>
        ))}
      </div>
    </div>
  );
}

export default memo(OrderBookCard);
