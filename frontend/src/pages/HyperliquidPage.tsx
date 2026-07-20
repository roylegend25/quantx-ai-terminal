import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { RefreshCw, Waves } from "lucide-react";
import Card from "../components/Layout/Card";
import { api } from "../services/api";
import { fmtUsd } from "../lib/format";
import type { AppData } from "../hooks/useAppData";

const POLL_MS = 8000;
const COIN_OPTIONS = ["BTC,ETH", "BTC", "ETH"] as const;
const NOTIONAL_OPTIONS = [25_000, 50_000, 100_000, 250_000, 500_000];

type Trade = {
  coin: string;
  side: "BUY" | "SELL" | string;
  price: number;
  size: number;
  notional: number;
  time: number;
  trade_id: number | null;
  hash: string | null;
};

type Snapshot = {
  trades: Trade[];
  coins: string[];
  min_notional: number;
  sample_window_seconds: number;
  fetched_at: number;
  data_source: "hyperliquid_ws" | "unavailable";
  error?: string;
};

function fmtTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function HyperliquidPage(_props: AppData) {
  const [coins, setCoins] = useState<(typeof COIN_OPTIONS)[number]>("BTC,ETH");
  const [minNotional, setMinNotional] = useState(NOTIONAL_OPTIONS[1]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      setLoading(true);
      const data: Snapshot | null = await api.hyperliquidLargeTrades(coins, minNotional).catch(() => null);
      if (cancelled) return;
      // A failed fetch keeps the last known-good trade list on screen rather
      // than flashing to empty - the honest "unavailable" state only comes
      // from the backend's own data_source field, never fabricated here.
      if (data) setSnapshot(data);
      setLoading(false);
    }
    poll();
    const id = window.setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [coins, minNotional]);

  const trades = snapshot?.trades ?? [];
  const buyCount = trades.filter((t) => t.side === "BUY").length;
  const sellCount = trades.filter((t) => t.side === "SELL").length;
  const totalNotional = trades.reduce((sum, t) => sum + t.notional, 0);
  const netFlow = trades.reduce((sum, t) => sum + (t.side === "BUY" ? t.notional : -t.notional), 0);

  const chartData = useMemo(
    () =>
      trades
        .slice()
        .reverse()
        .map((t) => ({ label: fmtTime(t.time), notional: t.side === "BUY" ? t.notional : -t.notional, side: t.side })),
    [trades]
  );

  const unavailable = snapshot?.data_source === "unavailable";
  const fetchedAgoSeconds = snapshot ? Math.max(0, Math.round(Date.now() / 1000 - snapshot.fetched_at)) : null;

  return (
    <div className="page-grid">
      <Card title="Hyperliquid Large Trades" full>
        <p className="regime-desc">
          <Waves size={13} /> Read-only monitoring of large prints on Hyperliquid's public BTC/ETH perpetual markets,
          sampled live from Hyperliquid's official public WebSocket (<code>wss://api.hyperliquid.xyz/ws</code>) -
          no scraping, no trading actions here.
        </p>
        <div className="controls" style={{ marginTop: 14 }}>
          <label className="filter-select-wrap">
            <span className="tile-label">Coins</span>
            <select className="filter-select" value={coins} onChange={(e) => setCoins(e.target.value as typeof coins)}>
              <option value="BTC,ETH">BTC + ETH</option>
              <option value="BTC">BTC only</option>
              <option value="ETH">ETH only</option>
            </select>
          </label>
          <label className="filter-select-wrap">
            <span className="tile-label">Minimum trade size</span>
            <select className="filter-select" value={minNotional} onChange={(e) => setMinNotional(Number(e.target.value))}>
              {NOTIONAL_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {fmtUsd(n, 0)}+
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {unavailable && (
        <Card title="Unavailable" full>
          <div className="regime-focus blocked">
            <span className="tile-label">Hyperliquid feed unavailable</span>
            <p className="regime-desc">{snapshot?.error || "Could not reach the Hyperliquid public WebSocket."}</p>
          </div>
        </Card>
      )}

      {!unavailable && (
        <>
          <div className="liq-heatmap-stats-row" style={{ marginBottom: -6 }}>
            <div className="liq-stat">
              <span className="tile-label">Large Trades ({snapshot?.sample_window_seconds ?? 0}s window)</span>
              <b>{trades.length}</b>
            </div>
            <div className="liq-stat">
              <span className="tile-label">Buy Prints</span>
              <b className="green">{buyCount}</b>
            </div>
            <div className="liq-stat">
              <span className="tile-label">Sell Prints</span>
              <b className="red">{sellCount}</b>
            </div>
            <div className="liq-stat">
              <span className="tile-label">Total Notional</span>
              <b>{fmtUsd(totalNotional, 0)}</b>
            </div>
            <div className="liq-stat">
              <span className="tile-label">Net Flow</span>
              <b className={netFlow >= 0 ? "green" : "red"}>{fmtUsd(netFlow, 0)}</b>
            </div>
          </div>

          <Card
            title="Trade Flow"
            full
            right={
              <span className="tile-label">
                <RefreshCw size={12} /> {loading ? "Refreshing…" : fetchedAgoSeconds != null ? `Updated ${fetchedAgoSeconds}s ago` : ""}
              </span>
            }
          >
            <div style={{ height: 220 }}>
              {chartData.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} margin={{ top: 8, right: 10, bottom: 2, left: -15 }}>
                    <XAxis dataKey="label" minTickGap={45} tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)" }} />
                    <Bar dataKey="notional" isAnimationActive={false}>
                      {chartData.map((d, i) => (
                        <Cell key={i} fill={d.side === "BUY" ? "var(--c-green)" : "var(--c-red)"} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p className="analytics-empty">No large trades observed in the current window.</p>
              )}
            </div>
          </Card>

          <Card title="Recent Large Trades" full>
            {trades.length === 0 ? (
              <p className="analytics-empty">No large trades observed in the current window.</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Coin</th>
                      <th>Side</th>
                      <th>Price</th>
                      <th>Size</th>
                      <th>Notional</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map((t) => (
                      <tr key={`${t.hash ?? t.trade_id}-${t.time}`}>
                        <td>{fmtTime(t.time)}</td>
                        <td>
                          <b>{t.coin}</b>
                        </td>
                        <td>
                          <span className={t.side === "BUY" ? "green" : "red"}>{t.side}</span>
                        </td>
                        <td>{fmtUsd(t.price)}</td>
                        <td>{t.size}</td>
                        <td>
                          <span className={t.side === "BUY" ? "green" : "red"}>{fmtUsd(t.notional, 0)}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
