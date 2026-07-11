/** Reusable Paper / Binance Live segmented control for pages that show
 *  the same section split by execution venue (Positions, Performance, Bot
 *  Settings, Risk Management, Execution). Built on the existing
 *  .mode-toggle/.mode-toggle-btn CSS already used by BotTradesPage.tsx and
 *  TradingShared.tsx's ModeToggle - no new styling needed. */

export type PaperLiveTab = "paper" | "binance";

type Props = {
  active: PaperLiveTab;
  onChange: (tab: PaperLiveTab) => void;
  paperLabel?: string;
  binanceLabel?: string;
};

export default function PaperLiveTabs({ active, onChange, paperLabel = "Paper Trading", binanceLabel = "Binance Live Trading" }: Props) {
  return (
    <div className="mode-toggle" role="tablist" aria-label="Paper or Binance Live data">
      <button
        role="tab"
        aria-selected={active === "paper"}
        className={`mode-toggle-btn ${active === "paper" ? "on paper" : ""}`}
        onClick={() => onChange("paper")}
      >
        {paperLabel}
      </button>
      <button
        role="tab"
        aria-selected={active === "binance"}
        className={`mode-toggle-btn ${active === "binance" ? "on live" : ""}`}
        onClick={() => onChange("binance")}
      >
        {binanceLabel}
      </button>
    </div>
  );
}
