import PerformancePage from "../../pages/PerformancePage";
import type { AppData } from "../../hooks/useAppData";
import PremiumPageShell from "../components/PremiumPageShell";

/** Hero chrome around the existing, unmodified PerformancePage. */
export default function PremiumPerformancePage(props: AppData) {
  return (
    <PremiumPageShell title="Performance" subtitle="Portfolio analytics and trade history across paper and Binance Real.">
      <PerformancePage {...props} />
    </PremiumPageShell>
  );
}
