import { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw, Stethoscope } from "lucide-react";
import Card from "../components/Layout/Card";
import { fmtLocalDateTime } from "../lib/format";
import { api } from "../services/api";
import type { AppData } from "../hooks/useAppData";

function na(value: unknown, render: (v: any) => string = String): string {
  return value === null || value === undefined ? "Not available" : render(value);
}

function boolLabel(v: unknown): string {
  if (v === true) return "Yes";
  if (v === false) return "No";
  return "Not available";
}

export default function TradingDiagnosticsPage(props: AppData) {
  const { showToast } = props;
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [errored, setErrored] = useState(false);
  const [testBusy, setTestBusy] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);

  async function reload() {
    setLoading(true);
    try {
      const r = await api.binanceTradingDiagnostics();
      setData(r);
      setErrored(false);
    } catch {
      setErrored(true);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleTestOrder() {
    const symbol = data?.order_types?.symbol || "BTCUSDT";
    setTestBusy(true);
    try {
      const r = await api.binanceTestStopOrder({ symbol, side: "SELL" });
      setTestResult(r);
      showToast(
        r.ok ? "Test validation passed" : `Test validation rejected: code ${r.code}`,
        r.ok ? "success" : "error"
      );
    } catch (e: any) {
      showToast(e?.message || "Test order validation failed", "error");
      setTestResult({ error: e?.message || "Request failed" });
    } finally {
      setTestBusy(false);
    }
  }

  const account = data?.account;
  const orderTypes = data?.order_types;
  const lastAttempt = data?.last_protective_attempt;
  const providerInfo = data?.protection_provider;

  return (
    <div className="page-grid">
      <Card title="Trading Diagnostics" full>
        <p className="regime-desc">
          <Stethoscope size={13} /> Read-only investigation of the live Binance account's real capabilities - every
          value below is read directly from Binance or from this app's own stored attempt history. Nothing here
          places a real order (the Test Order Validation button uses Binance's own no-op{" "}
          <code>/fapi/v1/order/test</code> endpoint, which never reaches the matching engine).
        </p>
        <div className="controls" style={{ marginTop: 10 }}>
          <button className="mini-btn" disabled={loading} onClick={reload}>
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
      </Card>

      {(errored || (!loading && !data?.available)) && (
        <Card title="Unavailable" full>
          <div className="regime-focus blocked">
            <span className="tile-label">Diagnostics unavailable</span>
            <p className="regime-desc">{data?.reason || "Could not read the Binance account."}</p>
          </div>
        </Card>
      )}

      {data?.available && (
        <>
          <Card title="Futures Account Type &amp; Mode" full>
            <div className="kv-grid">
              <div><span className="tile-label">Market</span><b className="tile-value">{na(account?.market)}</b></div>
              <div className="align-right"><span className="tile-label">Environment</span><b className={`tile-value ${account?.environment === "PRODUCTION" ? "red" : "green"}`}>{na(account?.environment)}</b></div>
              <div><span className="tile-label">Position Mode</span><b className="tile-value">{na(account?.position_mode)}</b></div>
              <div className="align-right"><span className="tile-label">Multi-Assets Margin</span><b className="tile-value">{boolLabel(account?.multi_assets_margin)}</b></div>
              <div><span className="tile-label">Can Trade</span><b className="tile-value">{boolLabel(account?.account_fields?.can_trade)}</b></div>
              <div className="align-right"><span className="tile-label">Fee Tier</span><b className="tile-value">{na(account?.account_fields?.fee_tier)}</b></div>
              <div><span className="tile-label">Trade Group ID</span><b className="tile-value">{na(account?.account_fields?.trade_group_id)}</b></div>
              <div className="align-right"><span className="tile-label">Margin Mode (per position)</span>
                <b className="tile-value">
                  {account?.margin_mode_per_position?.length
                    ? account.margin_mode_per_position.map((m: any) => `${m.symbol}: ${m.margin_type}`).join(", ")
                    : "No open positions"}
                </b>
              </div>
            </div>
            {account?.account_fields?.trade_group_id_interpretation && (
              <p className="regime-desc" style={{ marginTop: 10 }}>{account.account_fields.trade_group_id_interpretation}</p>
            )}
            {account?.portfolio_margin_indirect_evidence && (
              <p className="regime-desc">{account.portfolio_margin_indirect_evidence}</p>
            )}
            {(account?.account_fields_error || account?.position_mode_error || account?.multi_assets_margin_error) && (
              <p className="regime-desc"><AlertTriangle size={12} /> Some fields unavailable: {[account?.account_fields_error, account?.position_mode_error, account?.multi_assets_margin_error].filter(Boolean).join("; ")}</p>
            )}
          </Card>

          <Card title="API Permissions" full>
            {account?.api_permissions ? (
              <pre className="exec-raw-response">{JSON.stringify(account.api_permissions, null, 2)}</pre>
            ) : (
              <p className="regime-desc">Not available{account?.api_permissions_error ? ` — ${account.api_permissions_error}` : ""}</p>
            )}
          </Card>

          <Card title="Supported Order Types &amp; Endpoints" full>
            <div className="kv-grid">
              <div style={{ gridColumn: "1 / -1" }}>
                <span className="tile-label">Order Types per Binance exchangeInfo ({orderTypes?.symbol})</span>
                <b className="tile-value">{orderTypes?.order_types_per_exchange_info?.join(", ") || "Not available"}</b>
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <span className="tile-label">Entry Endpoint (this app)</span>
                <b className="tile-value green">{na(orderTypes?.entry_endpoint_used_by_this_app)}</b>
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <span className="tile-label">Protective Endpoint (this app)</span>
                <b className="tile-value red">{na(orderTypes?.protective_endpoint_used_by_this_app)}</b>
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <span className="tile-label">Documented Algo Endpoint (Binance)</span>
                <b className="tile-value orange">{na(orderTypes?.documented_algo_endpoint)}</b>
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <span className="tile-label">Algo API Reachable from this Key (GET /fapi/v1/algoOrder probe)</span>
                <b className={`tile-value ${orderTypes?.algo_api_reachable ? "green" : "red"}`}>{boolLabel(orderTypes?.algo_api_reachable)}</b>
              </div>
            </div>
            {orderTypes?.algo_api_evidence && (
              <p className="regime-desc">{orderTypes.algo_api_evidence}</p>
            )}
            {orderTypes?.algo_api_error && (
              <p className="regime-desc"><AlertTriangle size={12} /> Algo API probe error: {orderTypes.algo_api_error}</p>
            )}
          </Card>

          <Card title="Protection Provider" full>
            <p className="regime-desc">
              Which TP/SL order surface this account actually uses (Phase 26 Algo Order Provider) - detected once,
              per mode, the first time a classic protective order either succeeds or is rejected with{" "}
              <code>-4120 STOP_ORDER_SWITCH_ALGO</code>, then reused for every order after.
            </p>
            <div className="kv-grid" style={{ marginTop: 10 }}>
              <div>
                <span className="tile-label">Provider</span>
                <b className={`tile-value ${providerInfo?.provider === "algo" ? "orange" : providerInfo?.provider === "classic" ? "green" : ""}`}>
                  {na(providerInfo?.provider, (v) => String(v).toUpperCase())}
                </b>
              </div>
              <div className="align-right"><span className="tile-label">Detected At</span><b className="tile-value">{na(providerInfo?.capability?.detected_at, fmtLocalDateTime)}</b></div>
              <div style={{ gridColumn: "1 / -1" }}><span className="tile-label">Detection Reason</span><b className="tile-value">{na(providerInfo?.capability?.detection_reason)}</b></div>
              {providerInfo?.capability?.last_error && (
                <div style={{ gridColumn: "1 / -1" }}><span className="tile-label">Last Provider Error</span><b className="tile-value red">{providerInfo.capability.last_error}</b></div>
              )}
              <div><span className="tile-label">Last Provider Used</span><b className="tile-value">{na(providerInfo?.last_provider_used, (v) => String(v).toUpperCase())}</b></div>
              <div className="align-right"><span className="tile-label">Last Provider Latency</span><b className="tile-value">{providerInfo?.last_provider_latency_ms != null ? `${providerInfo.last_provider_latency_ms} ms` : "Not available"}</b></div>
              <div style={{ gridColumn: "1 / -1" }}><span className="tile-label">Last Verification Result (Protection Verified)</span><b className={`tile-value ${providerInfo?.last_verification_result === "PROTECTED" ? "green" : ""}`}>{na(providerInfo?.last_verification_result)}</b></div>
            </div>
            {providerInfo?.last_algo_response && (
              <>
                <span className="tile-label" style={{ marginTop: 10, display: "block" }}>Last Algo Response (POST /fapi/v1/algoOrder)</span>
                <pre className="exec-raw-response">{JSON.stringify(providerInfo.last_algo_response, null, 2)}</pre>
              </>
            )}
          </Card>

          <Card
            title="Last Protective Order Attempt"
            full
            right={
              <button className="mini-btn" disabled={testBusy} onClick={handleTestOrder}>
                {testBusy ? "Validating…" : "Run Test Order Validation"}
              </button>
            }
          >
            {lastAttempt?.available ? (
              <>
                <div className="kv-grid">
                  <div><span className="tile-label">Symbol</span><b className="tile-value">{na(lastAttempt.symbol)}</b></div>
                  <div className="align-right"><span className="tile-label">Side</span><b className="tile-value">{na(lastAttempt.side)}</b></div>
                  <div><span className="tile-label">When</span><b className="tile-value">{na(lastAttempt.created_at, fmtLocalDateTime)}</b></div>
                  <div className="align-right"><span className="tile-label">Final Status</span><b className={`tile-value ${lastAttempt.final_status === "order_accepted" ? "green" : "red"}`}>{na(lastAttempt.final_status)}</b></div>
                </div>
                <div className="regime-focus blocked" style={{ marginTop: 10 }}>
                  <span className="tile-label">Last TP Request</span>
                  <b className="tile-value">{lastAttempt.protective_tp_stage?.status || "Not available"}</b>
                  <p className="regime-desc">{lastAttempt.protective_tp_stage?.reason || "—"}</p>
                </div>
                <div className="regime-focus blocked" style={{ marginTop: 10 }}>
                  <span className="tile-label">Last SL Request</span>
                  <b className="tile-value">{lastAttempt.protective_sl_stage?.status || "Not available"}</b>
                  <p className="regime-desc">{lastAttempt.protective_sl_stage?.reason || "—"}</p>
                </div>
              </>
            ) : (
              <p className="regime-desc">{lastAttempt?.reason || "No protective order attempt recorded yet"}</p>
            )}

            {testResult && (
              <div className={`regime-focus ${testResult.ok ? "allowed" : "blocked"}`} style={{ marginTop: 14 }}>
                <span className="tile-label">Binance Response — POST /fapi/v1/order/test</span>
                {testResult.error ? (
                  <p className="regime-desc">{testResult.error}</p>
                ) : (
                  <>
                    <b className={`tile-value ${testResult.ok ? "green" : "red"}`}>
                      {testResult.ok ? "Validated OK" : `Rejected — code ${testResult.code}`}
                    </b>
                    <p className="regime-desc">{testResult.message || "No error message"}</p>
                    <pre className="exec-raw-response">{JSON.stringify({ request: testResult.request, response: testResult.response }, null, 2)}</pre>
                  </>
                )}
              </div>
            )}
          </Card>

          <Card title="Configuration" full>
            <div className="kv-grid">
              <div><span className="tile-label">Require TP/SL</span><b className="tile-value">{boolLabel(data?.config?.require_tp_sl)}</b></div>
              <div className="align-right"><span className="tile-label">Close If Protection Fails</span><b className="tile-value">{boolLabel(data?.config?.close_if_protection_fails)}</b></div>
              <div><span className="tile-label">Protection Watchdog Enabled</span><b className="tile-value">{boolLabel(data?.config?.watchdog_enabled)}</b></div>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
