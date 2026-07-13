import PortfolioPage from "../../pages/PortfolioPage";
import type { AppData } from "../../hooks/useAppData";
import type { NavKey } from "../../lib/nav";
import PremiumPageShell from "../components/PremiumPageShell";

type Props = AppData & { navigate: (key: NavKey) => void };

/** Hero chrome around the existing, unmodified PortfolioPage. PortfolioPage
 * fetches its own paper/Binance summaries and keeps the two accounts
 * strictly separate internally - that separation logic stays untouched;
 * see PremiumPositionsPage for the fuller rationale (Tier B). */
export default function PremiumPortfolioPage(props: Props) {
  return (
    <PremiumPageShell title="Portfolio" subtitle="Paper and Binance Real accounts, side by side - never mixed.">
      <PortfolioPage {...props} />
    </PremiumPageShell>
  );
}
