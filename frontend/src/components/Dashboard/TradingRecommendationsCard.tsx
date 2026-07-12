import { useEffect, useState } from "react";
import { Lightbulb } from "lucide-react";
import { fmtUsd } from "../../lib/format";
import { api } from "../../services/api";

type Props = {
  symbol: string;
  marginData: any;
  liveActive: boolean;
};

const REC_ICON: Record<string, string> = {
  increase_leverage: "⚙️",
  deposit_funds: "💰",
  raise_setting: "📈",
  reduce_setting: "⚠️",
  symbol_infeasible: "🔀",
};

export default function TradingRecommendationsCard({ symbol, marginData, liveActive }: Props) {
  const [symbolRecs, setSymbolRecs] = useState<any>(null);
  const [loadingSymbols, setLoadingSymbols] = useState(false);

  const marginRecommendations: any[] = marginData?.recommendations || [];
  const symbolInfeasible = marginRecommendations.some((r) => r.type === "symbol_infeasible");

  useEffect(() => {
    if (!liveActive || !symbolInfeasible) {
      setSymbolRecs(null);
      return;
    }
    let cancelled = false;
    setLoadingSymbols(true);
    api
      .binanceSymbolRecommendations()
      .then((r: any) => !cancelled && setSymbolRecs(r))
      .catch(() => !cancelled && setSymbolRecs(null))
      .finally(() => !cancelled && setLoadingSymbols(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveActive, symbolInfeasible, symbol]);

  if (!liveActive) {
    return (
      <div className="stack-card">
        <div className="card-title">Trading Recommendations</div>
        <p className="regime-desc">Switch to Binance Live Trading to see live recommendations.</p>
      </div>
    );
  }

  const bestSymbol = symbolRecs?.best_symbol;
  const bestCandidate = symbolRecs?.candidates?.find((c: any) => c.symbol === bestSymbol);

  return (
    <div className="stack-card">
      <div className="card-title">Trading Recommendations</div>

      {marginRecommendations.length === 0 && !symbolInfeasible && (
        <p className="regime-desc">No adjustments recommended right now — current settings look healthy.</p>
      )}

      <ul className="reason-list" style={{ paddingLeft: 0, listStyle: "none" }}>
        {marginRecommendations
          .filter((r) => r.type !== "symbol_infeasible")
          .map((r, i) => (
            <li key={i} className="regime-focus" style={{ marginBottom: 10 }}>
              <span className="tile-label">
                {REC_ICON[r.type] || "💡"} {r.type.replace(/_/g, " ")}
              </span>
              <p className="regime-desc" style={{ margin: "4px 0 0" }}>{r.message}</p>
            </li>
          ))}
      </ul>

      {symbolInfeasible && (
        <div className="regime-focus blocked" style={{ marginTop: marginRecommendations.length > 1 ? 0 : 10 }}>
          <span className="tile-label">
            <Lightbulb size={13} /> Current {symbol} minimum quantity prevents trading
          </span>
          {loadingSymbols ? (
            <p className="regime-desc">Evaluating alternative symbols…</p>
          ) : bestCandidate ? (
            <>
              <b className="tile-value green">Switch to {bestCandidate.symbol}</b>
              <p className="regime-desc">{symbolRecs.reason}</p>
              <div className="kv-grid" style={{ marginTop: 8 }}>
                <div>
                  <span className="tile-label">Minimum Margin</span>
                  <b className="tile-value">{fmtUsd(bestCandidate.minimum_margin_required)}</b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Expected Position Size</span>
                  <b className="tile-value">{fmtUsd(bestCandidate.recommended_max_notional)}</b>
                </div>
              </div>
            </>
          ) : (
            <p className="regime-desc">
              {symbolRecs?.reason || "No allowed symbol is currently feasible at this wallet size."}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
