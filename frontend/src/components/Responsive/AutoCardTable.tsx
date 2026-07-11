import { Fragment, useState, type Key, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useMediaQuery } from "../../hooks/useMediaQuery";

/** Generic table -> stacked-card fallback for the dozens of dense data
 *  tables across the app that don't warrant a hand-designed mobile layout
 *  (Research Lab, Model Center's secondary tables, Optimizer, Exchange
 *  Status, Execution, Performance, Stress Test). Desktop renders the exact
 *  same "table-wrap > data-table" markup every table in the app already
 *  uses; mobile (<=767px) renders label:value stacked cards instead of a
 *  horizontally-scrolling table. Modeled directly on the hand-built
 *  DesktopTable/MobileCards split in Dashboard/OpenPositionsCard.tsx -
 *  use that component instead of this one for trading-critical tables that
 *  need bespoke field grouping (positions, balances, orders, bot trades). */

export type AutoCardColumn<T> = {
  key: string;
  label: string;
  /** Reuse the exact cell JSX/formatting the source file's desktop <td>
   *  already has (fmtNum/fmtUsd/tone spans, etc.) - this component is a
   *  pure layout wrapper and applies no formatting of its own. */
  render: (row: T) => ReactNode;
  /** Omit this column from the mobile card body (still shown on desktop) -
   *  e.g. an internal id, or a column already surfaced as the card title. */
  hideOnCard?: boolean;
};

type Props<T> = {
  columns: AutoCardColumn<T>[];
  rows: T[];
  keyField: (row: T) => Key;
  /** Column key rendered as the card's bold header. Defaults to the first
   *  column. */
  titleColumn?: string;
  /** Column key rendered as a small badge next to the card title (status,
   *  side, severity, regime, ...). */
  statusColumn?: string;
  renderActions?: (row: T) => ReactNode;
  /** Expandable detail slot - both the desktop table (a colSpan row) and
   *  mobile cards (tap-to-expand) use this for the same row. */
  renderDetail?: (row: T) => ReactNode;
  emptyMessage?: string;
};

export default function AutoCardTable<T>({
  columns,
  rows,
  keyField,
  titleColumn,
  statusColumn,
  renderActions,
  renderDetail,
  emptyMessage = "No data.",
}: Props<T>) {
  const isMobile = useMediaQuery("(max-width: 767px)");
  const [expanded, setExpanded] = useState<Set<Key>>(new Set());

  if (rows.length === 0) {
    return <p className="analytics-empty">{emptyMessage}</p>;
  }

  function toggle(key: Key) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  if (isMobile) {
    const titleKey = titleColumn ?? columns[0]?.key;
    const bodyColumns = columns.filter((c) => !c.hideOnCard && c.key !== titleKey && c.key !== statusColumn);
    const titleCol = columns.find((c) => c.key === titleKey);
    const statusCol = statusColumn ? columns.find((c) => c.key === statusColumn) : undefined;

    return (
      <div className="auto-card-list">
        {rows.map((row) => {
          const key = keyField(row);
          const open = expanded.has(key);
          const clickable = Boolean(renderDetail);
          return (
            <div key={key} className={`auto-card${open ? " auto-card-open" : ""}`}>
              {clickable ? (
                <button className="auto-card-head" onClick={() => toggle(key)}>
                  <span className="auto-card-chevron">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
                  {titleCol && <b>{titleCol.render(row)}</b>}
                  {statusCol && <span className="auto-card-badge">{statusCol.render(row)}</span>}
                </button>
              ) : (
                <div className="auto-card-head auto-card-head-static">
                  {titleCol && <b>{titleCol.render(row)}</b>}
                  {statusCol && <span className="auto-card-badge">{statusCol.render(row)}</span>}
                </div>
              )}

              <div className="auto-card-grid">
                {bodyColumns.map((c) => (
                  <div key={c.key}>
                    <span className="tile-label">{c.label}</span>
                    <b className="tile-value">{c.render(row)}</b>
                  </div>
                ))}
              </div>

              {renderDetail && open && <div className="auto-card-detail">{renderDetail(row)}</div>}

              {renderActions && <div className="auto-card-actions pos-actions">{renderActions(row)}</div>}
            </div>
          );
        })}
      </div>
    );
  }

  const colSpan = columns.length + (renderActions ? 1 : 0);

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
            {renderActions && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = keyField(row);
            const open = expanded.has(key);
            return (
              <Fragment key={key}>
                <tr
                  className={renderDetail ? "auto-card-row-clickable" : undefined}
                  onClick={renderDetail ? () => toggle(key) : undefined}
                >
                  {columns.map((c) => (
                    <td key={c.key}>{c.render(row)}</td>
                  ))}
                  {renderActions && (
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="pos-actions">{renderActions(row)}</div>
                    </td>
                  )}
                </tr>
                {renderDetail && open && (
                  <tr>
                    <td colSpan={colSpan}>{renderDetail(row)}</td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
