import { memo } from "react";
import { CheckCircle2, Lock, ShieldAlert, ShieldCheck, XCircle } from "lucide-react";
import { fmtNum, fmtPct, fmtUsd } from "../../lib/format";

type Props = {
  exchangeStatus: any;
  exchangeRiskCheck: any;
  exchangeBalances: any[];
  exchangePositions: any[];
  exchangeOpenOrders: any[];
};

function ConnectionPill({ connected, configured }: { connected: boolean; configured: boolean }) {
  if (!configured) {
    return (
      <span className="chip">
        <XCircle size={14} /> Not configured
      </span>
    );
  }
  return connected ? (
    <span className="chip">
      <CheckCircle2 size={14} className="green" /> Connected
    </span>
  ) : (
    <span className="chip">
      <XCircle size={14} className="red" /> Disconnected
    </span>
  );
}

function ExchangeStatusCard({
  exchangeStatus,
  exchangeRiskCheck,
  exchangeBalances,
  exchangePositions,
  exchangeOpenOrders,
}: Props) {
  const exchanges: Record<string, any> = exchangeStatus?.exchanges || {};
  const names = Object.keys(exchanges);
  const readOnly = exchangeStatus?.read_only ?? exchangeRiskCheck?.read_only_flag;
  const riskExchanges: Record<string, any> = exchangeRiskCheck?.exchanges || {};

  const primary = names.find((n) => exchanges[n]?.connected);
  const funding = primary ? exchanges[primary]?.funding : null;

  return (
    <div>
      <div className="analytics-grid">
        <div className="analytics-tile status-tile">
          <span className="tile-label">Read-Only Mode</span>
          <b className={`tile-value ${readOnly ? "green" : "red"}`} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {readOnly ? <Lock size={16} /> : <ShieldAlert size={16} />}
            {readOnly ? "Enforced" : "DISABLED"}
          </b>
        </div>
        <div className="analytics-tile status-tile">
          <span className="tile-label">API Health</span>
          <b className="tile-value" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {names.length === 0 ? (
              "—"
            ) : names.some((n) => exchanges[n]?.connected) ? (
              <>
                <CheckCircle2 size={16} className="green" /> Reachable
              </>
            ) : (
              <>
                <XCircle size={16} className="red" /> No connections
              </>
            )}
          </b>
        </div>
        <div className="analytics-tile status-tile">
          <span className="tile-label">Permission Check</span>
          <b
            className={`tile-value ${exchangeRiskCheck ? (exchangeRiskCheck.safe ? "green" : "red") : ""}`}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            {exchangeRiskCheck ? (
              exchangeRiskCheck.safe ? (
                <>
                  <ShieldCheck size={16} /> No withdrawal keys
                </>
              ) : (
                <>
                  <ShieldAlert size={16} /> Withdrawal permission detected
                </>
              )
            ) : (
              "—"
            )}
          </b>
        </div>
        <div className="analytics-tile status-tile">
          <span className="tile-label">Funding Rate</span>
          <b className="tile-value">
            {funding?.funding_rate != null ? `${fmtPct(funding.funding_rate * 100, 4)} (${funding.symbol})` : "—"}
          </b>
        </div>
      </div>

      <div className="indicators-row">
        <span className="tile-label">Exchanges</span>
        <div className="chip-row">
          {names.length === 0 && <span className="analytics-empty">No exchange adapters available.</span>}
          {names.map((n) => (
            <span className="chip" key={n} style={{ textTransform: "capitalize" }}>
              {n}
              <ConnectionPill connected={!!exchanges[n]?.connected} configured={!!exchanges[n]?.configured} />
            </span>
          ))}
        </div>
      </div>

      {riskExchanges && Object.keys(riskExchanges).length > 0 && (
        <div className="indicators-row">
          <span className="tile-label">Key Permissions</span>
          <div className="chip-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
            {Object.entries(riskExchanges).map(([name, r]: [string, any]) => {
              if (!r.configured) return null;
              return (
                <div
                  key={name}
                  className={`regime-focus ${r.safe === false ? "blocked" : "allowed"}`}
                  style={{ display: "flex", alignItems: "center", gap: 10, textTransform: "capitalize" }}
                >
                  {r.safe === false ? <ShieldAlert size={16} className="red" /> : <ShieldCheck size={16} className="green" />}
                  <span className="regime-desc" style={{ margin: 0 }}>
                    {name}: {r.detectable ? (r.withdraw_enabled ? "withdrawal permission detected - key rejected" : "read-only confirmed") : (r.note || "permission scope not detectable - verify manually")}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="analytics-section">
        <span className="tile-label">Balances{primary ? ` (${primary})` : ""}</span>
        {exchangeBalances.length === 0 ? (
          <p className="analytics-empty">No balances - connect an exchange with read-only API keys.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Balance</th>
                  <th>Available</th>
                </tr>
              </thead>
              <tbody>
                {exchangeBalances.map((b) => (
                  <tr key={b.asset}>
                    <td>
                      <b>{b.asset}</b>
                    </td>
                    <td>{fmtNum(b.balance, 4)}</td>
                    <td>{fmtNum(b.available, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="analytics-section">
        <span className="tile-label">Positions{primary ? ` (${primary})` : ""}</span>
        {exchangePositions.length === 0 ? (
          <p className="analytics-empty">No open exchange positions.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Size</th>
                  <th>Entry</th>
                  <th>Mark</th>
                  <th>Unrealized PnL</th>
                </tr>
              </thead>
              <tbody>
                {exchangePositions.map((p) => (
                  <tr key={p.symbol}>
                    <td>
                      <b>{p.symbol}</b>
                    </td>
                    <td className={p.side === "LONG" ? "green" : "red"}>{p.side}</td>
                    <td>{fmtNum(p.size, 4)}</td>
                    <td>{fmtNum(p.entry_price, 2)}</td>
                    <td>{fmtNum(p.mark_price, 2)}</td>
                    <td className={p.unrealized_pnl >= 0 ? "green" : "red"}>{fmtUsd(p.unrealized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="analytics-section">
        <span className="tile-label">Open Orders{primary ? ` (${primary})` : ""}</span>
        {exchangeOpenOrders.length === 0 ? (
          <p className="analytics-empty">No open orders.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Type</th>
                  <th>Price</th>
                  <th>Qty</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {exchangeOpenOrders.map((o) => (
                  <tr key={o.order_id}>
                    <td>
                      <b>{o.symbol}</b>
                    </td>
                    <td className={o.side === "BUY" || o.side === "LONG" ? "green" : "red"}>{o.side}</td>
                    <td>{o.type}</td>
                    <td>{o.price != null ? fmtNum(o.price, 2) : "—"}</td>
                    <td>{o.qty != null ? fmtNum(o.qty, 4) : "—"}</td>
                    <td>{o.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(ExchangeStatusCard);
