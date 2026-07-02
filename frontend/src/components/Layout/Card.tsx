import type { ReactNode } from "react";

type Props = {
  title?: string;
  right?: ReactNode;
  wide?: boolean;
  full?: boolean;
  className?: string;
  children: ReactNode;
};

export default function Card({ title, right, wide = false, full = false, className = "", children }: Props) {
  return (
    <div className={`card ${wide ? "wide" : ""} ${full ? "full" : ""} ${className}`}>
      {(title || right) && (
        <div className="card-head">
          {title && <div className="card-title">{title}</div>}
          {right}
        </div>
      )}
      {children}
    </div>
  );
}
