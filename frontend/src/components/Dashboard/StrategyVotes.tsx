import { memo } from "react";

type Strategy = {
  direction: string;
  confidence: number;
  reason: string;
};

type Props = {
  strategies?: Record<string, Strategy>;
};

function StrategyVotes({ strategies }: Props) {
  if (!strategies) return null;

  return (
    <div className="strategy-grid">
      {Object.entries(strategies).map(([name, value]) => (
        <div className="strategy-card" key={name}>
          <h4>{name.replaceAll("_", " ").toUpperCase()}</h4>

          <p>{value.direction}</p>

          <b>{value.confidence}%</b>

          <small>{value.reason}</small>
        </div>
      ))}
    </div>
  );
}

export default memo(StrategyVotes);
