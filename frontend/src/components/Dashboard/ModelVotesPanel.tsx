import { memo } from "react";

type Vote = {
  name: string;
  direction: string | null;
  confidence: number | null;
  weight: number;
  available: boolean;
  drives_decision?: boolean;
  contribution?: number;
  reason?: string | null;
};

type Props = {
  /** prediction.decision_engine.model_votes */
  votes: Vote[] | null | undefined;
  finalDirection?: string | null;
};

function dirTone(direction?: string | null): string {
  if (direction === "LONG") return "green";
  if (direction === "SHORT") return "red";
  return "yellow";
}

function prettyName(name: string): string {
  return name === "Champion ML" ? name : name.replace(/_/g, " ");
}

function ModelVotesPanel({ votes, finalDirection }: Props) {
  if (!votes || votes.length === 0) {
    return <p className="analytics-empty">Waiting for the decision engine to report model votes.</p>;
  }

  // contribution is the signed push toward LONG in probability points; the
  // bar scales against the largest absolute push so rows stay comparable.
  const maxAbs = Math.max(1, ...votes.map((v) => Math.abs(v.contribution ?? 0)));

  return (
    <div className="votes-panel">
      {votes.map((v) => {
        const contribution = v.contribution ?? 0;
        const barPct = Math.min(100, (Math.abs(contribution) / maxAbs) * 100);
        return (
          <div className={`vote-row${v.available ? "" : " vote-unavailable"}`} key={v.name}>
            <div className="vote-head">
              <span className="vote-name">
                {prettyName(v.name)}
                {/* only the Champion gets the driver chip - in ensemble mode
                    every strategy contributes, so per-row chips are noise */}
                {v.name === "Champion ML" && v.drives_decision && v.available && (
                  <span className="chip cyan vote-driver">drives decision</span>
                )}
              </span>
              {v.available ? (
                <span className={`chip ${dirTone(v.direction)}`}>
                  {v.direction ?? "—"}
                  {typeof v.confidence === "number" ? ` · ${v.confidence.toFixed(0)}%` : ""}
                </span>
              ) : (
                <span className="chip">unavailable</span>
              )}
            </div>
            {v.available ? (
              <div className="vote-bar-row">
                <div className="vote-bar">
                  <div
                    className={`vote-bar-fill ${contribution >= 0 ? "fill-pos" : "fill-neg"}`}
                    style={{ width: `${barPct}%` }}
                  />
                </div>
                <span className="vote-meta">
                  w {(v.weight * 100).toFixed(0)}% · {contribution >= 0 ? "+" : ""}
                  {contribution.toFixed(1)} pts
                </span>
              </div>
            ) : (
              <p className="vote-reason">{v.reason ?? "Not available this cycle"}</p>
            )}
          </div>
        );
      })}
      {finalDirection && (
        <p className="regime-desc votes-final">
          Final decision: <b className={dirTone(finalDirection)}>{finalDirection}</b>
        </p>
      )}
    </div>
  );
}

export default memo(ModelVotesPanel);
