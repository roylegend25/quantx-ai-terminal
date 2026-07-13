import PositionsPage from "../../pages/PositionsPage";
import type { AppData } from "../../hooks/useAppData";
import type { NavKey } from "../../lib/nav";
import PremiumPageShell from "../components/PremiumPageShell";
import MetricCard from "../components/MetricCard";
import { fmtUsd } from "../../lib/format";

type Props = AppData & { navigate: (key: NavKey) => void };

/** Hero chrome around the existing, unmodified PositionsPage. PositionsPage
 * owns real close/edit/protect/close-all mutations against both the paper
 * engine and live Binance account (via useBinanceAccount) - forking that
 * logic for a "Premium" variant would create a second, divergent source of
 * truth for live-money actions, which is exactly the risk the redesign
 * plan calls out. Instead this page supplies a bespoke summary strip from
 * already-fetched AppData (no new mutations) and reuses PositionsPage's
 * JSX verbatim underneath; its .page-grid/.card/.data-table/badge markup
 * is reskinned entirely by premium/overrides.css. */
export default function PremiumPositionsPage(props: Props) {
  const openCount = props.positions.length;
  const maxOpen = props.portfolio?.max_open_positions;

  return (
    <PremiumPageShell title="Positions" subtitle="Open positions across paper and Binance Real, with journal history.">
      <div className="qp-metric-grid">
        <MetricCard label="Open Positions" value={typeof maxOpen === "number" ? `${openCount} / ${maxOpen}` : openCount} />
        <MetricCard label="Total Margin Used" value={fmtUsd(props.portfolio?.total_margin_used)} />
        <MetricCard label="Available Margin" value={fmtUsd(props.portfolio?.available_margin)} tone="positive" />
        <MetricCard
          label="Prediction Direction"
          value={props.prediction?.direction || "-"}
          tone={props.prediction?.direction === "LONG" ? "positive" : props.prediction?.direction === "SHORT" ? "negative" : "neutral"}
        />
      </div>
      <PositionsPage {...props} />
    </PremiumPageShell>
  );
}
