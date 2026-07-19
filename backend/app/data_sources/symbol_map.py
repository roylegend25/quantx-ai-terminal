"""Canonical symbol -> per-exchange instrument mapping for the resolver's
multi-exchange fallback (see app/data_sources/resolution_providers.py).

Only BTCUSDT/ETHUSDT are mapped for now - the unresolved-prediction pipeline
this serves only covers those two symbols. Extending to more symbols means
adding another entry here, nothing else.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExchangeSymbol:
    exchange: str
    market_type: str  # "usdt_perp" | "usdt_swap" | "spot"
    provider_symbol: str
    quote_asset: str = "USDT"


CANONICAL_SYMBOLS: dict[str, dict[str, ExchangeSymbol]] = {
    "BTCUSDT": {
        "binance_futures": ExchangeSymbol("binance", "usdt_perp", "BTCUSDT"),
        "binance_spot": ExchangeSymbol("binance", "spot", "BTCUSDT"),
        "bybit": ExchangeSymbol("bybit", "usdt_perp", "BTCUSDT"),
        "okx": ExchangeSymbol("okx", "usdt_swap", "BTC-USDT-SWAP"),
        "hyperliquid": ExchangeSymbol("hyperliquid", "usdt_perp", "BTC"),
    },
    "ETHUSDT": {
        "binance_futures": ExchangeSymbol("binance", "usdt_perp", "ETHUSDT"),
        "binance_spot": ExchangeSymbol("binance", "spot", "ETHUSDT"),
        "bybit": ExchangeSymbol("bybit", "usdt_perp", "ETHUSDT"),
        "okx": ExchangeSymbol("okx", "usdt_swap", "ETH-USDT-SWAP"),
        "hyperliquid": ExchangeSymbol("hyperliquid", "usdt_perp", "ETH"),
    },
}


def supported(symbol: str) -> bool:
    return symbol.upper() in CANONICAL_SYMBOLS


def provider_symbol(canonical_symbol: str, provider: str) -> ExchangeSymbol | None:
    return CANONICAL_SYMBOLS.get(canonical_symbol.upper(), {}).get(provider)
