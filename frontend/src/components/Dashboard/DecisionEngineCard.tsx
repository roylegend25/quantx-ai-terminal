import { memo } from "react";
import { Brain, Crown, ShieldAlert } from "lucide-react";
import { fmtLocalDateTime, fmtNum } from "../../lib/format";

type Props = {
  /** Flat GET /api/ml/champion payload (has_champion, status, health, ...) */
  status: any;
  /** prediction.decision_engine for the currently selected symbol/interval */
  decision: any;
  symbol: string;
  interval: string;
};

const MODE_LABEL: Record<string, string> = {
  active_drive_v2: "Active Drive V2",
  active_drive_v1: "Active Drive V1 (Legacy)",
  champion_ml: "Champion ML",
  strategy_ensemble: "Strategy Ensemble",
  fallback: "Fallback (Strategy Ensemble)",
};

function modeTone(mode?: string | null): string {
  if (mode === "active_drive_v2" || mode === "champion_ml") return "green";
  if (mode === "fallback") return "yellow";
  return "cyan";
}

function healthTone(health?: string | null): string {
  if (health === "healthy") return "green";
  if (health === "warning") return "yellow";
  return "red";
}

function DecisionEngineCard({ status, decision, symbol, interval }: Props) {
  const hasChampion = status?.has_champion === true;
  // The live per-prediction mode is the source of truth for what is driving
  // decisions *right now*; the champion status endpoint describes the model.
  const mode = decision?.mode ?? (hasChampion ? (status?.status === "active" ? "champion_ml" : "fallback") : "strategy_ensemble");

  return (
    <div className="dec-engine">
      <div className="dec-mode-row">
        <span className={`dec-mode-badge ${modeTone(mode)}`}>
          {mode === "champion_ml" ? <Crown size={14} /> : <Brain size={14} />}
          {MODE_LABEL[mode] ?? "Strategy Ensemble"}
        </span>
        {mode === "active_drive_v2" && (
        <div className="analytics-grid dec-metrics">
          <div className="analytics-tile"><span className="tile-label">LONG Points</span><b className="tile-value green">{decision?.long_points ?? 0}</b></div>
          <div className="analytics-tile"><span className="tile-label">SHORT Points</span><b className="tile-value red">{decision?.short_points ?? 0}</b></div>
          <div className="analytics-tile"><span className="tile-label">Point Margin</span><b className="tile-value">{decision?.point_margin ?? 0}</b></div>
          <div className="analytics-tile"><span className="tile-label">Evidence</span><b className="tile-value dec-small">{(decision?.evidence_tier ?? "insufficient_evidence").replaceAll("_", " ")}</b></div>
        </div>
      )}

      {hasChampion && mode !== "active_drive_v2" && (
          <span className={`chip ${healthTone(status?.health)}`}>health: {status?.health ?? "unknown"}</span>
        )}
        <span className="chip">
          {symbol} · {interval}
        </span>
      </div>

      {!hasChampion && mode !== "active_drive_v2" && (
        <div className="dec-empty">
          <ShieldAlert size={16} className="yellow" />
          <div>
            <b>No Champion model is active. Bot is using Strategy Ensemble.</b>
            <p className="regime-desc">{status?.reason ?? "Promote a model in the AI Model Center to activate Champion inference."}</p>
          </div>
        </div>
      )}

      {hasChampion && (
        <>
          <div className="analytics-grid dec-metrics">
            <div className="analytics-tile">
              <span className="tile-label">Champion Model</span>
              <b className="tile-value">{status?.model_type ?? "—"}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Version</span>
              <b className="tile-value">{status?.version ?? "—"}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Accuracy</span>
              <b className="tile-value">
                {typeof status?.accuracy === "number" ? `${fmtNum(status.accuracy * 100, 1)}%` : "—"}
              </b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Win Rate</span>
              <b className="tile-value">{typeof status?.win_rate === "number" ? `${fmtNum(status.win_rate, 1)}%` : "—"}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Profit Factor</span>
              <b className="tile-value">{fmtNum(status?.profit_factor)}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Sharpe</span>
              <b className="tile-value">{fmtNum(status?.sharpe)}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Last Trained</span>
              <b className="tile-value dec-small">{status?.trained_at ? fmtLocalDateTime(status.trained_at) : "—"}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Last Promoted</span>
              <b className="tile-value dec-small">{status?.promoted_at ? fmtLocalDateTime(status.promoted_at) : "—"}</b>
            </div>
            <div className="analytics-tile">
              <span className="tile-label">Trained On</span>
              <b className="tile-value dec-small">
                {status?.symbol ?? "—"}
                {status?.timeframe ? ` · ${status.timeframe}` : ""}
              </b>
            </div>
          </div>

          <p className={`regime-desc dec-reason ${mode === "champion_ml" ? "" : "dec-reason-warn"}`}>
            {mode === "fallback" && decision?.fallback_reason
              ? `Fallback active: ${decision.fallback_reason}`
              : status?.reason ?? ""}
          </p>
        </>
      )}
    </div>
  );
}

export default memo(DecisionEngineCard);
