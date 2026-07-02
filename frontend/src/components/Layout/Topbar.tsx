import { useEffect, useState } from "react";
import { Bell, Sun, UserRound } from "lucide-react";

type Props = {
  dashboard: any;
  onBellClick: () => void;
  onThemeClick: () => void;
};

function tickerRow(label: string, value: string, tone?: "green" | "red") {
  return (
    <div className="ticker-item" key={label}>
      <span className="ticker-label">{label}</span>
      <b className={tone ? tone : ""}>{value}</b>
    </div>
  );
}

export default function Topbar({ dashboard, onBellClick, onThemeClick }: Props) {
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const btc = dashboard?.symbols?.BTCUSDT;
  const eth = dashboard?.symbols?.ETHUSDT;

  const btcPrice = Number(btc?.ticker?.lastPrice || 0);
  const btcChange = Number(btc?.ticker?.priceChangePercent || 0);
  const ethPrice = Number(eth?.ticker?.lastPrice || 0);
  const ethChange = Number(eth?.ticker?.priceChangePercent || 0);
  const funding = Number(btc?.funding?.lastFundingRate || 0) * 100;
  const oi = Number(btc?.open_interest?.openInterest || 0);
  const volume = Number(btc?.ticker?.quoteVolume || 0);

  return (
    <header className="topbar">
      <div className="topbar-brand">
        <div className="brand-icon small">QX</div>
        <div>
          <h2>BTC PREDICTION BOT</h2>
          <p className="eyebrow">FUTURES TRADING</p>
        </div>
      </div>

      <div className="ticker-strip">
        {tickerRow("BTCUSDT", `$${btcPrice.toLocaleString()}`, btcChange >= 0 ? "green" : "red")}
        {tickerRow("ETHUSDT", `$${ethPrice.toLocaleString()}`, ethChange >= 0 ? "green" : "red")}
        {tickerRow("Funding Rate", `${funding.toFixed(4)}%`)}
        {tickerRow("Open Interest", fmtCompact(oi))}
        {tickerRow("24h Volume", `$${fmtCompact(volume)}`)}
        {tickerRow("24h Change", `${btcChange >= 0 ? "+" : ""}${btcChange.toFixed(2)}%`, btcChange >= 0 ? "green" : "red")}
      </div>

      <div className="topbar-actions">
        <span className="clock">{now.toUTCString().slice(17, 25)} UTC</span>
        <button className="icon-btn" onClick={onBellClick} title="Notifications (coming soon)">
          <Bell size={18} />
        </button>
        <button className="icon-btn" onClick={onThemeClick} title="Theme (coming soon)">
          <Sun size={18} />
        </button>
        <div className="avatar">
          <UserRound size={18} />
        </div>
      </div>
    </header>
  );
}

function fmtCompact(n: number): string {
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(n);
}
