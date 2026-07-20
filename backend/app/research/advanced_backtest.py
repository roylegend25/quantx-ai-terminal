"""Phase 20 advanced backtesting engine.

Builds on the Phase 16 lab (lab_strategies signals + execution_sim cost
model + metrics) and adds what a serious research backtest needs:

- candles come from the validated market_candles store (DB), falling back to
  the legacy CSV history only if the DB has nothing for the combination
- multi-symbol × multi-timeframe runs in one request
- trailing stops, max holding time, daily loss limits and a drawdown
  circuit breaker in the simulator
- in-sample/out-of-sample split, rolling walk-forward and Monte Carlo
- extended metrics: consecutive losses, long/short accuracy, per-regime and
  per-timeframe performance, per-strategy contribution
- named presets (Scalping ... Low Volatility)

Everything is historical replay over stored, quality-scored data. Nothing
here touches the live prediction or paper-trading path.
"""

import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import pandas as pd

from app.backtest.indicators import add_indicators
from app.data_sources.downloader import load_candles
from app.data_sources.normalizer import TIMEFRAMES_MS
from app.data_sources.validator import MIN_USABLE_QUALITY
from app.db.models import AdvancedBacktestRun, DataQualityReport
from app.db.session import SessionLocal
from app.research import execution_sim, lab_strategies, monte_carlo
from app.research.execution_sim import CostConfig, RiskConfig
from app.research.lab_engine import drawdown_curve, history_path
from app.research.metrics import average_metrics, compute_metrics


@dataclass
class AdvancedRiskConfig(RiskConfig):
    trailing_stop_atr_mult: float = 0.0  # 0 = no trailing stop
    max_holding_bars: int = 0  # 0 = unlimited
    daily_loss_limit_pct: float = 0.0  # 0 = no daily circuit breaker
    max_drawdown_stop_pct: float = 0.0  # 0 = no drawdown circuit breaker


PRESETS: dict[str, dict] = {
    "scalping": {
        "timeframes": ["1m", "3m", "5m"],
        "atr_sl_mult": 1.0, "atr_tp_mult": 1.5, "entry_confidence_threshold": 55.0,
        "trailing_stop_atr_mult": 0.5, "max_holding_bars": 30,
        "commission_pct": 0.04, "slippage_bps": 3.0, "latency_ms": 150.0,
    },
    "intraday": {
        "timeframes": ["5m", "15m", "30m"],
        "atr_sl_mult": 1.5, "atr_tp_mult": 2.5, "entry_confidence_threshold": 50.0,
        "trailing_stop_atr_mult": 1.0, "max_holding_bars": 96, "daily_loss_limit_pct": 3.0,
    },
    "swing": {
        "timeframes": ["4h", "6h", "12h", "1d"],
        "atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "entry_confidence_threshold": 50.0,
        "trailing_stop_atr_mult": 1.5, "max_holding_bars": 0,
    },
    "trend_following": {
        "timeframes": ["1h", "4h"],
        "atr_sl_mult": 2.0, "atr_tp_mult": 5.0, "entry_confidence_threshold": 50.0,
        "trailing_stop_atr_mult": 2.0,
        "strategy": "trend",
    },
    "mean_reversion": {
        "timeframes": ["15m", "1h"],
        "atr_sl_mult": 1.2, "atr_tp_mult": 1.8, "entry_confidence_threshold": 55.0,
        "max_holding_bars": 48,
        "strategy": "mean_reversion",
    },
    "high_volatility": {
        "timeframes": ["5m", "15m"],
        "atr_sl_mult": 2.5, "atr_tp_mult": 4.0, "entry_confidence_threshold": 65.0,
        "daily_loss_limit_pct": 2.0, "max_drawdown_stop_pct": 10.0, "slippage_bps": 5.0,
    },
    "low_volatility": {
        "timeframes": ["1h", "4h"],
        "atr_sl_mult": 1.0, "atr_tp_mult": 2.0, "entry_confidence_threshold": 45.0,
        "trailing_stop_atr_mult": 0.8,
    },
}

CONTRIBUTION_STRATEGIES = ["trend", "momentum", "mean_reversion", "breakout"]


class DataNotAvailableError(Exception):
    pass


# ---------------------------------------------------------------- data

