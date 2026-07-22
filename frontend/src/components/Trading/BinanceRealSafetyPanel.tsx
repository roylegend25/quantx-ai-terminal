import { useEffect, useState } from "react";
import Card from "../Layout/Card";
import { api } from "../../services/api";

/** Read-only safety section for the Binance Real Bot Settings tab (Part 1):
 *  live execution status, server live lock, maintenance status, Binance
 *  auth status, current real positions/open orders. Composed entirely from
 *  GET /api/risk/settings/binance-safety - performs no writes and cannot
 *  influence live-execution state. */

type Safety = {
  live_execution_status: string;
  server_live_lock_enabled: boolean;
  kill_switch_active: boolean;
  maintenance: { enabled: boolean; marker_present: boolean; reason: string | null };
  binance_authenticated: boolean;
  binance_unavailable_reason: string | null;
  current_real_positions: { symbol: string; side: string; quantity: number; entry_price: number; mark_price: number; unrealized_pnl: number }[];
  current_real_open_orders: { symbol: string; side: string; type: string; quantity: number; price: number }[];
};

export default function BinanceRealSafetyPanel() {
  const [data, setData] = useState<Safety | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .riskSettingsBinanceSafety()
      .then((res: Safety) => {
        if (!cancelled) setData(res);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card title="Binance Real Safety Status (read-only)" full>
      {loading ? (
        <p className="regime-desc">Loading safety status…</p>
      ) : !data ? (
        <p className="regime-desc">Safety status unavailable.</p>
      ) : (
        <>
          <div className="kv-grid">
            <div>
              <span className="tile-label">Live Execution Status</span>
              <b className={`tile-value ${data.live_execution_status === "BINANCE_LIVE" ? "red" : "green"}`}>
                {data.live_execution_status}
              </b>
            </div>
            <div className="align-right">
              <span className="tile-label">Server Live Lock</span>
              <b className={`tile-value ${data.server_live_lock_enabled ? "red" : "green"}`}>
                {data.server_live_lock_enabled ? "UNLOCKED" : "ENGAGED"}
              </b>
            </div>
            <div>
              <span className="tile-label">Kill Switch</span>
              <b className={`tile-value ${data.kill_switch_active ? "red" : "green"}`}>
                {data.kill_switch_active ? "ACTIVE" : "OFF"}
              </b>
            </div>
            <div className="align-right">
              <span className="tile-label">Maintenance</span>
              <b className={`tile-value ${data.maintenance.enabled ? "yellow" : "green"}`}>
                {data.maintenance.enabled ? "ENABLED" : "OFF"}
              </b>
            </div>
            <div>
              <span className="tile-label">Binance Authenticated</span>
              <b className={`tile-value ${data.binance_authenticated ? "green" : "red"}`}>
                {data.binance_authenticated ? "YES" : "NO"}
              </b>
            </div>
          </div>

          <p className="regime-desc" style={{ marginTop: 14 }}>
            Current real positions
          </p>
          {data.current_real_positions.length === 0 ? (
            <p className="regime-desc">None.</p>
          ) : (
            <table className="simple-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Qty</th>
                  <th>Entry</th>
                  <th>Mark</th>
                  <th>Unrealized PnL</th>
                </tr>
              </thead>
              <tbody>
                {data.current_real_positions.map((p, i) => (
                  <tr key={i}>
                    <td>{p.symbol}</td>
                    <td>{p.side}</td>
                    <td>{p.quantity}</td>
                    <td>{p.entry_price}</td>
                    <td>{p.mark_price}</td>
                    <td>{p.unrealized_pnl}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <p className="regime-desc" style={{ marginTop: 14 }}>
            Current real open orders
          </p>
          {data.current_real_open_orders.length === 0 ? (
            <p className="regime-desc">None.</p>
          ) : (
            <table className="simple-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Type</th>
                  <th>Qty</th>
                  <th>Price</th>
                </tr>
              </thead>
              <tbody>
                {data.current_real_open_orders.map((o, i) => (
                  <tr key={i}>
                    <td>{o.symbol}</td>
                    <td>{o.side}</td>
                    <td>{o.type}</td>
                    <td>{o.quantity}</td>
                    <td>{o.price}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </Card>
  );
}
