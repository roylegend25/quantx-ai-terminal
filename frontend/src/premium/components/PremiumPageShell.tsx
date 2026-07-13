import type { ReactNode } from "react";
import SectionHeader from "./SectionHeader";

type Props = {
  title?: string;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
};

/** Consistent page-level padding/max-width/vertical rhythm for every
 * Premium page body (bespoke and generic alike). */
export default function PremiumPageShell({ title, subtitle, right, children, className }: Props) {
  return (
    <div className={`qp-page${className ? ` ${className}` : ""}`}>
      {title && <SectionHeader title={title} subtitle={subtitle} right={right} className="qp-page-header" />}
      {children}
    </div>
  );
}
