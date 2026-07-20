import { useEffect, useState } from "react";
import { Eye, EyeOff, ShieldCheck, AlertTriangle, Trash2, PlugZap } from "lucide-react";
import Card from "../Layout/Card";
import { api } from "../../services/api";
import { fmtLocalDateTime } from "../../lib/format";

type CredentialStatus = {
  configured: boolean;
  label: string | null;
  environment: string | null;
  api_key_fingerprint: string | null;
  created_at: string | null;
  created_by: string | null;
  updated_at: string | null;
  last_validated_at: string | null;
  last_validation_status: "ok" | "failed" | null;
  last_validation_detail: string | null;
  write_permission_detected: boolean | null;
  withdraw_enabled_detected: boolean | null;
  credential_source: "database_encrypted" | "none";
  encryption_store_configured: boolean;
  execution_mode: string;
  message: string | null;
};

type Props = {
  showToast: (message: string, tone?: "success" | "error") => void;
};

/** Phase 32: admin-only Binance Real API credential management, directly
 *  from the Binance Real tab - replaces the old ".env only" instruction.
 *  Saving/testing/deleting a credential here NEVER changes execution mode,
 *  the live-authorization lease, or the scheduler - see TradingShared's
 *  AutomaticExecutionBanner and UserLiveConfirmationCard for the
 *  independent gates that actually control live order routing. */
