import type { ReactNode } from "react";

type Props = {
  title: string;
  subtitle?: ReactNode;
  right?: ReactNode;
  className?: string;
};

export default function SectionHeader({ title, subtitle, right, className }: Props) {
  return (
    <div className={`qp-section-header${className ? ` ${className}` : ""}`}>
      <div>
        <h2 className="qp-section-title">{title}</h2>
        {subtitle && <p className="qp-section-subtitle">{subtitle}</p>}
      </div>
      {right && <div className="qp-section-right">{right}</div>}
    </div>
  );
}
