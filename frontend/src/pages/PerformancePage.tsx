import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import Card from "../components/Layout/Card";
import PortfolioAnalytics from "../components/Dashboard/PortfolioAnalytics";
import { fmtNum, fmtPct, fmtUsd, toneClass, toneOf } from "../lib/format";
import { downloadBlob } from "../lib/backtestStats";
import LocalTime from "../components/LocalTime";
import AutoCardTable, { type AutoCardColumn } from "../components/Responsive/AutoCardTable";
import PaperLiveTabs, { type PaperLiveTab } from "../components/Trading/PaperLiveTabs";
import { BINANCE_TRADE_COLUMNS, BinanceTradeDetail } from "../lib/binanceTradeColumns";
import { useBinanceAccount } from "../hooks/useBinanceAccount";
import { api } from "../services/api";
import type { AppData } from "../hooks/useAppData";

const MIN_TRADES_FOR_STATS = 10;

function computeBotStats(trades: any[]) {
  const closed = trades.filter(
    (t) => t.label === "BOT_TRADE" && t.action === "close" && typeof t.realized_pnl === "number"
  );
  if (closed.length < MIN_TRADES_FOR_STATS) return null;

  const wins = closed.filter((t) => t.realized_pnl > 0);
  const losses = closed.filter((t) => t.realized_pnl < 0);
  const grossProfit = wins.reduce((s, t) => s + t.realized_pnl, 0);
  const grossLoss = Math.abs(losses.reduce((s, t) => s + t.realized_pnl, 0));
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

  const ordered = [...closed].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  );
  let cum = 0, peak = 0, maxDrawdown = 0;
  for (const t of ordered) {
    cum += t.realized_pnl;
    peak = Math.max(peak, cum);
    maxDrawdown = Math.max(maxDrawdown, peak - cum);
  }

  return {
    count: closed.length,
    winRate: wins.length / closed.length,
    profitFactor,
    grossProfit,
    grossLoss,
    maxDrawdown,
  };
}

function groupIncomeByType(rows: any[]): { type: string; total: number; count: number }[] {
  const map = new Map<string, { total: number; count: number }>();
  for (const r of rows) {
    const key = r.income_type || "OTHER";
    const entry = map.get(key) || { total: 0, count: 0 };
    entry.total += r.income || 0;
    entry.count += 1;
    map.set(key, entry);
  }
  return Array.from(map.entries()).map(([type, v]) => ({ type, ...v }));
}

const INCOME_COLUMNS: AutoCardColumn<any>[] = [
  { key: "time", label: "Time", render: (r) => new Date(r.time).toLocaleString() },
  { key: "type", label: "Type", render: (r) => r.income_type },
  { key: "symbol", label: "Symbol", render: (r) => r.symbol || "—" },
  {
    key: "amount",
    label: "Amount",
    render: (r) => (
      <span className={toneClass(toneOf(r.income))}>
        {fmtNum(r.income, 6)} {r.asset}
      </span>
    ),
  },
];

const TIMEFRAME_COLUMNS: AutoCardColumn<[string, any]>[] = [
  { key: "tf", label: "Timeframe", render: ([tf]) => tf },
  { key: "predictions", label: "Predictions", render: ([, s]) => s.predictions },
  { key: "hitRate", label: "Hit Rate", render: ([, s]) => <span className={s.hit_rate_pct >= 50 ? "green" : "red"}>{fmtPct(s.hit_rate_pct, 1)}</span> },
  { key: "avgError", label: "Avg Error", render: ([, s]) => fmtPct(s.avg_error_pct, 2) },
];

const RELIABILITY_COLUMNS: AutoCardColumn<any>[] = [
  { key: "bucket", label: "Confidence Bucket", render: (b) => b.bucket },
  { key: "predictions", label: "Predictions", render: (b) => b.predictions },
  { key: "hitRate", label: "Actual Hit Rate", render: (b) => fmtPct(b.hit_rate_pct, 1) },
  { key: "statedConfidence", label: "Stated Confidence", render: (b) => fmtNum(b.avg_confidence, 1) },
];

