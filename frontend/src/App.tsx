import { useState } from "react";
import "./App.css";
import Sidebar from "./components/Layout/Sidebar";
import Topbar from "./components/Layout/Topbar";
import LoginPage from "./components/Auth/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import PredictionsPage from "./pages/PredictionsPage";
import PositionsPage from "./pages/PositionsPage";
import PerformancePage from "./pages/PerformancePage";
import MarketPage from "./pages/MarketPage";
import BotSettingsPage from "./pages/BotSettingsPage";
import RiskPage from "./pages/RiskPage";
import LogsPage from "./pages/LogsPage";
import BacktestingPage from "./pages/BacktestingPage";
import SystemStatusPage from "./pages/SystemStatusPage";
import { useAppData } from "./hooks/useAppData";
import { useAuth } from "./hooks/useAuth";
import type { NavKey } from "./lib/nav";

function App() {
  const { authed, login: onLoginSuccess, logout } = useAuth();
  const [active, setActive] = useState<NavKey>("dashboard");
  const data = useAppData(authed);

  if (authed === null) {
    return null;
  }

  if (!authed) {
    return <LoginPage onSuccess={onLoginSuccess} />;
  }

  function handleStopBot() {
    data.botAction("stop");
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
        <Topbar
          dashboard={data.dashboard}
          onBellClick={() => data.showToast("Notifications are coming soon")}
          onThemeClick={() => data.showToast("Theme switcher is coming soon")}
        />

        {data.toast && <div className="toast">{data.toast}</div>}

        {renderPage()}

        <footer className="app-footer">Built with ❤️ for serious traders</footer>
      </main>
    </div>
  );
}

export default App;