def load_history_db(
    symbol: str,
    timeframe: str,
    start_date: str | None = None,
    end_date: str | None = None,
    db=None,
) -> tuple[pd.DataFrame, dict]:
    """Candles from the validated store, with indicators added. Returns
    (df, data_info) where data_info reports source/quality/interpolation so
    results always carry their data provenance."""
    start_ms = int(pd.to_datetime(start_date, utc=True).timestamp() * 1000) if start_date else None
    end_ms = int(pd.to_datetime(end_date, utc=True).timestamp() * 1000) if end_date else None

    rows = load_candles(symbol, timeframe, start_ms=start_ms, end_ms=end_ms, db=db)
    if len(rows) >= 100:
        df = pd.DataFrame(rows)
        quality = df["quality_score"].dropna().iloc[-1] if df["quality_score"].notna().any() else None
        info = {
            "source": "market_candles",
            "bars": len(df),
            "interpolated_bars": int(df["interpolated"].sum()),
            "quality_score": float(quality) if quality is not None else None,
            "quality_ok": quality is None or float(quality) >= MIN_USABLE_QUALITY,
        }
        if not info["quality_ok"]:
            raise DataNotAvailableError(
                f"Stored {symbol} {timeframe} dataset quality {quality} is below the usable "
                f"threshold {MIN_USABLE_QUALITY} - re-download before backtesting"
            )
        return add_indicators(df[["time", "open", "high", "low", "close", "volume"]]), info

    csv_file = history_path(symbol, timeframe)
    if csv_file.exists():
        df = pd.read_csv(csv_file)
        if start_ms or end_ms:
            ts = df["time"].astype("int64")
            if start_ms:
                df = df[ts >= start_ms]
            if end_ms:
                df = df[df["time"].astype("int64") <= end_ms]
            df = df.reset_index(drop=True)
        info = {"source": "csv_history", "bars": len(df), "interpolated_bars": 0, "quality_score": None, "quality_ok": True}
        return add_indicators(df), info

    raise DataNotAvailableError(
        f"No stored candles for {symbol} {timeframe} - download them first via POST /api/data/download"
    )


def _classify_regimes(df: pd.DataFrame) -> pd.Series:
    """Lightweight per-bar regime tag for regime-performance attribution
    (uses the indicator columns add_indicators already computed)."""
    ema20, ema50, ema200 = df["ema20"], df["ema50"], df["ema200"]
    bb_width = df["bb_width"]
    high_vol = bb_width > bb_width.rolling(100, min_periods=20).mean() * 1.5

    regime = pd.Series("RANGING", index=df.index)
    regime[(ema20 > ema50) & (ema50 > ema200)] = "TRENDING_UP"
    regime[(ema20 < ema50) & (ema50 < ema200)] = "TRENDING_DOWN"
    regime[high_vol.fillna(False)] = "HIGH_VOLATILITY"
    return regime


# ---------------------------------------------------------------- simulator

