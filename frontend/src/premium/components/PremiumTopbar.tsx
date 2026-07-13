import { memo, useEffect, useState } from "react";
import { UserRound } from "lucide-react";
import NotificationBell from "../../components/Layout/NotificationBell";
import type { UseThemeReturn } from "../../hooks/useTheme";
import type { UseDesignSystemReturn } from "../../hooks/useDesignSystem";
import type { NavKey } from "../../lib/nav";
import { NAV_ITEMS } from "../../lib/nav";
import MarketSummaryBar, { type MarketSummaryItem } from "./MarketSummaryBar";
import PremiumAppearanceMenu from "./PremiumAppearanceMenu";

type Props = {
  dashboard: any;
  theme: UseThemeReturn;
  designSystem: UseDesignSystemReturn;
  activeKey: NavKey;
};

function PremiumTopbar({ dashboard, designSystem, activeKey }: Props) {
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const btc = dashboard?.symbols?.BTCUSDT;
  const eth = dashboard?.symbols?.ETHUSDT;
  const loading = !dashboard;

  const items: MarketSummaryItem[] = loading
    ? []
    : [
        {
          symbol: "BTCUSDT",
          price: Number(btc?.ticker?.lastPrice || 0),
          changePct: Number(btc?.ticker?.priceChangePercent || 0),
        },
        {
          symbol: "ETHUSDT",
          price: Number(eth?.ticker?.lastPrice || 0),
          changePct: Number(eth?.ticker?.priceChangePercent || 0),
        },
      ];

  const pageLabel = NAV_ITEMS.find((i) => i.key === activeKey)?.label || "Dashboard";

  return (
    <header className="qp-topbar">
      <div className="qp-topbar-title">
        <p className="qp-topbar-eyebrow">Welcome back, Trader</p>
        <h1>{pageLabel}</h1>
      </div>

      <MarketSummaryBar items={items} loading={loading} />

      <div className="qp-topbar-actions">
        <span className="qp-clock">{now.toUTCString().slice(17, 25)} UTC</span>
        <NotificationBell routeKey={activeKey} />
        <PremiumAppearanceMenu designSystem={designSystem} />
        <div className="qp-avatar">
          <UserRound size={17} />
        </div>
      </div>
    </header>
  );
}

export default memo(PremiumTopbar);
