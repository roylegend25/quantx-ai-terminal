import { Play, Loader2 } from "lucide-react";
import type { DirectionLens } from "../../lib/backtestStats";

export type BtConfig = {
  symbol: string;
  timeframe: string;
  rangeKey: string;
  customStart: string;
  customEnd: string;
  capital: number;
  positionSize: number;
  commissionPct: number;
  slippageBps: number;
  fundingPct: number;
  leverage: number;
  direction: DirectionLens;
  strategy: string;
  // custom-strategy extras (sent only when strategy === "custom")
  preset: string;
  atrSlMult: number;
  atrTpMult: number;
  entryThreshold: number;
};

export const SYMBOLS = ["BTCUSDT", "ETHUSDT"];
export const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"];
export const RANGES: { key: string; label: string; days: number | null }[] = [
  { key: "1w", label: "Last Week", days: 7 },
  { key: "1mo", label: "Last Month", days: 30 },
  { key: "3mo", label: "3 Months", days: 90 },
  { key: "6mo", label: "6 Months", days: 182 },
  { key: "1y", label: "1 Year", days: 365 },
  { key: "custom", label: "Custom", days: null },
];
export const STRATEGIES: { value: string; label: string }[] = [
  { value: "trend", label: "Trend Following" },
  { value: "momentum", label: "Momentum" },
  { value: "mean_reversion", label: "Mean Reversion" },
  { value: "ensemble", label: "Ensemble" },
  { value: "champion_ml", label: "Champion Model" },
  { value: "custom", label: "Custom" },
];
export const PRESETS = ["", "scalping", "intraday", "swing", "trend_following", "mean_reversion", "high_volatility", "low_volatility"];

const DIRECTIONS: { value: DirectionLens; label: string }[] = [
  { value: "long", label: "Long Only" },
  { value: "short", label: "Short Only" },
  { value: "both", label: "Long & Short" },
];

type Props = {
  cfg: BtConfig;
  onChange: (patch: Partial<BtConfig>) => void;
  onRun: () => void;
  running: boolean;
};

export default function ControlBar({ cfg, onChange, onRun, running }: Props) {
  const num = (key: keyof BtConfig) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ [key]: Number(e.target.value) } as Partial<BtConfig>);

  return (
    <div className="bt-controlbar glass-panel">
      <div className="bt-controlbar-grid">
        <div className="lab-field bt-field">
          <label className="tile-label">Symbol</label>
          <select value={cfg.symbol} onChange={(e) => onChange({ symbol: e.target.value })}>
            {SYMBOLS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        <div className="lab-field bt-field bt-field-wide">
          <label className="tile-label">Timeframe</label>
          <div className="bt-tf-row">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                type="button"
                className={`bt-chip ${cfg.timeframe === tf ? "active" : ""}`}
                onClick={() => onChange({ timeframe: tf })}
              >
                {tf.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        <div className="lab-field bt-field">
          <label className="tile-label">Date Range</label>
          <select value={cfg.rangeKey} onChange={(e) => onChange({ rangeKey: e.target.value })}>
            {RANGES.map((r) => (
              <option key={r.key} value={r.key}>{r.label}</option>
            ))}
          </select>
        </div>

        {cfg.rangeKey === "custom" && (
          <>
            <div className="lab-field bt-field">
              <label className="tile-label">From</label>
              <input type="date" value={cfg.customStart} onChange={(e) => onChange({ customStart: e.target.value })} />
            </div>
            <div className="lab-field bt-field">
              <label className="tile-label">To</label>
              <input type="date" value={cfg.customEnd} onChange={(e) => onChange({ customEnd: e.target.value })} />
            </div>
          </>
        )}

        <div className="lab-field bt-field">
          <label className="tile-label">Initial Capital $</label>
          <input type="number" min="100" step="500" value={cfg.capital} onChange={num("capital")} />
        </div>
        <div className="lab-field bt-field">
          <label className="tile-label">Position Size $</label>
          <input type="number" min="10" step="100" value={cfg.positionSize} onChange={num("positionSize")} />
        </div>
        <div className="lab-field bt-field">
          <label className="tile-label">Commission %</label>
          <input type="number" min="0" step="0.01" value={cfg.commissionPct} onChange={num("commissionPct")} />
        </div>
        <div className="lab-field bt-field">
          <label className="tile-label">Slippage (bps)</label>
          <input type="number" min="0" step="0.5" value={cfg.slippageBps} onChange={num("slippageBps")} />
        </div>
        <div className="lab-field bt-field">
          <label className="tile-label">Funding % / 8h</label>
          <input type="number" min="0" step="0.005" value={cfg.fundingPct} onChange={num("fundingPct")} />
        </div>
        <div className="lab-field bt-field">
          <label className="tile-label" title="Multiplies the notional position size sent to the simulator">Leverage ×</label>
          <input type="number" min="1" max="25" step="1" value={cfg.leverage} onChange={num("leverage")} />
        </div>

        <div className="lab-field bt-field">
          <label className="tile-label">Direction</label>
          <select value={cfg.direction} onChange={(e) => onChange({ direction: e.target.value as DirectionLens })}>
            {DIRECTIONS.map((d) => (
              <option key={d.value} value={d.value}>{d.label}</option>
            ))}
          </select>
        </div>

        <div className="lab-field bt-field">
          <label className="tile-label">Strategy</label>
          <select value={cfg.strategy} onChange={(e) => onChange({ strategy: e.target.value })}>
            {STRATEGIES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>

        {cfg.strategy === "custom" && (
          <>
            <div className="lab-field bt-field">
              <label className="tile-label">Preset</label>
              <select value={cfg.preset} onChange={(e) => onChange({ preset: e.target.value })}>
                {PRESETS.map((p) => (
                  <option key={p} value={p}>{p ? p.replace(/_/g, " ") : "none (ensemble)"}</option>
                ))}
              </select>
            </div>
            <div className="lab-field bt-field">
              <label className="tile-label">ATR SL ×</label>
              <input type="number" min="0.2" step="0.1" value={cfg.atrSlMult} onChange={num("atrSlMult")} />
            </div>
            <div className="lab-field bt-field">
              <label className="tile-label">ATR TP ×</label>
              <input type="number" min="0.2" step="0.1" value={cfg.atrTpMult} onChange={num("atrTpMult")} />
            </div>
            <div className="lab-field bt-field">
              <label className="tile-label">Min Confidence</label>
              <input type="number" min="0" max="100" step="5" value={cfg.entryThreshold} onChange={num("entryThreshold")} />
            </div>
          </>
        )}
      </div>

      <button type="button" className="bt-run-btn" onClick={onRun} disabled={running}>
        {running ? <Loader2 size={18} className="bt-spin" /> : <Play size={18} />}
        {running ? "Running Backtest…" : "Run Backtest"}
      </button>
    </div>
  );
}
