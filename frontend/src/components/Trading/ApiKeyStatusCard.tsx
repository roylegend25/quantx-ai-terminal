import { useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import Card from "../Layout/Card";
import type { TradingStatus } from "./TradingShared";

type Props = {
  status: TradingStatus | null;
  onRefresh: () => Promise<unknown>;
};

/** API Key Status (item 7): configured/connected status only - never the
 *  keys themselves. Distinguishes three things that used to be conflated
 *  into one misleading "Account Connected" field: key configuration,
 *  public market reachability, and the actual signed account read. Every
 *  field here comes straight off the shared TradingStatus (GET
 *  /api/trading/mode, app.api.exchange.binance_connectivity) - no
 *  independent connectivity calculation. */
export default function ApiKeyStatusCard({ status, onRefresh }: Props) {
  const [refreshing, setRefreshing] = useState(false);

  const keyConfigured = status?.binance_api_key_configured ?? status?.binance_configured;
  const secretConfigured = status?.binance_api_secret_configured ?? status?.binance_configured;
  const publicConnected = status?.binance_public_connected;
  const signedReadOk = status?.binance_signed_read_ok ?? status?.binance_account_connected ?? status?.binance_connected;
  const accountConnected = status?.binance_account_connected ?? status?.binance_connected;
  const accountError = status?.binance_account_error;

  const refresh = async () => {
    setRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <Card
      title="API Key Status"
      right={
        <button className="mini-btn" disabled={refreshing} onClick={refresh}>
          <RefreshCw size={13} /> {refreshing ? "Refreshing…" : "Refresh Binance Status"}
        </button>
      }
    >
      <div className="kv-grid">
        <div>
          <span className="tile-label">Binance API Key</span>
          <b className={`tile-value ${keyConfigured ? "green" : "red"}`}>{keyConfigured ? "Configured" : "Missing"}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Binance API Secret</span>
          <b className={`tile-value ${secretConfigured ? "green" : "red"}`}>{secretConfigured ? "Configured" : "Missing"}</b>
        </div>
        <div>
          <span className="tile-label">Public Market Data</span>
          <b className={`tile-value ${publicConnected ? "green" : "red"}`}>{publicConnected ? "Connected" : "Failed"}</b>
        </div>
        <div className="align-right">
          <span className="tile-label">Signed Account Read</span>
          <b className={`tile-value ${signedReadOk ? "green" : "red"}`}>{signedReadOk ? "Connected" : "Failed"}</b>
        </div>
        <div>
          <span className="tile-label">Account Connected</span>
          <b className={`tile-value ${accountConnected ? "green" : "red"}`}>{accountConnected ? "Yes" : "No"}</b>
        </div>
      </div>
      {!accountConnected && accountError && (
        <p className="regime-desc" style={{ marginTop: 12 }}>
          <AlertTriangle size={13} /> {accountError}
        </p>
      )}
      <p className="regime-desc" style={{ marginTop: 12 }}>
        API keys are configured on the server .env only and are never readable or displayed through this UI — this
        card shows configured/connected status only.
      </p>
    </Card>
  );
}
