import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";

const POLL_MS = 10000;

export type ConfirmState = { title: string; body: string; action: () => Promise<void> } | null;

/** Single source of truth for "Binance real account data + actions" -
 *  extracted from what BinanceRealPage.tsx used to manage inline so the
 *  same live positions/orders/trades/income and the same close/cancel/
 *  edit-risk actions can be reused on other pages' Binance-Live tabs
 *  without re-fetching or re-implementing them. Never touches paper data -
 *  every call here hits the read-only/audited Binance-real endpoints only. */
export function useBinanceAccount(showToast: (message: string, tone?: "success" | "error") => void) {
  const [summary, setSummary] = useState<any>(null);
  const [balances, setBalances] = useState<any>(null);
  const [positions, setPositions] = useState<any>(null);
  const [orders, setOrders] = useState<any>(null);
  const [trades, setTrades] = useState<any>(null);
  const [income, setIncome] = useState<any>(null);

  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmState>(null);

  const load = useCallback(async () => {
    const [s, b, p, o, t, inc] = await Promise.all([
      api.binanceSummary().catch(() => null),
      api.binanceBalances().catch(() => null),
      api.binancePositions().catch(() => null),
      api.binanceOrders().catch(() => null),
      api.binanceTrades().catch(() => null),
      api.binanceIncome(30).catch(() => null),
    ]);
    if (s) setSummary(s);
    if (b) setBalances(b);
    if (p) setPositions(p);
    if (o) setOrders(o);
    if (t) setTrades(t);
    if (inc) setIncome(inc);
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const run = useCallback(
    async (fn: () => Promise<any>, okMessage: string) => {
      setBusy(true);
      try {
        await fn();
        showToast(okMessage, "success");
      } catch (e: any) {
        showToast(e?.message || "Action failed", "error");
      } finally {
        setBusy(false);
        setConfirm(null);
        await load();
      }
    },
    [showToast, load]
  );

  const saveRisk = useCallback(
    async (id: number, patch: { stop_loss?: number | null; take_profit?: number | null }) => {
      await api.binanceUpdatePositionRisk(id, { stop_loss: patch.stop_loss, take_profit: patch.take_profit });
      showToast("Binance Live TP/SL orders updated", "success");
      await load();
    },
    [showToast, load]
  );

  return {
    summary,
    balances,
    positions,
    orders,
    trades,
    income,
    balanceRows: balances?.balances || [],
    positionRows: positions?.positions || [],
    orderRows: orders?.orders || [],
    tradeRows: trades?.trades || [],
    incomeRows: income?.income || [],
    busy,
    confirm,
    setConfirm,
    run,
    saveRisk,
    reload: load,
  };
}
