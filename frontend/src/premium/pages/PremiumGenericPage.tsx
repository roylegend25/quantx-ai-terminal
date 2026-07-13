import type { ReactNode } from "react";
import { NAV_ITEMS, type NavKey } from "../../lib/nav";
import PremiumPageShell from "../components/PremiumPageShell";

type Props = {
  activeKey: NavKey;
  children: ReactNode;
};

/** Tier B wrapper for the ~12 pages that don't get a bespoke Premium
 * composition: renders the existing Classic page component completely
 * unmodified (same data, same JSX, same event handlers) inside Premium
 * chrome. Reskinning happens entirely through premium/overrides.css
 * targeting the .page-grid/.card/.data-table classNames those pages
 * already emit - see the plan's Tier B rationale. */
export default function PremiumGenericPage({ activeKey, children }: Props) {
  const label = NAV_ITEMS.find((i) => i.key === activeKey)?.label || "";
  return (
    <PremiumPageShell title={label}>
      <div className="qp-generic-page">{children}</div>
    </PremiumPageShell>
  );
}
