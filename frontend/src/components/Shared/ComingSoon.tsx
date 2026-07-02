import type { ComponentType } from "react";
import { Sparkles } from "lucide-react";

type Props = {
  icon?: ComponentType<{ size?: number }>;
  title: string;
  description: string;
};

export default function ComingSoon({ icon: Icon = Sparkles, title, description }: Props) {
  return (
    <div className="coming-soon-panel">
      <Icon size={28} />
      <b>{title}</b>
      <p>{description}</p>
      <span className="badge">Coming Soon</span>
    </div>
  );
}
