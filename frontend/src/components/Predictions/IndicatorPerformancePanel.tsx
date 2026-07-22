import { useEffect, useMemo, useState } from "react";
import Card from "../Layout/Card";
import AutoCardTable, { type AutoCardColumn } from "../Responsive/AutoCardTable";
import { api } from "../../services/api";

/** Indicator Performance section for the Prediction Results page (Bot
 *  Settings Part 7): per-indicator status/active-vs-shadow performance,
 *  filters, and manual enable/disable actions. Manual "enable for Binance
 *  Real" never unlocks real execution - it only flips
 *  IndicatorEligibility(mode="binance_real").status, a completely separate
 *  gate from live-trading permission. */

type Performance = {
  sample_size: number;
  correct: number;
  wrong: number;
  neutral: number;
  wrong_rate: number | null;
  hit_rate: number | null;
  net_expectancy: number | null;
  last_10_outcomes: string[];
  data_quality_flag: boolean;
};

type IndicatorRow = {
  id: number;
  source_name: string;
  source_version: string;
  symbol: string;
  timeframe: string;
  mode: string;
  status: string;
  status_reason: string | null;
  last_status_change_at: string | null;
  starred: boolean;
  active_performance: Performance;
  shadow_performance: Performance;
  current_ensemble_influence: string;
};

const STATUS_LABELS: Record<string, string> = {
  ACTIVE: "Active",
  SHADOW_ONLY_POOR_PERFORMANCE: "Poor Performance",
  MANUALLY_DISABLED: "Manually Disabled",
  RECOMMENDED_FOR_REACTIVATION: "Recommended ⭐",
  INSUFFICIENT_SAMPLE: "Insufficient Sample",
  DATA_QUALITY_BLOCKED: "Data Quality Blocked",
};

const FILTERS = ["All", "Active", "Shadow Only", "Poor Performance", "Recommended ⭐", "Insufficient Sample"] as const;
type Filter = (typeof FILTERS)[number];

function filterMatches(filter: Filter, row: IndicatorRow): boolean {
  switch (filter) {
    case "All":
      return true;
    case "Active":
      return row.status === "ACTIVE";
    case "Shadow Only":
      return row.status === "SHADOW_ONLY_POOR_PERFORMANCE" || row.status === "RECOMMENDED_FOR_REACTIVATION";
    case "Poor Performance":
      return row.status === "SHADOW_ONLY_POOR_PERFORMANCE";
    case "Recommended ⭐":
      return row.status === "RECOMMENDED_FOR_REACTIVATION";
    case "Insufficient Sample":
      return row.status === "INSUFFICIENT_SAMPLE";
    default:
      return true;
  }
}

