import { FileText } from "lucide-react";
import Card from "../components/Layout/Card";
import ComingSoon from "../components/Shared/ComingSoon";

export default function LogsPage() {
  return (
    <div className="page-grid">
      <Card title="System Logs" full>
        <ComingSoon
          icon={FileText}
          title="Log Streaming"
          description="A structured log/event feed isn't exposed by the backend yet — this view will light up once the engine ships an events or audit-log endpoint."
        />
      </Card>
    </div>
  );
}
