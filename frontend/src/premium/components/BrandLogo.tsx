import { BRANDS, type BrandKey } from "../../assets/brands";

type Size = "inline" | "card" | "hero";

const SIZE_PX: Record<Size, number> = { inline: 20, card: 32, hero: 44 };

type Props = {
  brand: BrandKey;
  size?: Size;
  className?: string;
};

/** Renders one bundled brand mark at a consistent size tier. Never used
 * decoratively - only in symbol/balance/exchange-status contexts where the
 * mark improves recognition (market ticker, symbol selector, chart title,
 * positions, portfolio, execution/protection provider status). */
export default function BrandLogo({ brand, size = "inline", className }: Props) {
  const def = BRANDS[brand];
  const px = SIZE_PX[size];
  return (
    <span
      className={`qp-brand-logo qp-brand-logo-${size}${className ? ` ${className}` : ""}`}
      style={{ color: `var(${def.colorVar})`, width: px, height: px }}
      role="img"
      aria-label={def.label}
    >
      <def.Icon size={px} />
    </span>
  );
}