function ActionModal({
  row,
  onClose,
  onDone,
  showToast,
}: {
  row: IndicatorRow;
  onClose: () => void;
  onDone: () => void;
  showToast: (message: string, tone?: "success" | "error") => void;
}) {
  const [action, setAction] = useState("enable_paper");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<any>(null);

  useEffect(() => {
    api
      .indicatorHistory(row.id)
      .then(setHistory)
      .catch(() => setHistory(null));
  }, [row.id]);

  async function submit() {
    setBusy(true);
    try {
      await api.indicatorAction(row.id, action, reason || undefined);
      showToast("Indicator action applied", "success");
      onDone();
    } catch (e: any) {
      showToast(e?.message || "Failed to apply action", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <h3>
          {row.source_name} · {row.symbol} {row.timeframe}
        </h3>
        <p className="regime-desc">Current status: {STATUS_LABELS[row.status] ?? row.status}</p>
        {row.status_reason && <p className="regime-desc">Why removed: {row.status_reason}</p>}
        <p className="regime-desc">
          Active performance: {row.active_performance.sample_size} samples, hit rate{" "}
          {row.active_performance.hit_rate != null ? `${(row.active_performance.hit_rate * 100).toFixed(0)}%` : "—"}
        </p>
        <p className="regime-desc">
          Shadow performance: {row.shadow_performance.sample_size} samples, hit rate{" "}
          {row.shadow_performance.hit_rate != null ? `${(row.shadow_performance.hit_rate * 100).toFixed(0)}%` : "—"}, net
          expectancy {row.shadow_performance.net_expectancy != null ? row.shadow_performance.net_expectancy.toFixed(4) : "—"}
        </p>
        {history?.history?.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <span className="tile-label">Status history</span>
            <ul className="dec-reasons">
              {history.history.slice(0, 5).map((h: any) => (
                <li key={h.id}>
                  {h.previous_status ?? "—"} → {h.new_status} ({h.changed_by}
                  {h.reason ? `: ${h.reason}` : ""})
                </li>
              ))}
            </ul>
          </div>
        )}
        {row.status !== "ACTIVE" && (
          <div className="risk-warning">
            Reactivation risk: this indicator was removed for poor performance. Review the sample sizes above before
            re-enabling.
          </div>
        )}

        <select value={action} onChange={(e) => setAction(e.target.value)} className="risk-number-input" style={{ width: "100%" }}>
          <option value="enable_paper">Enable for Paper</option>
          <option value="enable_binance_real">Enable for Binance Real</option>
          <option value="enable_both">Enable for Both</option>
          <option value="keep_shadow">Keep Shadow Only</option>
          <option value="disable">Disable Manually</option>
        </select>
        <input
          className="risk-number-input"
          style={{ width: "100%", marginTop: 8 }}
          placeholder="Reason (optional)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />

        <div className="controls" style={{ marginTop: 16 }}>
          <button onClick={submit} disabled={busy}>
            {busy ? "Applying…" : "Confirm"}
          </button>
          <button onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

export default function IndicatorPerformancePanel({
  showToast,
}: {
  showToast: (message: string, tone?: "success" | "error") => void;
}) {
  const [rows, setRows] = useState<IndicatorRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [symbolFilter, setSymbolFilter] = useState<"All" | "BTCUSDT" | "ETHUSDT">("All");
  const [timeframeFilter, setTimeframeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<Filter>("All");
  const [activeRow, setActiveRow] = useState<IndicatorRow | null>(null);

  async function load() {
    setLoading(true);
    try {
      const res: any = await api.indicatorPerformance({
        symbol: symbolFilter === "All" ? undefined : symbolFilter,
        timeframe: timeframeFilter || undefined,
      });
      setRows(res.indicators || []);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolFilter, timeframeFilter]);

  const filtered = useMemo(() => rows.filter((r) => filterMatches(statusFilter, r)), [rows, statusFilter]);

  const columns: AutoCardColumn<IndicatorRow>[] = [
    { key: "indicator", label: "Indicator", render: (r) => `${r.source_name.replaceAll("_", " ")}${r.starred ? " ⭐" : ""}` },
    { key: "symbol", label: "Symbol", render: (r) => r.symbol },
    { key: "timeframe", label: "Timeframe", render: (r) => r.timeframe },
    { key: "mode", label: "Mode", render: (r) => r.mode },
    { key: "status", label: "Status", render: (r) => STATUS_LABELS[r.status] ?? r.status },
    { key: "influence", label: "Ensemble Influence", render: (r) => r.current_ensemble_influence },
    {
      key: "last10",
      label: "Last 10 Outcomes",
      render: (r) => (r.active_performance.last_10_outcomes || []).map((o) => (o === "correct" ? "✓" : o === "wrong" ? "✗" : "·")).join(" "),
    },
    { key: "correct", label: "Correct", render: (r) => r.active_performance.correct },
    { key: "wrong", label: "Wrong", render: (r) => r.active_performance.wrong },
    { key: "neutral", label: "Neutral", render: (r) => r.active_performance.neutral },
    {
      key: "wrong_rate",
      label: "Wrong Rate",
      render: (r) => (r.active_performance.wrong_rate != null ? `${(r.active_performance.wrong_rate * 100).toFixed(0)}%` : "—"),
    },
    { key: "shadow_sample", label: "Shadow Sample", render: (r) => r.shadow_performance.sample_size },
    {
      key: "shadow_hit_rate",
      label: "Shadow Hit Rate",
      render: (r) => (r.shadow_performance.hit_rate != null ? `${(r.shadow_performance.hit_rate * 100).toFixed(0)}%` : "—"),
    },
    {
      key: "net_expectancy",
      label: "Net Expectancy",
      render: (r) => (r.shadow_performance.net_expectancy != null ? r.shadow_performance.net_expectancy.toFixed(4) : "—"),
    },
    { key: "removed_reason", label: "Removal Reason", render: (r) => r.status_reason ?? "—", hideOnCard: true },
    {
      key: "actions",
      label: "Actions",
      render: (r) => (
        <button className="tf-btn" onClick={() => setActiveRow(r)}>
          Manage
        </button>
      ),
    },
  ];

  return (
    <Card title="Indicator Performance" full>
      <div className="controls" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        <div className="tf-group">
          {(["All", "BTCUSDT", "ETHUSDT"] as const).map((s) => (
            <button key={s} className={symbolFilter === s ? "tf-btn active" : "tf-btn"} onClick={() => setSymbolFilter(s)}>
              {s === "All" ? "All" : s.replace("USDT", "")}
            </button>
          ))}
        </div>
        <input
          className="risk-number-input"
          placeholder="Timeframe (e.g. 5m)"
          value={timeframeFilter}
          onChange={(e) => setTimeframeFilter(e.target.value)}
          style={{ maxWidth: 160 }}
        />
        <div className="tf-group">
          {FILTERS.map((f) => (
            <button key={f} className={statusFilter === f ? "tf-btn active" : "tf-btn"} onClick={() => setStatusFilter(f)}>
              {f}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="regime-desc">Loading indicator performance…</p>
      ) : filtered.length === 0 ? (
        <p className="regime-desc">No indicators match these filters.</p>
      ) : (
        <AutoCardTable columns={columns} rows={filtered} keyField={(r) => r.id} />
      )}

      {activeRow && (
        <ActionModal
          row={activeRow}
          onClose={() => setActiveRow(null)}
          onDone={() => {
            setActiveRow(null);
            load();
          }}
          showToast={showToast}
        />
      )}
    </Card>
  );
}
