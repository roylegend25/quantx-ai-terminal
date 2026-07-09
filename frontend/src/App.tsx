import { lazy, Suspense, useCallback, useState } from "react";
import "./App.css";
import Sidebar from "./components/Layout/Sidebar";
import Topbar from "./components/Layout/Topbar";
import LoginPage from "./components/Auth/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import { useAppData } from "./hooks/useAppData";
import { useAuth } from "./hooks/useAuth";
import { useTheme } from "./hooks/useTheme";
import type { NavKey } from "./lib/nav";

const PredictionsPage = lazy(() => import("./pages/PredictionsPage"));
const PositionsPage = lazy(() => import("./pages/PositionsPage"));
const PerformancePage = lazy(() => import("./pages/PerformancePage"));
const MarketPage = lazy(() => import("./pages/MarketPage"));
const BotSettingsPage = lazy(() => import("./pages/BotSettingsPage"));
const RiskPage = lazy(() => import("./pages/RiskPage"));
const LogsPage = lazy(() => import("./pages/LogsPage"));
const BacktestingPage = lazy(() => import("./pages/BacktestingPage"));
const SystemStatusPage = lazy(() => import("./pages/SystemStatusPage"));
const StressTestPage = lazy(() => import("./pages/StressTestPage"));
const ExecutionPage = lazy(() => import("./pages/ExecutionPage"));
const ModelCenterPage = lazy(() => import("./pages/ModelCenterPage"));
const ResearchLabPage = lazy(() => import("./pages/ResearchLabPage"));

function PageFallback() {
  return <div className="page-fallback" aria-hidden="true" />;
}

function App() {
  const { authed, login: onLoginSuccess, logout } = useAuth();
  const [active, setActive] = useState<NavKey>("dashboard");
  const data = useAppData(authed);
  const theme = useTheme();

  const handleStopBot = useCallback(() => {
    data.botAction("stop");
  }, [data.botAction]);

  if (authed === null) {
    return null;
  }

  if (!authed) {
    return <LoginPage onSuccess={onLoginSuccess} />;
  }

  function renderPage() {
    switch (active) {
      case "dashboard":
        return <DashboardPage {...data} navigate={setActive} />;
      case "predictions":
        return <PredictionsPage {...data} />;
      case "positions":
        return <PositionsPage {...data} navigate={setActive} />;
      case "performance":
        return <PerformancePage {...data} />;
      case "market":
        return <MarketPage {...data} />;
      case "bot-settings":
        return <BotSettingsPage {...data} />;
      case "risk":
        return <RiskPage {...data} />;
      case "logs":
        return <LogsPage />;
      case "backtesting":
        return <BacktestingPage {...data} />;
      case "system-status":
        return <SystemStatusPage {...data} />;
      case "stress-test":
        return <StressTestPage {...data} />;
      case "execution":
        return <ExecutionPage {...data} />;
      case "model-center":
        return <ModelCenterPage {...data} />;
      case "research-lab":
        return <ResearchLabPage {...data} />;
      default:
        return <DashboardPage {...data} navigate={setActive} />;
    }
  }

  return (
    <div className="app">
      <Sidebar
        active={active}
        onNavigate={setActive}
        botStatus={data.botStatus}
        dashboard={data.dashboard}
        onStopBot={handleStopBot}
        onLogout={logout}
      />

      <main className="main">
        <Topbar dashboard={data.dashboard} theme={theme} />

        {data.toast && <div className={`toast ${data.toastTone === "error" ? "toast-error" : ""}`}>{data.toast}</div>}

        <Suspense fallback={<PageFallback />}>{renderPage()}</Suspense>

        <footer className="app-footer">Built with ❤️ for serious traders</footer>
      </main>
    </div>
  );
}

export default App;
