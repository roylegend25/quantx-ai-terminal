import { lazy, Suspense, useCallback, useState } from "react";
import "./App.css";
import "./premium/index.css";
import Sidebar from "./components/Layout/Sidebar";
import MobileBottomNav from "./components/Layout/MobileBottomNav";
import Topbar from "./components/Layout/Topbar";
import LoginPage from "./components/Auth/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import { useAppData } from "./hooks/useAppData";
import { useAuth } from "./hooks/useAuth";
import { useTheme } from "./hooks/useTheme";
import { useDesignSystem } from "./hooks/useDesignSystem";
import { useMediaQuery } from "./hooks/useMediaQuery";
import type { NavKey } from "./lib/nav";
import PremiumSidebar from "./premium/components/PremiumSidebar";
import PremiumTopbar from "./premium/components/PremiumTopbar";
import PremiumBottomNav from "./premium/components/PremiumBottomNav";
import PremiumDashboardPage from "./premium/pages/PremiumDashboardPage";

// Lazy, matching the Classic pages they wrap below - each of these
// statically imports its Classic counterpart (PositionsPage etc.), so
// keeping them eager would pull those Classic bundles into the main chunk
// even when Premium mode is off (see INEFFECTIVE_DYNAMIC_IMPORT build warning
// this fixes).
const PremiumPositionsPage = lazy(() => import("./premium/pages/PremiumPositionsPage"));
const PremiumPortfolioPage = lazy(() => import("./premium/pages/PremiumPortfolioPage"));
const PremiumBinanceRealPage = lazy(() => import("./premium/pages/PremiumBinanceRealPage"));
const PremiumPerformancePage = lazy(() => import("./premium/pages/PremiumPerformancePage"));
const PremiumGenericPage = lazy(() => import("./premium/pages/PremiumGenericPage"));

const PortfolioPage = lazy(() => import("./pages/PortfolioPage"));
const PaperTradingPage = lazy(() => import("./pages/PaperTradingPage"));
const BinanceRealPage = lazy(() => import("./pages/BinanceRealPage"));
const BotTradesPage = lazy(() => import("./pages/BotTradesPage"));
const PredictionsPage = lazy(() => import("./pages/PredictionsPage"));
const PredictionResultsPage = lazy(() => import("./pages/PredictionResultsPage"));
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
const TradingDiagnosticsPage = lazy(() => import("./pages/TradingDiagnosticsPage"));
const HyperliquidPage = lazy(() => import("./pages/HyperliquidPage"));

function PageFallback() {
  return <div className="page-fallback" aria-hidden="true" />;
}

function App() {
  const { authed, login: onLoginSuccess, logout } = useAuth();
  const [active, setActive] = useState<NavKey>("dashboard");
  const data = useAppData(authed);
  const theme = useTheme();
  const designSystem = useDesignSystem();
  const isPremium = designSystem.mode === "premium";
  const isPhoneNav = useMediaQuery("(max-width: 767px)");

  const handleStopBot = useCallback(() => {
    data.botAction("stop");
  }, [data.botAction]);

  if (authed === null) {
    return null;
  }

  if (!authed) {
    return <LoginPage onSuccess={onLoginSuccess} />;
  }

  function renderClassicPage() {
    switch (active) {
      case "dashboard":
        return <DashboardPage {...data} navigate={setActive} />;
      case "portfolio":
        return <PortfolioPage {...data} navigate={setActive} />;
      case "paper-trading":
        return <PaperTradingPage {...data} />;
      case "binance-real":
        return <BinanceRealPage {...data} />;
      case "bot-trades":
        return <BotTradesPage />;
      case "predictions":
        return <PredictionsPage {...data} />;
      case "prediction-results":
        return <PredictionResultsPage showToast={data.showToast} />;
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
      case "trading-diagnostics":
        return <TradingDiagnosticsPage {...data} />;
      case "hyperliquid":
        return <HyperliquidPage {...data} />;
      default:
        return <DashboardPage {...data} navigate={setActive} />;
    }
  }

  /** Bespoke Premium pages get hero-quality custom compositions; the
   * remaining ~12 pages reuse their existing, unmodified Classic component
   * inside one shared PremiumGenericPage shell (see plan: Tier B). */
  function renderPage() {
    if (!isPremium) return renderClassicPage();

    switch (active) {
      case "dashboard":
        return <PremiumDashboardPage {...data} navigate={setActive} />;
      case "positions":
        return <PremiumPositionsPage {...data} navigate={setActive} />;
      case "portfolio":
        return <PremiumPortfolioPage {...data} navigate={setActive} />;
      case "binance-real":
        return <PremiumBinanceRealPage {...data} />;
      case "performance":
        return <PremiumPerformancePage {...data} />;
      default:
        return <PremiumGenericPage activeKey={active}>{renderClassicPage()}</PremiumGenericPage>;
    }
  }

  const botStatusLabel = (data.botStatus?.status || data.dashboard?.bot?.status || "").toUpperCase();

  return (
    <div className={isPremium ? "app qp-app" : "app"}>
      {isPhoneNav ? (
        isPremium ? (
          <PremiumBottomNav
            active={active}
            onNavigate={setActive}
            onStopBot={handleStopBot}
            onLogout={logout}
            isBotLive={botStatusLabel === "RUNNING"}
          />
        ) : (
          <MobileBottomNav
            active={active}
            onNavigate={setActive}
            onStopBot={handleStopBot}
            onLogout={logout}
            isBotLive={botStatusLabel === "RUNNING"}
          />
        )
      ) : isPremium ? (
        <PremiumSidebar
          active={active}
          onNavigate={setActive}
          botStatus={data.botStatus}
          dashboard={data.dashboard}
          onStopBot={handleStopBot}
          onLogout={logout}
        />
      ) : (
        <Sidebar
          active={active}
          onNavigate={setActive}
          botStatus={data.botStatus}
          dashboard={data.dashboard}
          onStopBot={handleStopBot}
          onLogout={logout}
        />
      )}

      <main className={isPhoneNav ? "main main-has-bottom-nav" : "main"}>
        {isPremium ? (
          <PremiumTopbar dashboard={data.dashboard} theme={theme} designSystem={designSystem} activeKey={active} />
        ) : (
          <Topbar dashboard={data.dashboard} theme={theme} designSystem={designSystem} activeKey={active} />
        )}

        {data.toast && <div className={`toast ${data.toastTone === "error" ? "toast-error" : ""}`}>{data.toast}</div>}

        <Suspense fallback={<PageFallback />}>{renderPage()}</Suspense>

        {!isPremium && <footer className="app-footer">Built with ❤️ for serious traders</footer>}
      </main>
    </div>
  );
}

export default App;
