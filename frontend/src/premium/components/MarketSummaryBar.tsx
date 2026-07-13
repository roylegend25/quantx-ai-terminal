import MarketAssetIcon from "./MarketAssetIcon";

export type MarketSummaryItem = {
  symbol: string;
  price: number;
  changePct: number;
  sublabel?: string;
  subvalue?: string;
};

type Props = {
  items: MarketSummaryItem[];
  loading?: boolean;
};

function fmtPrice(n: number): string {
  if (!Number.isFinite(n)) return "-";
  return n.toLocaleString(undefined, { maximumFractionDigits: n >= 100 ? 2 : 4 });
}

/** Horizontally-scrollable market ticker strip (spec section 8): logo +
 * symbol + price + change per item. Contained by its own overflow-x, never
 * the page - see .qp-market-bar in components.css. */
export default function MarketSummaryBar({ items, loading }: Props) {
  if (loading) {
    return (
      <div className="qp-market-bar" aria-busy="true">
        {Array.from({ length: 4 }).map((_, i) => (
          <div className="qp-market-item qp-market-item-skeleton" key={i} />
        ))}
      </div>
    );
  }

  return (
    <div className="qp-market-bar">
      {items.map((item) => {
        const up = item.changePct >= 0;
        return (
          <div className="qp-market-item" key={item.symbol}>
            <MarketAssetIcon symbol={item.symbol} size="inline" />
            <div className="qp-market-item-body">
              <span className="qp-market-item-symbol">{item.symbol}</span>
              <span className="qp-market-item-price">${fmtPrice(item.price)}</span>
            </div>
            <span className={`qp-market-item-change ${up ? "qp-tone-text-positive" : "qp-tone-text-negative"}`}>
              {up ? "+" : ""}
              {item.changePct.toFixed(2)}%
            </span>
            {item.sublabel && (
              <div className="qp-market-item-sub">
                <span>{item.sublabel}</span>
                <b>{item.subvalue}</b>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
