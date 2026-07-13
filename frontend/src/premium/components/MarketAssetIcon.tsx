import { brandForSymbol, baseAssetFromSymbol } from "../../assets/brands";
import BrandLogo from "./BrandLogo";

type Size = "inline" | "card" | "hero";

const SIZE_PX: Record<Size, number> = { inline: 20, card: 32, hero: 44 };

type Props = {
  /** Trading-pair symbol, e.g. "BTCUSDT". Also accepts a bare base asset
   * ("BTC"). Unrecognized symbols degrade to a text-glyph fallback instead
   * of a broken/missing icon - only BTC/ETH/USDT/BNB/Binance are
   * load-bearing today (the bot only trades BTCUSDT/ETHUSDT). */
  symbol: string | undefined | null;
  size?: Size;
  className?: string;
};

export default function MarketAssetIcon({ symbol, size = "inline", className }: Props) {
  const brand = brandForSymbol(symbol);
  if (brand) return <BrandLogo brand={brand.key} size={size} className={className} />;

  const base = baseAssetFromSymbol(symbol);
  const glyph = base.slice(0, 3) || "?";
  const px = SIZE_PX[size];

  return (
    <span
      className={`qp-brand-logo qp-brand-fallback qp-brand-logo-${size}${className ? ` ${className}` : ""}`}
      style={{ width: px, height: px, fontSize: px * 0.38 }}
      role="img"
      aria-label={base || "Unknown asset"}
    >
      {glyph}
    </span>
  );
}
