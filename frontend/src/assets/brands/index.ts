import type { ComponentType } from "react";
import {
  BitcoinIcon,
  EthereumIcon,
  BinanceIcon,
  BnbIcon,
  TetherIcon,
  UsdcIcon,
  SolanaIcon,
  XrpIcon,
  QuantxIcon,
} from "./icons";

export type BrandIconProps = { size?: number; className?: string };

export type BrandKey = "BTC" | "ETH" | "BNB" | "USDT" | "USDC" | "SOL" | "XRP" | "BINANCE" | "QUANTX";

export type BrandDef = {
  key: BrandKey;
  label: string;
  Icon: ComponentType<BrandIconProps>;
  /** CSS custom property name (see premium/tokens.css) - not a literal hex,
   * so the same brand def works if the token palette is ever retuned. */
  colorVar: string;
};

export const BRANDS: Record<BrandKey, BrandDef> = {
  BTC: { key: "BTC", label: "Bitcoin", Icon: BitcoinIcon, colorVar: "--qp-btc-orange" },
  ETH: { key: "ETH", label: "Ethereum", Icon: EthereumIcon, colorVar: "--qp-eth-blue" },
  BNB: { key: "BNB", label: "BNB", Icon: BnbIcon, colorVar: "--qp-binance-yellow" },
  USDT: { key: "USDT", label: "Tether", Icon: TetherIcon, colorVar: "--qp-green" },
  USDC: { key: "USDC", label: "USD Coin", Icon: UsdcIcon, colorVar: "--qp-accent-strong" },
  SOL: { key: "SOL", label: "Solana", Icon: SolanaIcon, colorVar: "--qp-accent-strong" },
  XRP: { key: "XRP", label: "XRP", Icon: XrpIcon, colorVar: "--qp-text-primary" },
  BINANCE: { key: "BINANCE", label: "Binance", Icon: BinanceIcon, colorVar: "--qp-binance-yellow" },
  QUANTX: { key: "QUANTX", label: "QuantX", Icon: QuantxIcon, colorVar: "--qp-accent-strong" },
};

const QUOTE_SUFFIXES = ["USDT", "USDC", "BUSD", "FDUSD", "BTC", "ETH", "USD"];

/** "BTCUSDT" -> "BTC", "ETHUSDT" -> "ETH", "SOLBUSD" -> "SOL", etc. Only the
 * two symbols the bot actually trades today (BTCUSDT/ETHUSDT) are
 * load-bearing; everything else is forward-looking and falls back to a
 * plain ticker-text glyph via MarketAssetIcon if it isn't in BRANDS. */
export function baseAssetFromSymbol(symbol: string | undefined | null): string {
  if (!symbol) return "";
  const upper = symbol.toUpperCase();
  for (const suffix of QUOTE_SUFFIXES) {
    if (upper.length > suffix.length && upper.endsWith(suffix)) {
      return upper.slice(0, -suffix.length);
    }
  }
  return upper;
}

export function brandForSymbol(symbol: string | undefined | null): BrandDef | null {
  const base = baseAssetFromSymbol(symbol);
  return (BRANDS as Record<string, BrandDef>)[base] || null;
}
