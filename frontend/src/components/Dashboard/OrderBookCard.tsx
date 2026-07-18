import { memo, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

type Props = {
  orderbook: any;
  rows?: number;
  /** Wall-clock time (Date.now()) of the last successful order book fetch -
   *  used only to compute real elapsed staleness, never fabricated. */
  updatedAt?: number | null;
};

// Order book polls every 10s (useAppData POLL_MS) - two missed cycles is a
// real reconnect signal, not noise from one slow response.
const STALE_AFTER_MS = 25_000;

function Level({ price, qty, cumQty, maxCumQty, side }: { price: number; qty: number; cumQty: number; maxCumQty: number; side: "bid" | "ask" }) {
  const depthPct = maxCumQty > 0 ? Math.min(100, (cumQty / maxCumQty) * 100) : 0;
  return (
    <div className={`book-row ${side === "ask" ? "red" : "green"}`}>
      <div className="book-row-depth" style={{ [side === "ask" ? "right" : "left"]: 0, width: `${depthPct}%` }} />
      <span className="book-row-price">{price.toFixed(1)}</span>
      <b className="book-row-qty">{qty.toFixed(3)}</b>
    </div>
  );
}

function OrderBookCard({ orderbook, rows = 6, updatedAt }: Props) {
  // Re-renders once a second purely to keep the stale/reconnecting banner's
  // elapsed-time check current - it never touches the order book data itself.
  const [, forceTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => forceTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  const asksAsc = (orderbook?.asks || []).slice(0, rows);
  const asks = asksAsc.slice().reverse();
  const bids = (orderbook?.bids || []).slice(0, rows);
  const bestBid = orderbook?.bids?.[0]?.price;
  const bestAsk = orderbook?.asks?.[0]?.price;
  const mid = bestBid && bestAsk ? (bestBid + bestAsk) / 2 : null;
  const spreadPct = mid && orderbook?.spread ? ((orderbook.spread / mid) * 100).toFixed(2) : "0.00";

  // Cumulative depth from best price outward, for the in-row fill bar -
  // asks accumulate from the best ask (bottom of the reversed list)
  // upward, bids accumulate from the best bid downward.
  let askCum = 0;
  const askCumByPrice = new Map<number, number>();
  for (const x of asksAsc) { askCum += x.qty; askCumByPrice.set(x.price, askCum); }
  let bidCum = 0;
  const bidCumByPrice = new Map<number, number>();
  for (const x of bids) { bidCum += x.qty; bidCumByPrice.set(x.price, bidCum); }
  const maxCum = Math.max(askCum, bidCum, 1e-9);

  const isStale = updatedAt != null && Date.now() - updatedAt > STALE_AFTER_MS;
  const neverLoaded = updatedAt == null && !orderbook;

  return (
    <div className="orderbook-card">
      {isStale && (
        <div className="regime-focus" style={{ marginBottom: 10 }}>
          <span className="tile-label">
            <RefreshCw size={13} /> Reconnecting — last update {Math.round((Date.now() - (updatedAt as number)) / 1000)}s ago
          </span>
        </div>
      )}

      {neverLoaded ? (
        <p className="analytics-empty">Waiting for the order book feed…</p>
      ) : (
        <div className={`orderbook-3col ${isStale ? "orderbook-stale" : ""}`}>
          <div className="ob-col">
            <div className="ob-col-head red">Price (USDT)</div>
            {asks.map((x: any, i: number) => (
              <Level key={"a" + i} price={x.price} qty={x.qty} cumQty={askCumByPrice.get(x.price) ?? 0} maxCumQty={maxCum} side="ask" />
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
              <Level key={"b" + i} price={x.price} qty={x.qty} cumQty={bidCumByPrice.get(x.price) ?? 0} maxCumQty={maxCum} side="bid" />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(OrderBookCard);