def simulate_advanced(
    df: pd.DataFrame,
    signals: list[tuple[str, float]],
    cost: CostConfig,
    risk: AdvancedRiskConfig,
    starting_balance: float = 10000.0,
) -> tuple[list[dict], list[dict]]:
    """execution_sim.simulate_trades plus trailing stops, max holding time,
    daily loss limit and a drawdown circuit breaker. Returns
    (trades, risk_events)."""
    has_time = "time" in df.columns
    times = df["time"].astype(float).tolist() if has_time else []
    bar_duration_ms = execution_sim._bar_duration_ms(times)

    regimes = _classify_regimes(df) if "ema200" in df.columns else pd.Series("UNKNOWN", index=df.index)

    def _time_at(i: int):
        return pd.to_datetime(df.iloc[i]["time"], unit="ms", utc=True) if has_time else None

    trades: list[dict] = []
    risk_events: list[dict] = []
    position: dict | None = None
    bars_held = 0

    balance = starting_balance
    peak = starting_balance
    day_pnl = 0.0
    current_day = None
    day_blocked = False
    breaker_tripped = False
    n = len(df)

    for i in range(n):
        row = df.iloc[i]
        direction, confidence = signals[i]
        if confidence < risk.entry_confidence_threshold:
            direction = "NO_TRADE"

        close = float(row["close"])
        high = float(row.get("high", close))
        low = float(row.get("low", close))
        atr = float(row.get("atr") or 0.0) or close * 0.01
        next_open = float(df.iloc[i + 1]["open"]) if i + 1 < n and "open" in df.columns else None
        fill_price = execution_sim._fill_price(close, next_open, cost.latency_ms, bar_duration_ms)

        bar_time = _time_at(i)
        bar_day = bar_time.date() if bar_time is not None else None
        if bar_day != current_day:
            current_day = bar_day
            day_pnl = 0.0
            day_blocked = False

        if position is not None:
            bars_held += 1
            side = position["side"]
            exit_price, exit_reason = None, None

            if side == "LONG":
                if low <= position["sl"]:
                    exit_price, exit_reason = position["sl"], "stop_loss"
                elif high >= position["tp"]:
                    exit_price, exit_reason = position["tp"], "take_profit"
            else:
                if high >= position["sl"]:
                    exit_price, exit_reason = position["sl"], "stop_loss"
                elif low <= position["tp"]:
                    exit_price, exit_reason = position["tp"], "take_profit"

            if exit_price is None and risk.max_holding_bars and bars_held >= risk.max_holding_bars:
                exit_price, exit_reason = fill_price, "max_holding_time"

            if exit_price is None and direction != side and direction != "NO_TRADE":
                exit_price, exit_reason = fill_price, "signal_flip"

            if exit_price is not None:
                trade = execution_sim._close_position(position, exit_price, bar_time, exit_reason, cost)
                trade["regime"] = position.get("regime", "UNKNOWN")
                trades.append(trade)
                balance += trade["pnl"]
                day_pnl += trade["pnl"]
                peak = max(peak, balance)
                position = None
                bars_held = 0
            elif risk.trailing_stop_atr_mult > 0:
                trail = atr * risk.trailing_stop_atr_mult
                if side == "LONG":
                    position["sl"] = max(position["sl"], high - trail)
                else:
                    position["sl"] = min(position["sl"], low + trail)

        # ---- circuit breakers gate NEW entries only; open positions keep
        # managing themselves toward their stops/targets ----
        if risk.daily_loss_limit_pct > 0 and not day_blocked:
            if day_pnl <= -starting_balance * risk.daily_loss_limit_pct / 100:
                day_blocked = True
                risk_events.append(
                    {"time": bar_time.isoformat() if bar_time else i, "type": "daily_loss_limit",
                     "detail": f"Daily loss {day_pnl:.2f} hit {risk.daily_loss_limit_pct}% limit - entries blocked for the day"}
                )
        if risk.max_drawdown_stop_pct > 0 and not breaker_tripped and peak > 0:
            dd = (peak - balance) / peak * 100
            if dd >= risk.max_drawdown_stop_pct:
                breaker_tripped = True
                risk_events.append(
                    {"time": bar_time.isoformat() if bar_time else i, "type": "drawdown_circuit_breaker",
                     "detail": f"Drawdown {dd:.2f}% hit {risk.max_drawdown_stop_pct}% breaker - no further entries"}
                )

        can_enter = not day_blocked and not breaker_tripped
        if position is None and can_enter and direction in ("LONG", "SHORT"):
            position = execution_sim._open_position(direction, fill_price, bar_time, atr, confidence, cost, risk)
            position["regime"] = regimes.iloc[i] if i < len(regimes) else "UNKNOWN"
            bars_held = 0

    if position is not None:
        trade = execution_sim._close_position(position, float(df.iloc[-1]["close"]), _time_at(n - 1), "end_of_data", cost)
        trade["regime"] = position.get("regime", "UNKNOWN")
        trades.append(trade)

    return trades, risk_events


# ---------------------------------------------------------------- metrics

def extended_metrics(trades: list[dict], starting_balance: float = 10000.0) -> dict:
    metrics = compute_metrics(trades, starting_balance=starting_balance)

    max_consecutive = streak = 0
    for t in sorted(trades, key=lambda t: t.get("exit_time") or t.get("entry_time") or 0):
        if float(t.get("pnl") or 0.0) < 0:
            streak += 1
            max_consecutive = max(max_consecutive, streak)
        else:
            streak = 0
    metrics["max_consecutive_losses"] = max_consecutive

    for side_name, key in (("LONG", "long_accuracy_pct"), ("SHORT", "short_accuracy_pct")):
        side_trades = [t for t in trades if t.get("side") == side_name]
        wins = sum(1 for t in side_trades if float(t.get("pnl") or 0.0) >= 0)
        metrics[key] = round(wins / len(side_trades) * 100, 2) if side_trades else None
        metrics[f"{side_name.lower()}_trades"] = len(side_trades)

    by_regime: dict[str, list[dict]] = {}
    for t in trades:
        by_regime.setdefault(t.get("regime") or "UNKNOWN", []).append(t)
    metrics["regime_performance"] = {
        regime: {
            "trades": len(ts),
            "win_rate": round(sum(1 for t in ts if float(t.get("pnl") or 0) >= 0) / len(ts) * 100, 2),
            "total_pnl": round(sum(float(t.get("pnl") or 0) for t in ts), 2),
        }
        for regime, ts in by_regime.items()
    }
    return metrics


