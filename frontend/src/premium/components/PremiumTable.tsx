import type { Key, ReactNode } from "react";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import MobileDataCard from "./MobileDataCard";
import EmptyState from "./EmptyState";

export type PremiumColumn<T> = {
  key: string;
  label: string;
  render: (row: T) => ReactNode;
  hideOnCard?: boolean;
  /** Right-align numeric columns on desktop. */
  align?: "left" | "right";
};

type Props<T> = {
  columns: PremiumColumn<T>[];
  rows: T[];
  keyField: (row: T) => Key;
  titleColumn?: string;
  statusColumn?: string;
  iconColumn?: string;
  renderActions?: (row: T) => ReactNode;
  emptyMessage?: string;
  emptyIcon?: ReactNode;
};

/** Bespoke-tier responsive table for the hero pages (Positions, Portfolio,
 * Binance Real, Performance). Same table<->card responsive strategy as
 * AutoCardTable, but with premium markup/classNames and an optional icon
 * column (asset logos) that becomes the mobile card's leading icon. */
export default function PremiumTable<T>({
  columns,
  rows,
  keyField,
  titleColumn,
  statusColumn,
  iconColumn,
  renderActions,
  emptyMessage = "No data yet.",
  emptyIcon,
}: Props<T>) {
  const isMobile = useMediaQuery("(max-width: 767px)");

  if (rows.length === 0) {
    return <EmptyState title={emptyMessage} icon={emptyIcon} />;
  }

  if (isMobile) {
    const titleKey = titleColumn ?? columns[0]?.key;
    const titleCol = columns.find((c) => c.key === titleKey);
    const statusCol = statusColumn ? columns.find((c) => c.key === statusColumn) : undefined;
    const iconCol = iconColumn ? columns.find((c) => c.key === iconColumn) : undefined;
    const bodyColumns = columns.filter(
      (c) => !c.hideOnCard && c.key !== titleKey && c.key !== statusColumn && c.key !== iconColumn
    );

    return (
      <div className="qp-data-card-list">
        {rows.map((row) => (
          <MobileDataCard
            key={keyField(row)}
            icon={iconCol?.render(row)}
            title={titleCol?.render(row)}
            status={statusCol?.render(row)}
            fields={bodyColumns.map((c) => ({ label: c.label, value: c.render(row) }))}
            actions={renderActions?.(row)}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="qp-table-wrap">
      <table className="qp-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.align === "right" ? "qp-th-right" : undefined}>
                {c.label}
              </th>
            ))}
            {renderActions && <th className="qp-th-right">Actions</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={keyField(row)}>
              {columns.map((c) => (
                <td key={c.key} className={c.align === "right" ? "qp-td-right" : undefined}>
                  {c.render(row)}
                </td>
              ))}
              {renderActions && (
                <td className="qp-td-right">
                  <div className="qp-row-actions">{renderActions(row)}</div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
