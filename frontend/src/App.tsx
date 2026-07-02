
import { useEffect, useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  Activity,
  Bot,
  Brain,
  ChartCandlestick,
  LogOut,
  PauseCircle,
  PlayCircle,
  Shield,
  StopCircle,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import "./App.css";
import StrategyVotes from "./components/Dashboard/StrategyVotes";
import BacktestCard from "./components/Dashboard/BacktestCard";
import PortfolioAnalytics from "./components/Dashboard/PortfolioAnalytics";
import AIDecisionFlow from "./components/Dashboard/AIDecisionFlow";
import MarketRegimePanel from "./components/Dashboard/MarketRegimePanel";
import AIDecisionCenter from "./components/Dashboard/AIDecisionCenter";
import LoginPage from "./components/Auth/LoginPage";
import { authFetch, getToken, logout } from "./services/auth";

const API = "";

type DashboardData = any;
type PredictionData = any;
type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

function Card({ title, children, wide = false, full = false }: any) {
  return (
    <div className={`card ${wide ? "wide" : ""} ${full ? "full" : ""}`}>
      <div className="card-title">{title}</div>
      {children}
    </div>
  );
}

function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [dashboard, setDashboard] = useState<DashboardData>(null);
  const [prediction, setPrediction] = useState<PredictionData>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [toast, setToast] = useState("");
  const [orderbook, setOrderbook] = useState<any>(null);
  const [trades, setTrades] = useState<any[]>([]);
  const [portfolio, setPortfolio] = useState<any>(null);
  const [positions, setPositions] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [backtest, setBacktest] = useState<any>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  async function load() {
    const [dashRes, predRes, candlesRes] = await Promise.all([
      authFetch(`${API}/api/dashboard`),
      authFetch(`${API}/api/prediction/${symbol}`),
      authFetch(`${API}/api/market/${symbol}/candles?limit=220`),
      authFetch(`${API}/api/orderbook/${symbol}?limit=10`),
    ]);

    const dash = await dashRes.json();
    const pred = await predRes.json();
    const c = await candlesRes.json();

    setDashboard(dash);
    setPrediction(pred.prediction);
    try {
      const obRes = await authFetch(`${API}/api/orderbook/${symbol}?limit=10`);
      setOrderbook(await obRes.json());

    const tradesRes = await authFetch(`${API}/api/trades/${symbol}?limit=20`);
    const tradesData = await tradesRes.json();
    setTrades(tradesData.trades || []);

    const portfolioRes = await authFetch(`${API}/api/paper/portfolio`);
    setPortfolio(await portfolioRes.json());

    const positionsRes = await authFetch(`${API}/api/paper/positions`);
    const positionsData = await positionsRes.json();
    setPositions(positionsData.positions || []);

    const historyRes = await authFetch(`${API}/api/paper/history`);
    const historyData = await historyRes.json();
    setHistory(historyData.trades || []);
    } catch {}
    setCandles(
      c.map((x: Candle) => ({
        ...x,
        label: new Date(x.time).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
      }))
    );
    setLastUpdated(new Date());
  }

  
async function runBacktest() {
  const res = await authFetch(`${API}/api/backtest/run?symbol=${symbol}&interval=5m`);
  const data = await res.json();
  const results = data.results || data;

  if (results.equity_curve) {
    results.chart = results.equity_curve.map((v: number, i: number) => ({
      trade: i,
      equity: v,
    }));
  }

  setBacktest(results);
}

async function openPaperTrade(side: "LONG" | "SHORT") {
  await authFetch(`${API}/api/paper/open?symbol=${symbol}&side=${side}&usdt_size=1000`, {
    method: "POST",
  });
  load();
}

async function closePaperTrade(id:number){
  await authFetch(`${API}/api/paper/close/${id}`,{
    method:"POST"
  });
  load();
}

async function botAction(action: string) {

    const res = await authFetch(`${API}/api/bot/${action}`, { method: "POST" });
    const data = await res.json();
    setToast(data.message);
    setTimeout(() => setToast(""), 3000);
  }

  useEffect(() => {
    if (!getToken()) {
      setAuthed(false);
      return;
    }
    authFetch(`${API}/api/auth/me`).then((res) => setAuthed(res.ok));
  }, []);

  function handleLogout() {
    logout();
    setAuthed(false);
  }

  useEffect(() => {
    if (!authed) return;
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [symbol, authed]);

  if (authed === null) {
    return null;
  }

  if (!authed) {
    return <LoginPage onSuccess={() => setAuthed(true)} />;
  }

  const market = dashboard?.symbols?.[symbol];
  const ticker = market?.ticker;
  const funding = market?.funding;
  const oi = market?.open_interest;

  const lastPrice = Number(ticker?.lastPrice || 0);
  const change = Number(ticker?.priceChangePercent || 0);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">QX</div>
          <div>
            <h1>QuantX AI</h1>
            <p>Futures Terminal</p>
          </div>
        </div>

        {[
          "Dashboard",
          "AI Prediction",
          "Quant Signals",
          "Live Chart",
          "Risk Engine",
          "Paper Trading",
          "Performance",
          "Settings",
        ].map((x, i) => (
          <button className={i === 0 ? "nav active" : "nav"} key={x}>
            {x}
          </button>
        ))}

        <div className="status-box">
          <Bot size={22} />
          <div>
            <b>{dashboard?.bot?.status || "loading"}</b>
            <span>{dashboard?.mode || "paper"} mode</span>
          </div>
          <button className="logout-btn" onClick={handleLogout} title="Log out">
            <LogOut size={18} />
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">Live Futures Dashboard</p>
            <h2>{symbol}</h2>
          </div>

          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            <option>BTCUSDT</option>
            <option>ETHUSDT</option>
          </select>
        </header>

        {toast && <div className="toast">{toast}</div>}

        <section className="hero">
          <div>
            <p className="eyebrow">Current Price</p>
            <h3>${lastPrice.toLocaleString()}</h3>
            <span className={change >= 0 ? "green" : "red"}>
              {change >= 0 ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
              {change.toFixed(2)}%
            </span>
          </div>

          <div>
            <p className="eyebrow">AI Direction</p>
            <h3>{prediction?.direction || "Loading"}</h3>
            <span>{prediction?.confidence || 0}% confidence</span>
          </div>

          <div>
            <p className="eyebrow">Funding Rate</p>
            <h3>{Number(funding?.lastFundingRate || 0).toFixed(5)}%</h3>
            <span>next funding live</span>
          </div>

          <div>
            <p className="eyebrow">Open Interest</p>
            <h3>{Number(oi?.openInterest || 0).toLocaleString()}</h3>
            <span>contracts</span>
          </div>
        </section>

        <section className="grid">
          <Card title={`${symbol} Live Chart`} wide>
            <ResponsiveContainer width="100%" height={360}>
              <AreaChart data={candles}>
                <defs>
                  <linearGradient id="price" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#7c5cff" stopOpacity={0.5} />
                    <stop offset="95%" stopColor="#7c5cff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="label" />
                <YAxis domain={["dataMin", "dataMax"]} />
                <Tooltip />
                <Area
                  type="monotone"
                  dataKey="close"
                  stroke="#7c5cff"
                  fill="url(#price)"
                  strokeWidth={3}
                />
              </AreaChart>
            </ResponsiveContainer>
          </Card>

          <Card title="AI Prediction">
            <div className="ai-panel">
              <Brain size={42} />
              <h3>{prediction?.direction || "..."}</h3>
              <p>Probability Up: {prediction?.probability_up || 0}%</p>
              <p>Probability Down: {prediction?.probability_down || 0}%</p>
              <progress value={prediction?.confidence || 0} max={100} />
            </div>
          </Card>

          <Card title="Trade Plan">
            <p>Entry: <b>${prediction?.price || 0}</b></p>
            <p>Target: <b className="green">${prediction?.target || 0}</b></p>
            <p>Stop: <b className="red">${prediction?.stop || 0}</b></p>
            <p>Quality: <b>{prediction?.trade_quality || 0}/10</b></p>
          </Card>

          <Card title="Quant Signals">
            <p>Regime: <b>{prediction?.regime}</b></p>
            <p>RSI: <b>{prediction?.features?.rsi?.toFixed?.(2)}</b></p>
            <p>EMA20: <b>{prediction?.features?.ema20?.toFixed?.(2)}</b></p>
            <p>EMA50: <b>{prediction?.features?.ema50?.toFixed?.(2)}</b></p>
            <p>ATR: <b>{prediction?.features?.atr?.toFixed?.(2)}</b></p>
          </Card>

          <Card title="Market Regime" wide>
            <MarketRegimePanel prediction={prediction} />
          </Card>

          <Card title="AI Decision Flow" full>
            <AIDecisionFlow prediction={prediction} ticker={ticker} />
          </Card>

          <Card title="AI Decision Center" full>
            <AIDecisionCenter symbol={symbol} prediction={prediction} lastUpdated={lastUpdated} />
          </Card>

          <Card title="Live Order Book">
            <div className="orderbook">
              <div>
                <h4 className="green">Bids</h4>
                {orderbook?.bids?.slice(0, 8).map((x: any, i: number) => (
                  <div className="book-row" key={"b" + i}>
                    <span>{x.price.toFixed(1)}</span>
                    <b>{x.qty.toFixed(3)}</b>
                  </div>
                ))}
              </div>
              <div>
                <h4 className="red">Asks</h4>
                {orderbook?.asks?.slice(0, 8).map((x: any, i: number) => (
                  <div className="book-row" key={"a" + i}>
                    <span>{x.price.toFixed(1)}</span>
                    <b>{x.qty.toFixed(3)}</b>
                  </div>
                ))}
              </div>
            </div>
            <p className="spread">Spread: ${orderbook?.spread}</p>
          </Card>

          <Card title="Trade Tape">
            <div className="trade-tape">
              {trades.slice(0, 12).map((x: any) => (
                <div className="trade-row" key={x.id}>
                  <span className={x.side === "BUY" ? "green" : "red"}>{x.side}</span>
                  <b>{x.price.toFixed(1)}</b>
                  <span>{x.qty.toFixed(3)}</span>
                
<button
onClick={()=>closePaperTrade(x.id)}
style={{
padding:'6px 10px',
borderRadius:10,
cursor:'pointer'
}}>
Close
</button>
</div>
              ))}

            </div>
          </Card>

          <Card title="Risk Engine">
            <Shield size={34} />
            <h3>{prediction?.risk?.allowed ? "TRADE ALLOWED" : "NO TRADE"}</h3>
            <p>{prediction?.risk?.reason}</p>
            <p>Max risk: {prediction?.risk?.max_risk_per_trade_pct}%</p>
          </Card>

          <Card title="Paper Portfolio">
            <p>Balance: <b>${portfolio?.balance?.toLocaleString?.() || 0}</b></p>
            <p>Equity: <b>${portfolio?.equity?.toLocaleString?.() || 0}</b></p>
            <p>Unrealized PnL: <b className={(portfolio?.unrealized_pnl || 0) >= 0 ? "green" : "red"}>${portfolio?.unrealized_pnl || 0}</b></p>
            <p>Total PnL: <b className={(portfolio?.total_pnl || 0) >= 0 ? "green" : "red"}>${portfolio?.total_pnl || 0}</b></p>
            <p>Win Rate: <b>{portfolio?.win_rate || 0}%</b></p>
          </Card>

          <Card title="Portfolio Analytics" full>
            <PortfolioAnalytics portfolio={portfolio} positions={positions} history={history} />
          </Card>

          <Card title="Open Positions">
            <div className="positions">
              {positions.length === 0 && <p>No open paper positions</p>}
              {positions.map((x: any) => (
                <div className="position-row" key={x.id}>
                  <div>
                    <b>{x.symbol}</b>
                    <span>{x.side}</span>
                  </div>
                  <div>
                    <span>Entry</span>
                    <b>{x.entry}</b>
                  </div>
                  <div>
                    <span>Mark</span>
                    <b>{x.mark}</b>
                  </div>
                  <div>
                    <span>PnL</span>
                    <b className={x.pnl >= 0 ? "green" : "red"}>${x.pnl}</b>
                  </div>
                
<button
onClick={()=>closePaperTrade(x.id)}
style={{
padding:'6px 10px',
borderRadius:10,
cursor:'pointer'
}}>
Close
</button>
</div>
              ))}

            </div>
          </Card>

          

<Card title="Backtest Lab">
  <BacktestCard
    backtest={backtest}
    runBacktest={runBacktest}
  />
</Card>


<Card title="Strategy Votes">
  <StrategyVotes strategies={prediction?.strategies} />
</Card>

<Card title="Paper Trading">

<div className="controls">
<button onClick={()=>openPaperTrade("LONG")}>🟢 Open Long</button>
<button onClick={()=>openPaperTrade("SHORT")}>🔴 Open Short</button>
</div>
</Card>

<Card title="Trade Journal" wide>
            <div className="journal">
              {history.slice(0, 10).map((x: any) => (
                <div className="journal-row" key={x.id}>
                  <b>{x.symbol}</b>
                  <span>{x.side}</span>
                  <span>{x.status}</span>
                  <span>Entry {x.entry}</span>
                  <span>Exit {x.exit || "-"}</span>
                  <b className={(x.pnl || 0) >= 0 ? "green" : "red"}>${x.pnl}</b>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Bot Controls" wide>
            <div className="controls">
              <button onClick={() => botAction("start")}><PlayCircle /> Start</button>
              <button onClick={() => botAction("pause")}><PauseCircle /> Pause</button>
              <button onClick={() => botAction("stop")}><StopCircle /> Stop</button>
              <button onClick={() => botAction("paper")}><Shield /> Paper</button>
              <button onClick={() => botAction("live")}><Activity /> Live</button>
              <button onClick={load}><ChartCandlestick /> Refresh</button>
            </div>
          </Card>
        </section>
      </main>
    </div>
  );
}

export default App;