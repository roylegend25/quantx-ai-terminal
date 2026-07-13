export type ResponsiveTabItem = { key: string; label: string };

type Props = {
  items: ResponsiveTabItem[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
};

/** Horizontally scrollable pill tabs - the same shape works unmodified from
 * desktop timeframe pills down to phone-width scrollable tab strips. */
export default function ResponsiveTabs({ items, active, onChange, className }: Props) {
  return (
    <div className={`qp-tabs${className ? ` ${className}` : ""}`} role="tablist">
      {items.map((item) => (
        <button
          key={item.key}
          role="tab"
          aria-selected={active === item.key}
          className={active === item.key ? "qp-tab active" : "qp-tab"}
          onClick={() => onChange(item.key)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
