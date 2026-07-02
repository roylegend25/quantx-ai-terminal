import pandas as pd

from app.strategy.manager import StrategyManager
from app.backtest.metrics import calculate_metrics
from app.backtest.indicators import add_indicators

class BacktestEngine:

    def __init__(self):
        self.manager = StrategyManager()

    def run(self, csv_file: str):

        df = pd.read_csv(csv_file)
        df = add_indicators(df)

        trades = []

        position = None
        entry_price = 0

        for _, row in df.iterrows():

            features = {
                "price": row["close"],
                "ema20": row.get("ema20", row["close"]),
                "ema50": row.get("ema50", row["close"]),
                "ema200": row.get("ema200", row["close"]),
                "rsi": row.get("rsi", 50),
                "macd_hist": row.get("macd_hist", 0),
                "bb_width": row.get("bb_width", 0.01),
                "atr": row.get("atr", 100),
            }

            results = self.manager.evaluate(features)

            long_votes = 0
            short_votes = 0

            for result in results.values():
                if result["direction"] == "LONG":
                    long_votes += result["confidence"]

                elif result["direction"] == "SHORT":
                    short_votes += result["confidence"]

            signal = "NO_TRADE"

            if long_votes > short_votes and long_votes >= 50:
                signal = "LONG"

            elif short_votes > long_votes and short_votes >= 50:
                signal = "SHORT"

            price = row["close"]

            if position is None:
                if signal in ["LONG", "SHORT"]:
                    position = signal
                    entry_price = price

            else:
                if signal != position and signal != "NO_TRADE":

                    if position == "LONG":
                        pnl = price - entry_price
                    else:
                        pnl = entry_price - price

                    trades.append({
                        "entry": entry_price,
                        "exit": price,
                        "side": position,
                        "pnl": pnl,
                    })

                    position = signal
                    entry_price = price

        return calculate_metrics(trades)
