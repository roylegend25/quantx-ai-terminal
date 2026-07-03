import { memo } from "react";

type Props = {
  marketContext: any;
};

function toneOfBias(bias?: string): "green" | "red" | "yellow" {
  if (bias === "BULLISH") return "green";
  if (bias === "BEARISH") return "red";
  return "yellow";
}

function MarketSentimentCard({ marketContext }: Props) {
  const bias = marketContext?.market_bias ?? "NEUTRAL";
  const tone = toneOfBias(bias);

  const fundingRate = marketContext?.funding_rate;
  const longShort = marketContext?.long_short_ratio;
  const fearGreed = marketContext?.fear_greed;
  const news = marketContext?.news_sentiment;

  return (
    <div>
      <p className="regime-desc">Overall Market Sentiment (AI Aggregated)</p>
      <h3 className={tone} style={{ fontSize: 28, margin: "6px 0 16px" }}>
        {bias}
      </h3>

      <div className="sentiment-grid">
        <div className="sentiment-tile">
          <span className="tile-label">Social Sentiment</span>
          <b className={typeof news === "number" ? (news >= 0 ? "green" : "red") : ""}>
            {typeof news === "number" ? `${(news * 100).toFixed(0)}%` : "—"}
          </b>
          <span className="sentiment-sub">{typeof news === "number" ? (news >= 0 ? "Bullish" : "Bearish") : "No data"}</span>
        </div>

        <div className="sentiment-tile">
          <span className="tile-label">Funding Rate</span>
          <b className={typeof fundingRate === "number" ? (fundingRate >= 0 ? "green" : "red") : ""}>
            {typeof fundingRate === "number" ? `${(fundingRate * 100).toFixed(4)}%` : "—"}
          </b>
          <span className="sentiment-sub">{typeof fundingRate === "number" ? (fundingRate >= 0 ? "Positive" : "Negative") : "No data"}</span>
        </div>

        <div className="sentiment-tile">
          <span className="tile-label">Long/Short Ratio</span>
          <b>{typeof longShort === "number" ? longShort.toFixed(2) : "—"}</b>
          <span className="sentiment-sub">
            {typeof longShort === "number" ? (longShort > 1 ? "Long Biased" : "Short Biased") : "No data"}
          </span>
        </div>

        <div className="sentiment-tile">
          <span className="tile-label">Fear & Greed</span>
          <b>{fearGreed?.index ?? "—"}</b>
          <span className="sentiment-sub">{fearGreed?.label ?? "No data"}</span>
        </div>
      </div>
    </div>
  );
}

export default memo(MarketSentimentCard);
