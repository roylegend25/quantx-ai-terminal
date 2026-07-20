import { memo } from "react";
import { normalizeDecision } from "../../lib/decisionSummary";
import { fmtUsd } from "../../lib/format";

/** Phase 34: fixed paper-engine notional per trade (see the "Open a Paper
 *  Trade" panel in PositionsPage.tsx, which opens the exact same $1,000
 *  notional) and the leverage choices offered there - reused here, not
 *  invented, so the paper envelope always matches what a paper trade would
 *  actually do. */
const PAPER_NOTIONAL = 1000;
const PAPER_LEVERAGE_OPTIONS = [1, 2, 3, 5, 10, 20];

type Props = {
  prediction: any;
  paperAvailableMargin: number | null | undefined;
  marginData: any;
  marginErrored: boolean;
};

function EnvelopeRow({ label, min, max }: { label: string; min: string; max: string }) {
  return (
    <div className="risk-envelope-row">
      <span className="tile-label">{label}</span>
      <div className="risk-envelope-bounds">
        <span>
          <small>Min</small>
          <b>{min}</b>
        </span>
        <span>
          <small>Max</small>
          <b>{max}</b>
        </span>
      </div>
    </div>
  );
}

/** Live minimum/maximum risk envelope for the CURRENT decision, kept
 *  strictly separate for paper vs Binance Real - the two are never merged
 *  into one figure, since they draw on different capital, different
 *  configured limits, and (for Binance Real) live exchange constraints. */
function DecisionRiskEnvelopeCard({ prediction, paperAvailableMargin, marginData, marginErrored }: Props) {
  const d = normalizeDecision(prediction);

  const paperMarginMin = PAPER_NOTIONAL / Math.max(...PAPER_LEVERAGE_OPTIONS);
  const paperMarginMax = PAPER_NOTIONAL / Math.min(...PAPER_LEVERAGE_OPTIONS);

  const binanceAvailable = marginData?.available === true;

  return (
    <div className="risk-envelope-grid">
      <div className="risk-envelope-panel">
        <span className="chip cyan source-badge">PAPER</span>
        <EnvelopeRow label="Notional / Trade" min={fmtUsd(PAPER_NOTIONAL)} max={fmtUsd(PAPER_NOTIONAL)} />
        <EnvelopeRow label="Margin Required" min={fmtUsd(paperMarginMin)} max={fmtUsd(paperMarginMax)} />
        <div className="risk-envelope-row">
          <span className="tile-label">Available Margin</span>
          <b className="tile-value">{fmtUsd(paperAvailableMargin)}</b>
        </div>
        {d.actionable && (
          <EnvelopeRow
            label="Decision Price Bounds"
            min={d.stop != null ? fmtUsd(d.stop) : "—"}
            max={d.target != null ? fmtUsd(d.target) : "—"}
          />
        )}
      </div>

      <div className="risk-envelope-panel">
        <span className="chip purple source-badge">BINANCE REAL</span>
        {!binanceAvailable ? (
          <p className="regime-desc">
            {marginErrored
              ? "Could not reach the Binance Real account - risk envelope unavailable."
              : marginData?.reason || "Binance Real risk envelope unavailable."}
          </p>
        ) : (
          <>
            <EnvelopeRow
              label="Notional / Trade"
              min={fmtUsd(marginData.market?.min_notional)}
              max={fmtUsd(marginData.recommendation?.current_setting_notional)}
            />
            <EnvelopeRow
              label="Margin Required (at configured max)"
              min={fmtUsd(0)}
              max={fmtUsd(marginData.breakdown_at_current_setting?.initial_margin)}
            />
            <div className="risk-envelope-row">
              <span className="tile-label">Available Margin</span>
              <b className="tile-value">{fmtUsd(marginData.account?.available_balance)}</b>
            </div>
            <div className="risk-envelope-row">
              <span className="tile-label">Configured Leverage</span>
              <b className="tile-value">{marginData.account?.configured_leverage != null ? `${marginData.account.configured_leverage}x` : "—"}</b>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default memo(DecisionRiskEnvelopeCard);
