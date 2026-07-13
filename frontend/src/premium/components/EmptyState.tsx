import type { ReactNode } from "react";

type Props = {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
};

export default function EmptyState({ title, description, icon, action }: Props) {
  return (
    <div className="qp-empty-state">
      {icon && <div className="qp-empty-icon">{icon}</div>}
      <p className="qp-empty-title">{title}</p>
      {description && <p className="qp-empty-desc">{description}</p>}
      {action && <div className="qp-empty-action">{action}</div>}
    </div>
  );
}