export default function PerformancePage(props: AppData) {
  const { portfolio, positions, history, learningPerformance, loadLearning, showToast } = props;

  const [tab, setTab] = useState<PaperLiveTab>("paper");
  const [binanceBotTrades, setBinanceBotTrades] = useState<any[]>([]);
  const { summary, incomeRows } = useBinanceAccount(showToast);

  useEffect(() => {
    loadLearning();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    api.botTradesBinance().then((res) => setBinanceBotTrades(res?.trades || [])).catch(() => {});
  }, []);

  const byTimeframe: [string, any][] = Object.entries(learningPerformance?.by_timeframe ?? {});
  const reliability: any[] = (learningPerformance?.confidence_reliability ?? []).filter(
    (b: any) => b.predictions > 0
  );

  const botStats = computeBotStats(binanceBotTrades);
  const incomeByType = groupIncomeByType(incomeRows);

  const handleExportBinancePerformance = () => {
    const lines = ["section,field,value"];
    if (summary?.available) {
      lines.push(`account,wallet_balance,${summary.total_wallet_balance}`);
      lines.push(`account,unrealized_pnl,${summary.unrealized_pnl}`);
      lines.push(`account,daily_realized_pnl,${summary.daily_pnl}`);
      lines.push(`account,notional_exposure,${summary.total_notional_exposure}`);
    }
    if (botStats) {
      lines.push(`bot_stats,closed_trades,${botStats.count}`);
      lines.push(`bot_stats,win_rate_pct,${(botStats.winRate * 100).toFixed(2)}`);
      lines.push(`bot_stats,profit_factor,${botStats.profitFactor.toFixed(2)}`);
      lines.push(`bot_stats,max_drawdown_usd,${botStats.maxDrawdown.toFixed(2)}`);
    }
    for (const row of incomeByType) {
      lines.push(`income,${row.type},${row.total.toFixed(6)}`);
    }
    lines.push("trades,,");
    lines.push("time,symbol,side,action,quantity,avg_fill_price,realized_pnl,label");
    for (const t of binanceBotTrades) {
      lines.push(
        `${t.created_at ?? ""},${t.symbol ?? ""},${t.side ?? ""},${t.action ?? ""},${t.quantity ?? ""},${t.avg_fill_price ?? ""},${t.realized_pnl ?? ""},${t.label ?? ""}`
      );
    }
    downloadBlob(`binance_performance_${new Date().toISOString().slice(0, 10)}.csv`, lines.join("\n"), "text/csv");
  };

  return (
    <div className="page-grid">
      <Card
        title="Performance"
        full
        right={<PaperLiveTabs active={tab} onChange={setTab} />}
      >
        <p className="regime-desc">
          {tab === "paper"
            ? "Simulated equity curve and PnL from the paper trading engine."
            : "Real Binance Futures account performance - wallet balance, PnL, income and closed bot trades, read live from the account."}
        </p>
      </Card>

      {tab === "paper" ? (
        <Card title="Portfolio Analytics" full>
          <PortfolioAnalytics portfolio={portfolio} positions={positions} history={history} />
        </Card>
      ) : (
        <>
          <Card
            title="Real Account Performance"
            full
            className="live-danger-card"
            right={
              <button className="mini-btn" onClick={handleExportBinancePerformance}>
                <Download size={13} /> Export Report
              </button>
            }
          >
            {summary?.available ? (
              <div className="kv-grid">
                <div>
                  <span className="tile-label">Wallet Balance</span>
                  <b className="tile-value">{fmtUsd(summary?.total_wallet_balance)}</b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Unrealized PnL</span>
                  <b className={`tile-value ${toneClass(toneOf(summary?.unrealized_pnl))}`}>{fmtUsd(summary?.unrealized_pnl)}</b>
                </div>
                <div>
                  <span className="tile-label">Daily Realized PnL</span>
                  <b className={`tile-value ${toneClass(toneOf(summary?.daily_pnl))}`}>{fmtUsd(summary?.daily_pnl)}</b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Notional Exposure</span>
                  <b className="tile-value">{fmtUsd(summary?.total_notional_exposure)}</b>
                </div>
              </div>
            ) : (
              <p className="analytics-empty">
                {summary?.reason || "Binance account unavailable - no performance curve is fabricated without real data."}
              </p>
            )}
          </Card>

          <Card title="Bot Trading Stats (Real)" full>
            {botStats ? (
              <div className="kv-grid">
                <div>
                  <span className="tile-label">Closed Bot Trades</span>
                  <b className="tile-value">{botStats.count}</b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Win Rate</span>
                  <b className={`tile-value ${botStats.winRate >= 0.5 ? "green" : "red"}`}>{fmtPct(botStats.winRate, 1)}</b>
                </div>
                <div>
                  <span className="tile-label">Profit Factor</span>
                  <b className={`tile-value ${botStats.profitFactor >= 1 ? "green" : "red"}`}>
                    {Number.isFinite(botStats.profitFactor) ? botStats.profitFactor.toFixed(2) : "∞"}
                  </b>
                </div>
                <div className="align-right">
                  <span className="tile-label">Max Drawdown</span>
                  <b className="tile-value red">{fmtUsd(botStats.maxDrawdown)}</b>
                </div>
              </div>
            ) : (
              <p className="analytics-empty">
                Not enough Binance trade history yet — win rate, profit factor and drawdown need at least{" "}
                {MIN_TRADES_FOR_STATS} closed bot trades ({binanceBotTrades.filter((t) => t.action === "close").length} so far).
              </p>
            )}
          </Card>

          <Card title="Income / Fees / Funding Breakdown (30d)" full>
            {incomeByType.length > 0 ? (
              <div className="kv-grid">
                {incomeByType.map((row) => (
                  <div key={row.type}>
                    <span className="tile-label">{row.type} ({row.count})</span>
                    <b className={`tile-value ${toneClass(toneOf(row.total))}`}>{fmtNum(row.total, 4)} USDT</b>
                  </div>
                ))}
              </div>
            ) : (
              <p className="analytics-empty">Not enough Binance trade history yet.</p>
            )}
          </Card>

          <Card title="Income / Fees / Funding (30d)" full>
            <AutoCardTable
              columns={INCOME_COLUMNS}
              rows={incomeRows.slice(0, 20).map((r: any, i: number) => ({ ...r, _key: i }))}
              keyField={(r: any) => r._key}
              titleColumn="symbol"
              statusColumn="type"
              emptyMessage="No income rows"
            />
          </Card>

          <Card title="Closed Bot Trades" full>
            <AutoCardTable
              columns={BINANCE_TRADE_COLUMNS}
              rows={binanceBotTrades.slice(0, 30)}
              keyField={(t: any) => t.id}
              titleColumn="symbol"
              statusColumn="side"
              renderDetail={(t: any) => <BinanceTradeDetail t={t} />}
              emptyMessage="No closed Binance bot trades yet"
            />
          </Card>

          <Card title="Equity History">
            <p className="analytics-empty">
              Equity history is not tracked yet — account snapshots are read live, not stored over time. This will
              populate once periodic account snapshots are persisted.
            </p>
          </Card>
        </>
      )}

      <Card title="Prediction Accuracy by Timeframe">
        {byTimeframe.length ? (
          <>
            <p className="regime-desc">
              From the learning loop's last evaluation (<LocalTime value={learningPerformance.evaluated_at} label="Evaluated" />) —
              predictions resolved against recorded outcomes only.
            </p>
            <div style={{ marginTop: 12 }}>
              <AutoCardTable columns={TIMEFRAME_COLUMNS} rows={byTimeframe} keyField={([tf]) => tf} titleColumn="tf" />
            </div>
          </>
        ) : (
          <p className="analytics-empty">
            No learning evaluation yet — run "Evaluate Prediction History" in the Research Lab.
          </p>
        )}
      </Card>

      <Card title="Model Confidence Reliability">
        {reliability.length ? (
          <>
            <p className="regime-desc">
              How often each stated-confidence bucket was actually right. A calibrated model's hit rate tracks its
              bucket.
            </p>
            <div style={{ marginTop: 12 }}>
              <AutoCardTable columns={RELIABILITY_COLUMNS} rows={reliability} keyField={(b) => b.bucket} titleColumn="bucket" />
            </div>
          </>
        ) : (
          <p className="analytics-empty">
            No calibration data yet — run "Evaluate Prediction History" in the Research Lab.
          </p>
        )}
      </Card>
    </div>
  );
}