export default function BinanceCredentialCard({ showToast }: Props) {
  const [status, setStatus] = useState<CredentialStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [label, setLabel] = useState("");
  const [environment, setEnvironment] = useState<"live" | "testnet">("live");
  const [password, setPassword] = useState("");
  const [revealKey, setRevealKey] = useState(false);
  const [revealSecret, setRevealSecret] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string | null } | null>(null);

  const load = async () => {
    try {
      const s = await api.binanceCredentialStatus();
      setStatus(s);
    } catch (e: any) {
      showToast(e?.message || "Failed to load credential status", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const clearForm = () => {
    setApiKey("");
    setApiSecret("");
    setPassword("");
    setLabel("");
    setRevealKey(false);
    setRevealSecret(false);
  };

  const save = async () => {
    if (!apiKey.trim() || !apiSecret.trim() || !password) {
      showToast("API key, API secret, and your password are all required", "error");
      return;
    }
    setBusy(true);
    try {
      const r = await api.saveBinanceCredential({
        password,
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        label: label.trim() || undefined,
        environment,
      });
      showToast(r.message || "Credentials stored securely — execution remains PAPER", "success");
      clearForm();
      setShowForm(false);
      setTestResult(null);
      await load();
    } catch (e: any) {
      showToast(e?.message || "Failed to save credentials", "error");
    } finally {
      // Always clear the secret fields, success or failure - never leave a
      // submitted secret sitting in component state.
      setApiSecret("");
      setPassword("");
      setBusy(false);
    }
  };

  const testConnection = async () => {
    setBusy(true);
    setTestResult(null);
    try {
      const r = await api.testBinanceCredential();
      setTestResult({ ok: r.ok, detail: r.detail });
      showToast(r.ok ? "Signed read-only connection succeeded" : `Connection test failed: ${r.detail}`, r.ok ? "success" : "error");
      await load();
    } catch (e: any) {
      showToast(e?.message || "Connection test failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const deleteCredential = async () => {
    if (!deletePassword) {
      showToast("Your password is required to delete stored credentials", "error");
      return;
    }
    setBusy(true);
    try {
      await api.deleteBinanceCredential(deletePassword);
      showToast("Stored credential deleted. Live routing was not affected.", "success");
      setShowDeleteConfirm(false);
      setTestResult(null);
      await load();
    } catch (e: any) {
      showToast(e?.message || "Failed to delete credentials", "error");
    } finally {
      setDeletePassword("");
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <Card title="Binance Real API Credentials">
        <p className="regime-desc">Loading credential status…</p>
      </Card>
    );
  }

  return (
    <Card title="Binance Real API Credentials">
      <p className="regime-desc">
        <ShieldCheck size={13} style={{ verticalAlign: "-2px" }} /> Credentials stored securely — execution remains PAPER.
        Saving, testing, or deleting a key here never changes the execution mode or places an order.
      </p>

      <div className="controls" style={{ marginTop: 10, marginBottom: 10 }}>
        <span className={`badge ${status?.configured ? "badge-green" : ""}`}>
          {status?.configured ? "Credentials Present" : "Credentials Not Configured"}
        </span>
        <span className={`badge ${status?.last_validation_status === "ok" ? "badge-green" : status?.last_validation_status === "failed" ? "badge-red" : ""}`}>
          Signed Read: {status?.last_validation_status === "ok" ? "Working" : status?.last_validation_status === "failed" ? "Failed" : "Not Tested"}
        </span>
        {status?.write_permission_detected !== null && status?.write_permission_detected !== undefined && (
          <span className={`badge ${status.write_permission_detected ? "badge-orange" : ""}`}>
            Write Permission: {status.write_permission_detected ? "Detected" : "Not Detected"}
          </span>
        )}
        {status?.withdraw_enabled_detected === true && (
          <span className="badge badge-red">
            <AlertTriangle size={11} style={{ verticalAlign: "-1px" }} /> Withdrawals Enabled — Disable Immediately
          </span>
        )}
      </div>

      {status?.configured && (
        <div className="dec-kv-row" style={{ marginBottom: 10 }}>
          <div>
            <span className="tile-label">Fingerprint</span>
            <b className="tile-value dec-small">{status.api_key_fingerprint}</b>
          </div>
          <div>
            <span className="tile-label">Label</span>
            <b className="tile-value dec-small">{status.label || "—"}</b>
          </div>
          <div>
            <span className="tile-label">Expected Environment</span>
            <b className="tile-value dec-small">{status.environment === "live" ? "Live (Real Money)" : "Testnet"}</b>
          </div>
          <div>
            <span className="tile-label">Credential Source</span>
            <b className="tile-value dec-small">Encrypted Database</b>
          </div>
          <div>
            <span className="tile-label">Last Validated</span>
            <b className="tile-value dec-small">{status.last_validated_at ? fmtLocalDateTime(new Date(status.last_validated_at).getTime()) : "Never"}</b>
          </div>
          <div>
            <span className="tile-label">Saved</span>
            <b className="tile-value dec-small">{status.created_at ? fmtLocalDateTime(new Date(status.created_at).getTime()) : "—"}</b>
          </div>
        </div>
      )}

      {testResult && (
        <p className={`regime-desc ${testResult.ok ? "" : "risk-modal-error"}`}>
          {testResult.ok ? <ShieldCheck size={13} /> : <AlertTriangle size={13} />} {testResult.detail}
        </p>
      )}

      <div className="controls" style={{ marginBottom: showForm ? 10 : 0 }}>
        <button className="mini-btn" disabled={busy} onClick={() => setShowForm((v) => !v)}>
          {status?.configured ? "Rotate Credentials" : "Save Credentials"}
        </button>
        {status?.configured && (
          <button className="mini-btn" disabled={busy} onClick={testConnection}>
            <PlugZap size={13} /> Test Read-Only Connection
          </button>
        )}
        {status?.configured && (
          <button className="mini-btn" disabled={busy} onClick={() => setShowDeleteConfirm((v) => !v)}>
            <Trash2 size={13} /> Delete / Revoke
          </button>
        )}
      </div>

      {showForm && (
        <div className="live-unlock-panel" style={{ marginTop: 10 }}>
          {!status?.encryption_store_configured && (
            <p className="risk-modal-error">
              <AlertTriangle size={14} /> Credential encryption is not configured on the server. Saving is disabled
              until CREDENTIAL_ENCRYPTION_KEY is set.
            </p>
          )}
          <label className="live-unlock-phrase">
            <span className="tile-label">Binance Real API Key</span>
            <div className="controls" style={{ gap: 6 }}>
              <input
                type={revealKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                style={{ flex: 1 }}
              />
              <button type="button" className="mini-btn" onClick={() => setRevealKey((v) => !v)} title="Reveal while typing">
                {revealKey ? <EyeOff size={13} /> : <Eye size={13} />}
              </button>
            </div>
          </label>
          <label className="live-unlock-phrase">
            <span className="tile-label">Binance Real API Secret</span>
            <div className="controls" style={{ gap: 6 }}>
              <input
                type={revealSecret ? "text" : "password"}
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                style={{ flex: 1 }}
              />
              <button type="button" className="mini-btn" onClick={() => setRevealSecret((v) => !v)} title="Reveal while typing">
                {revealSecret ? <EyeOff size={13} /> : <Eye size={13} />}
              </button>
            </div>
          </label>
          <label className="live-unlock-phrase">
            <span className="tile-label">Account Label (optional)</span>
            <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} autoComplete="off" placeholder="e.g. Main Futures Account" />
          </label>
          <label className="live-unlock-phrase">
            <span className="tile-label">Expected Exchange Environment</span>
            <select value={environment} onChange={(e) => setEnvironment(e.target.value as "live" | "testnet")}>
              <option value="live">Live (Real Money)</option>
              <option value="testnet">Testnet</option>
            </select>
          </label>
          <p className="regime-desc">
            <AlertTriangle size={12} style={{ verticalAlign: "-1px" }} /> Reminder: restrict this API key to this
            server's IP address on Binance, and disable withdrawal permission entirely.
          </p>
          <label className="live-unlock-phrase">
            <span className="tile-label">Re-enter your password to confirm it's really you, right now</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>
          <div className="modal-actions">
            <button
              className="mini-btn"
              disabled={busy}
              onClick={() => {
                clearForm();
                setShowForm(false);
              }}
            >
              Cancel
            </button>
            <button className="btn-long" disabled={busy || !status?.encryption_store_configured} onClick={save}>
              {busy ? "Saving…" : "Save Credentials"}
            </button>
          </div>
        </div>
      )}

      {showDeleteConfirm && (
        <div className="live-unlock-panel" style={{ marginTop: 10 }}>
          <p className="risk-modal-error">
            <AlertTriangle size={14} /> This permanently deletes the stored credential. It does not affect execution
            mode or the live-authorization lease.
          </p>
          <label className="live-unlock-phrase">
            <span className="tile-label">Re-enter your password to confirm deletion</span>
            <input type="password" value={deletePassword} onChange={(e) => setDeletePassword(e.target.value)} autoComplete="current-password" />
          </label>
          <div className="modal-actions">
            <button className="mini-btn" disabled={busy} onClick={() => { setShowDeleteConfirm(false); setDeletePassword(""); }}>
              Cancel
            </button>
            <button className="btn-danger" disabled={busy} onClick={deleteCredential}>
              {busy ? "Deleting…" : "Confirm Delete"}
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}
