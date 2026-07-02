import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Star, TrendingDown, TrendingUp } from "lucide-react";
import type { Candle } from "../../hooks/useAppData";
import { fmtNum } from "../../lib/format";

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

type Props = {
  symbol: string;
  onSymbolChange: (s: string) => void;
  interval: string;
  onIntervalChange: (i: string) => void;
  candles: Candle[];
  ticker: any;
  prediction: any;
};

export default function ChartPanel({
  symbol,
  onSymbolChange,
  interval,
  onIntervalChange,
  candles,
  ticker,
  prediction,
}: Props) {
  const lastPrice = Number(ticker?.lastPrice || 0);
  const change = Number(ticker?.priceChangePercent || 0);
  const changeAbs = Number(ticker?.priceChange || 0);
  const features = prediction?.features || {};

  return (
    <div className="chart-card">
      <div className="chart-toolbar">
        <div className="chart-symbol">
          <select value={symbol} onChange={(e) => onSymbolChange(e.target.value)}>
            <option value="BTCUSDT">BTCUSDT PERPETUAL</option>
            <option value="ETHUSDT">ETHUSDT PERPETUAL</option>
          </select>
          <button className="icon-btn" title="Favorite (coming soon)">
            <Star size={16} />
          </button>
        </div>

        <div className="tf-group">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              className={tf === interval ? "tf-btn active" : "tf-btn"}
              onClick={() => onIntervalChange(tf)}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      <div className="chart-price-row">
        <h3>${lastPrice.toLocaleString()}</h3>
        <span className={change >= 0 ? "green" : "red"}>
          {change >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
          {change.toFixed(2)}% ({changeAbs >= 0 ? "+" : ""}
          {changeAbs.toFixed(1)})
        </span>
      </div>

      <ResponsiveContainer width="100%" height={340}>
        <AreaChart data={candles}>
          <defs>
            <linearGradient id="price" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#7c5cff" stopOpacity={0.5} />
              <stop offset="95%" stopColor="#7c5cff" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="label" stroke="#8b90a8" fontSize={12} />
          <YAxis domain={["dataMin", "dataMax"]} stroke="#8b90a8" fontSize={12} />
          <Tooltip />
          <Area type="monotone" dataKey="close" stroke="#7c5cff" fill="url(#price)" strokeWidth={2.5} />
        </AreaChart>
      </ResponsiveContainer>

      <div className="indicators-row">
        <span className="tile-label">Indicators</span>
        <div className="indicator-chips">
          <span className="chip">
            EMA 20 <b>{fmtNum(features.ema20, 1)}</b>
          </span>
          <span className="chip">
            EMA 50 <b>{fmtNum(features.ema50, 1)}</b>
          </span>
          <span className="chip">
            RSI <b>{fmtNum(features.rsi, 1)}</b>
          </span>
          <span className={`chip ${(features.macd_hist ?? 0) >= 0 ? "green" : "red"}`}>
            MACD <b>{(features.macd_hist ?? 0) >= 0 ? "Bullish" : "Bearish"}</b>
          </span>
          <span className="chip">
            BB Width <b>{fmtNum(features.bb_width, 2)}</b>
          </span>
        </div>
      </div>
    </div>
  );
}
