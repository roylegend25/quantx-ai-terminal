import Card from "../components/Layout/Card";
import PortfolioAnalytics from "../components/Dashboard/PortfolioAnalytics";
import type { AppData } from "../hooks/useAppData";

export default function PerformancePage(props: AppData) {
  const { portfolio, positions, history } = props;

  return (
    <div className="page-grid">
      <Card title="Portfolio Analytics" full>
        <PortfolioAnalytics portfolio={portfolio} positions={positions} history={history} />
      </Card>
    </div>
  );
}
