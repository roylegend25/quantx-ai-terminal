import type { Key, ReactNode } from "react";

export type MobileDataCardField = { label: string; value: ReactNode };

type Props = {
  icon?: ReactNode;
  title: ReactNode;
  status?: ReactNode;
  fields: MobileDataCardField[];
  actions?: ReactNode;
  detail?: ReactNode;
  keyId?: Key;
};

/** The mobile-card shape every premium table collapses into below 768px:
 * icon/logo + title + status in the header, key:value grid in the body,
 * actions pinned to the footer. Used directly by PremiumTable and by
 * bespoke premium pages (Positions, Portfolio, Binance Real) that need
 * hand-grouped fields rather than PremiumTable's generic column mapping. */
export default function MobileDataCard({ icon, title, status, fields, actions, detail, keyId }: Props) {
  return (
    <div className="qp-data-card" key={keyId}>
      <div className="qp-data-card-head">
        {icon && <span className="qp-data-card-icon">{icon}</span>}
        <b className="qp-data-card-title">{title}</b>
        {status && <span className="qp-data-card-status">{status}</span>}
      </div>
      <div className="qp-data-card-grid">
        {fields.map((f) => (
          <div key={f.label}>
            <span className="qp-data-card-field-label">{f.label}</span>
            <b className="qp-data-card-field-value">{f.value}</b>
          </div>
        ))}
      </div>
      {detail && <div className="qp-data-card-detail">{detail}</div>}
      {actions && <div className="qp-data-card-actions">{actions}</div>}
    </div>
  );
}
