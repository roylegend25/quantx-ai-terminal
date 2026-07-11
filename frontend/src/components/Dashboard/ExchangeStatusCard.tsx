import { memo } from "react";
import { CheckCircle2, Lock, ShieldAlert, ShieldCheck, XCircle } from "lucide-react";
import { fmtNum, fmtPct, fmtUsd } from "../../lib/format";
import AutoCardTable, { type AutoCardColumn } from "../Responsive/AutoCardTable";

const BALANCE_COLUMNS: AutoCardColumn<any>[] = [
  { key: "asset", label: "Asset", render: (b) => <b>{b.asset}</b> },
  { key: "balance", label: "Balance", render: (b) => fmtNum(b.balance, 4) },
  { key: "available", label: "Available", render: (b) => fmtNum(b.available, 4) },
];

const POSITION_COLUMNS: AutoCardColumn<any>[] = [
  { key: "symbol", label: "Symbol", render: (p) => <b>{p.symbol}</b> },
  { key: "side", label: "Side", render: (p) => <span className={p.side === "LONG" ? "green" : "red"}>{p.side}</span> },
  { key: "size", label: "Size", render: (p) => fmtNum(p.size, 4) },
  { key: "entry", label: "Entry", render: (p) => fmtNum(p.entry_price, 2) },
  { key: "mark", label: "Mark", render: (p) => fmtNum(p.mark_price, 2) },
  { key: "pnl", label: "Unrealized PnL", render: (p) => <span className={p.unrealized_pnl >= 0 ? "green" : "red"}>{fmtUsd(p.unrealized_pnl)}</span> },
];

const ORDER_COLUMNS: AutoCardColumn<any>[] = [
  { key: "symbol", label: "Symbol", render: (o) => <b>{o.symbol}</b> },
  { key: "side", label: "Side", render: (o) => <span className={o.side === "BUY" || o.side === "LONG" ? "green" : "red"}>{o.side}</span> },
  { key: "type", label: "Type", render: (o) => o.type },
  { key: "price", label: "Price", render: (o) => (o.price != null ? fmtNum(o.price, 2) : "—") },
  { key: "qty", label: "Qty", render: (o) => (o.qty != null ? fmtNum(o.qty, 4) : "—") },
  { key: "status", label: "Status", render: (o) => o.status },
];

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
        <AutoCardTable
          columns={BALANCE_COLUMNS}
          rows={exchangeBalances}
          keyField={(b) => b.asset}
          titleColumn="asset"
          emptyMessage="No balances - connect an exchange with read-only API keys."
        />
      </div>

      <div className="analytics-section">
        <span className="tile-label">Positions{primary ? ` (${primary})` : ""}</span>
        <AutoCardTable
          columns={POSITION_COLUMNS}
          rows={exchangePositions}
          keyField={(p) => p.symbol}
          titleColumn="symbol"
          statusColumn="side"
          emptyMessage="No open exchange positions."
        />
      </div>

      <div className="analytics-section">
        <span className="tile-label">Open Orders{primary ? ` (${primary})` : ""}</span>
        <AutoCardTable
          columns={ORDER_COLUMNS}
          rows={exchangeOpenOrders}
          keyField={(o) => o.order_id}
          titleColumn="symbol"
          statusColumn="status"
          emptyMessage="No open orders."
        />
      </div>
    </div>
  );
}

export default memo(ExchangeStatusCard);
