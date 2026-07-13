import type { TradingStatus } from "./TradingShared";

/** Single source of truth for "is a Binance Live action allowed right now,
 *  and if not, exactly why" - replaces the repeated `disabled={busy ||
 *  !isLive}` / `title={!isLive ? "Locked..." : ""}` pattern that used to be
 *  copy-pasted across BinanceRealPage, PositionsPage, BinancePositionsTable,
 *  RiskPage, ExecutionPage and BotSettingsPage. Every field here is derived
 *  from the same safe status TradingShared.useTradingStatus already polls
 *  (GET /api/trading/mode) - no new network calls, no behavior change to
 *  what's currently allowed, just one shared, specific reason string.
 *
 *  canView is true whenever the account can be viewed (Binance is
 *  configured) - it matches the existing "locked view" behavior in
 *  LiveUnlockModal, where switching to the Binance Real tab/page is always
 *  allowed even while trading stays locked. canTrade and the narrower
 *  can* flags require every real-money gate to be open. */

export type BinanceLiveGate = {
  canView: boolean;
  canTrade: boolean;
  canEditRisk: boolean;
  canCancelOrders: boolean;
  canClosePositions: boolean;
  /** First blocking reason, in priority order, or null when canTrade. Use
   *  this directly as a disabled button's title/tooltip. */
  disabledReason: string | null;
  /** Cancel-specific reason, or null when canCancelOrders. Canceling a
   *  resting real order is a de-risking safety action (Debug 1.pdf
   *  section 2), not order placement - it must stay available whenever a
   *  real account exists, regardless of active trading mode, the
   *  server/user live locks, or the kill switch, so it is never blocked
   *  by the same reasons trade placement is. */
  cancelDisabledReason: string | null;
  /** Ordered list of every unmet requirement (for a checklist view). */
  requiredSteps: string[];
};

type Opts = {
  symbol?: string;
  balanceAvailable?: boolean;
};

const NO_STATUS_REASON = "Disabled because Binance status has not loaded yet";

export function evaluateBinanceLiveGate(status: TradingStatus | null, opts: Opts = {}): BinanceLiveGate {
  if (!status) {
    return {
      canView: false,
      canTrade: false,
      canEditRisk: false,
      canCancelOrders: false,
      canClosePositions: false,
      disabledReason: NO_STATUS_REASON,
      cancelDisabledReason: NO_STATUS_REASON,
      requiredSteps: [NO_STATUS_REASON],
    };
  }

  const checks: { ok: boolean; reason: string }[] = [
    { ok: !!status.binance_configured, reason: "Disabled because Binance API keys are not configured" },
    { ok: status.binance_connected !== false, reason: "Disabled because the Binance account is not connected" },
    { ok: !!status.binance_live_enabled_by_server, reason: "Disabled because Server Live Lock is OFF" },
    { ok: !!status.binance_live_unlocked_by_user, reason: "Disabled because User Live Unlock is locked" },
    {
      ok: status.active_mode === "BINANCE_LIVE",
      reason:
        status.active_mode === "PAPER"
          ? "Disabled because active mode is PAPER"
          : "Disabled because Binance Live mode is locked - complete the live-risk confirmation",
    },
    { ok: !status.kill_switch_active, reason: "Disabled because Kill Switch is active" },
  ];

  if (opts.symbol) {
    const allowed = (status.allowed_symbols || []).includes(opts.symbol.toUpperCase());
    checks.push({ ok: allowed, reason: `Disabled because ${opts.symbol.toUpperCase()} is not an allowed symbol` });
  }

  if (opts.balanceAvailable === false) {
    checks.push({ ok: false, reason: "Disabled because available balance is insufficient" });
  }

  const requiredSteps = checks.filter((c) => !c.ok).map((c) => c.reason);
  const canTrade = requiredSteps.length === 0;

  // Cancel is deliberately narrower than the trade checks above: it only
  // needs a real, reachable Binance account - not the server/user live
  // locks or the kill switch, which exist to gate NEW order placement.
  const cancelChecks: { ok: boolean; reason: string }[] = [
    { ok: !!status.binance_configured, reason: "Disabled because Binance API keys are not configured" },
    { ok: status.binance_connected !== false, reason: "Disabled because the Binance account is not connected" },
  ];
  const cancelRequiredSteps = cancelChecks.filter((c) => !c.ok).map((c) => c.reason);
  const canCancelOrders = cancelRequiredSteps.length === 0;

  return {
    canView: !!status.binance_configured,
    canTrade,
    canEditRisk: canTrade,
    canCancelOrders,
    canClosePositions: canTrade,
    disabledReason: requiredSteps[0] ?? null,
    cancelDisabledReason: cancelRequiredSteps[0] ?? null,
    requiredSteps,
  };
}

/** Hook form for components that already hold a `TradingStatus | null` from
 *  useTradingStatus() - kept as a thin wrapper so call sites read like a
 *  hook (`const gate = useBinanceLiveGate(status)`) even though today it's
 *  pure derivation with no internal state of its own. */
export function useBinanceLiveGate(status: TradingStatus | null, opts?: Opts): BinanceLiveGate {
  return evaluateBinanceLiveGate(status, opts);
}