def _serialize_trades(trades: list[dict]) -> list[dict]:
    out = []
    for t in trades:
        row = dict(t)
        for key in ("entry_time", "exit_time"):
            if row.get(key) is not None and not isinstance(row[key], str):
                row[key] = row[key].isoformat()
        out.append(row)
    return out


# ---------------------------------------------------------------- runner

def _apply_preset(req: dict) -> dict:
    preset_name = (req.get("preset") or "").lower().replace(" ", "_") or None
    if not preset_name:
        return req
    if preset_name not in PRESETS:
        raise ValueError(f"Unknown preset '{preset_name}'. Valid: {list(PRESETS)}")
    merged = dict(PRESETS[preset_name])
    # explicit request fields always win over the preset's defaults
    merged.update({k: v for k, v in req.items() if v is not None})
    merged["preset"] = preset_name
    return merged


def _cost_from(req: dict) -> CostConfig:
    fields = {k: req[k] for k in CostConfig.__dataclass_fields__ if req.get(k) is not None}
    return CostConfig(**fields)


def _risk_from(req: dict) -> AdvancedRiskConfig:
    fields = {k: req[k] for k in AdvancedRiskConfig.__dataclass_fields__ if req.get(k) is not None}
    return AdvancedRiskConfig(**fields)


def run_advanced(req: dict, db=None) -> dict:
    """One advanced run over symbols × timeframes. req keys (all optional
    except strategy): strategy, symbols, timeframes, preset, start_date,
    end_date, starting_balance, train_split, monte_carlo_sims, plus any
    CostConfig / AdvancedRiskConfig field."""
    req = _apply_preset(req)

    strategy = req.get("strategy") or "ensemble"
    if strategy not in lab_strategies.STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy}', expected one of {lab_strategies.STRATEGIES}")

    symbols = [s.upper() for s in (req.get("symbols") or ["BTCUSDT"])]
    from app.timeframes.canonical import storage_interval
    timeframes = [storage_interval(tf) for tf in (req.get("timeframes") or ["5m"])]
    for tf in timeframes:
        if tf not in TIMEFRAMES_MS:
            raise ValueError(f"Unsupported timeframe '{tf}'")

    starting_balance = float(req.get("starting_balance") or 10000.0)
    train_split = min(0.95, max(0.05, float(req.get("train_split") or 0.7)))
    mc_sims = int(req.get("monte_carlo_sims") or 0)

    cost = _cost_from(req)
    risk = _risk_from(req)

    owns = db is None
    db = db or SessionLocal()
    combos = []
    try:
        for symbol in symbols:
            for timeframe in timeframes:
                combo = {"symbol": symbol, "timeframe": timeframe}
                try:
                    df, data_info = load_history_db(symbol, timeframe, req.get("start_date"), req.get("end_date"), db=db)
                    signals = lab_strategies.generate_signals(strategy, df, db=db)
                    trades, risk_events = simulate_advanced(df, signals, cost, risk, starting_balance)
                    metrics = extended_metrics(trades, starting_balance)

                    split_at = int(len(df) * train_split)
                    in_sample_trades, _ = simulate_advanced(
                        df.iloc[:split_at].reset_index(drop=True), signals[:split_at], cost, risk, starting_balance
                    )
                    oos_df = df.iloc[split_at:].reset_index(drop=True)
                    oos_trades, _ = simulate_advanced(oos_df, signals[split_at:], cost, risk, starting_balance)

                    mc = None
                    if mc_sims >= 100 and len(trades) >= 5:
                        mc = monte_carlo.run(trades, starting_balance=starting_balance, simulations=mc_sims)

                    contribution = None
                    if strategy == "ensemble":
                        contribution = {}
                        for sub in CONTRIBUTION_STRATEGIES:
                            try:
                                sub_signals = lab_strategies.generate_signals(sub, df, db=db)
                                sub_trades, _ = simulate_advanced(df, sub_signals, cost, risk, starting_balance)
                                sub_metrics = compute_metrics(sub_trades, starting_balance=starting_balance)
                                contribution[sub] = {
                                    k: sub_metrics.get(k)
                                    for k in ("total_trades", "win_rate", "total_return_pct", "profit_factor", "sharpe_ratio")
                                }
                            except Exception as exc:  # noqa: BLE001
                                contribution[sub] = {"error": str(exc)}

                    combo.update(
                        {
                            "ok": True,
                            "data": data_info,
                            "metrics": metrics,
                            "in_sample_metrics": compute_metrics(in_sample_trades, starting_balance=starting_balance),
                            "out_of_sample_metrics": compute_metrics(oos_trades, starting_balance=starting_balance),
                            "train_split": train_split,
                            "monte_carlo": mc,
                            "strategy_contribution": contribution,
                            "risk_events": risk_events,
                            "equity_curve": metrics["equity_curve"],
                            "drawdown_curve": drawdown_curve(metrics["equity_curve"]),
                            "trades": _serialize_trades(trades),
                        }
                    )
                except (DataNotAvailableError, lab_strategies.ModelNotAvailableError, ValueError) as exc:
                    combo.update({"ok": False, "error": str(exc)})
                combos.append(combo)

        ok_metrics = [c["metrics"] for c in combos if c.get("ok")]
        timeframe_performance = {}
        for tf in timeframes:
            tf_metrics = [c["metrics"] for c in combos if c.get("ok") and c["timeframe"] == tf]
            if tf_metrics:
                timeframe_performance[tf] = average_metrics(tf_metrics)

        result = {
            "run_id": uuid.uuid4().hex[:12],
            "strategy": strategy,
            "preset": req.get("preset"),
            "symbols": symbols,
            "timeframes": timeframes,
            "starting_balance": starting_balance,
            "cost": asdict(cost),
            "risk": asdict(risk),
            "combinations": combos,
            "summary": {
                "combinations_run": len(combos),
                "combinations_ok": sum(1 for c in combos if c.get("ok")),
                "average_metrics": average_metrics(ok_metrics) if ok_metrics else None,
                "timeframe_performance": timeframe_performance,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        db.add(
            AdvancedBacktestRun(
                run_id=result["run_id"],
                strategy=strategy,
                symbols=symbols,
                timeframes=timeframes,
                preset=req.get("preset"),
                status="succeeded" if result["summary"]["combinations_ok"] else "failed",
                request={k: v for k, v in req.items() if k != "trades"},
                results=result,
            )
        )
        db.commit()
        return result
    finally:
        if owns:
            db.close()


def get_run(run_id: str, db=None) -> dict | None:
    owns = db is None
    db = db or SessionLocal()
    try:
        row = db.query(AdvancedBacktestRun).filter(AdvancedBacktestRun.run_id == run_id).first()
        return row.results if row else None
    finally:
        if owns:
            db.close()


def list_runs(limit: int = 30, db=None) -> list[dict]:
    owns = db is None
    db = db or SessionLocal()
    try:
        rows = db.query(AdvancedBacktestRun).order_by(AdvancedBacktestRun.created_at.desc()).limit(limit).all()
        out = []
        for r in rows:
            summary = (r.results or {}).get("summary") or {}
            out.append(
                {
                    "run_id": r.run_id,
                    "strategy": r.strategy,
                    "symbols": r.symbols,
                    "timeframes": r.timeframes,
                    "preset": r.preset,
                    "status": r.status,
                    "combinations_ok": summary.get("combinations_ok"),
                    "average_metrics": summary.get("average_metrics"),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
            )
        return out
    finally:
        if owns:
            db.close()


def run_walkforward_for(run_id: str, train_bars: int = 500, validate_bars: int = 150, db=None) -> dict:
    """Rolling walk-forward over the stored run's configuration, one window
    set per successful (symbol, timeframe) combination."""
    owns = db is None
    db = db or SessionLocal()
    try:
        row = db.query(AdvancedBacktestRun).filter(AdvancedBacktestRun.run_id == run_id).first()
        if row is None:
            raise ValueError(f"Run '{run_id}' not found")
        req = row.request or {}
        cost = _cost_from(req)
        risk = _risk_from(req)
        starting_balance = float(req.get("starting_balance") or 10000.0)

        results = []
        for combo in (row.results or {}).get("combinations", []):
            if not combo.get("ok"):
                continue
            symbol, timeframe = combo["symbol"], combo["timeframe"]
            df, _ = load_history_db(symbol, timeframe, req.get("start_date"), req.get("end_date"), db=db)

            windows = []
            start = 0
            step = validate_bars
            while start + train_bars + validate_bars <= len(df):
                validate_slice = df.iloc[start + train_bars : start + train_bars + validate_bars].reset_index(drop=True)
                signals = lab_strategies.generate_signals(row.strategy, validate_slice, db=db)
                trades, _events = simulate_advanced(validate_slice, signals, cost, risk, starting_balance)
                windows.append(
                    {
                        "window": len(windows) + 1,
                        "validate_range": [int(start + train_bars), int(start + train_bars + validate_bars - 1)],
                        "metrics": compute_metrics(trades, starting_balance=starting_balance),
                    }
                )
                start += step

            results.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "windows_run": len(windows),
                    "train_bars": train_bars,
                    "validate_bars": validate_bars,
                    "windows": windows,
                    "average_metrics": average_metrics([w["metrics"] for w in windows]) if windows else None,
                }
            )

        if not results:
            raise ValueError("Run has no successful combinations to walk forward")
        return {"run_id": run_id, "strategy": row.strategy, "results": results}
    finally:
        if owns:
            db.close()


def run_montecarlo_for(run_id: str, simulations: int = 1000, db=None) -> dict:
    owns = db is None
    db = db or SessionLocal()
    try:
        row = db.query(AdvancedBacktestRun).filter(AdvancedBacktestRun.run_id == run_id).first()
        if row is None:
            raise ValueError(f"Run '{run_id}' not found")

        results = []
        starting_balance = float((row.request or {}).get("starting_balance") or 10000.0)
        for combo in (row.results or {}).get("combinations", []):
            if not combo.get("ok"):
                continue
            trades = [
                {
                    **t,
                    "entry_time": datetime.fromisoformat(t["entry_time"]) if t.get("entry_time") else None,
                    "exit_time": datetime.fromisoformat(t["exit_time"]) if t.get("exit_time") else None,
                }
                for t in combo.get("trades", [])
            ]
            if len(trades) < 5:
                results.append({"symbol": combo["symbol"], "timeframe": combo["timeframe"],
                                "ok": False, "error": f"Only {len(trades)} trades - need 5+"})
                continue
            mc = monte_carlo.run(trades, starting_balance=starting_balance, simulations=simulations)
            results.append({"symbol": combo["symbol"], "timeframe": combo["timeframe"], "ok": True, **mc})

        if not results:
            raise ValueError("Run has no successful combinations to simulate")
        return {"run_id": run_id, "results": results}
    finally:
        if owns:
            db.close()


def compare_strategies(
    symbol: str,
    timeframe: str,
    start_date: str | None = None,
    end_date: str | None = None,
    starting_balance: float = 10000.0,
    db=None,
) -> dict:
    """Every lab strategy over the same validated DB dataset with default
    advanced costs - the DB-backed successor of /api/research/lab/compare."""
    owns = db is None
    db = db or SessionLocal()
    try:
        df, data_info = load_history_db(symbol, timeframe, start_date, end_date, db=db)
        cost, risk = CostConfig(), AdvancedRiskConfig()
        results = []
        for strategy in lab_strategies.STRATEGIES:
            try:
                signals = lab_strategies.generate_signals(strategy, df, db=db)
                trades, _events = simulate_advanced(df, signals, cost, risk, starting_balance)
                results.append({"strategy": strategy, "ok": True, "metrics": extended_metrics(trades, starting_balance)})
            except lab_strategies.ModelNotAvailableError as exc:
                results.append({"strategy": strategy, "ok": False, "error": str(exc)})
        return {"ok": True, "symbol": symbol.upper(), "timeframe": timeframe, "data": data_info, "results": results}
    finally:
        if owns:
            db.close()


def latest_quality(symbol: str, timeframe: str, db=None) -> dict | None:
    owns = db is None
    db = db or SessionLocal()
    try:
        row = (
            db.query(DataQualityReport)
            .filter(DataQualityReport.symbol == symbol.upper(), DataQualityReport.timeframe == timeframe)
            .order_by(DataQualityReport.created_at.desc())
            .first()
        )
        if row is None:
            return None
        return {"quality_score": row.quality_score, "checked_at": row.created_at.isoformat() if row.created_at else None}
    finally:
        if owns:
            db.close()
