import BinanceRealPage from "../../pages/BinanceRealPage";
import type { AppData } from "../../hooks/useAppData";
import PremiumPageShell from "../components/PremiumPageShell";
import BrandLogo from "../components/BrandLogo";

/** Hero chrome around the existing, unmodified BinanceRealPage - the
 * highest-consequence page in the app (real orders against a live Binance
 * account, gated by server lock + user live confirmation). See
 * PremiumPositionsPage for the Tier B rationale; nothing here forks that
 * logic, only the surrounding shell/title changes. */
export default function PremiumBinanceRealPage(props: AppData) {
  return (
    <PremiumPageShell
      title="Binance Real"
      subtitle="Live Binance Futures account - real orders, real funds."
      right={<BrandLogo brand="BINANCE" size="card" />}
    >
      <BinanceRealPage {...props} />
    </PremiumPageShell>
  );
}
