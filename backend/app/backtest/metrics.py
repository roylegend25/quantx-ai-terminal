def calculate_metrics(trades, initial_balance=10000):
    if not trades:
        return {
            "initial_balance": initial_balance,
            "final_balance": initial_balance,
            "total_return_pct": 0,
            "total_trades": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "max_drawdown_pct": 0,
        }

    balance = initial_balance
    equity_curve = [balance]
    wins = []
    losses = []

    for t in trades:
        pnl = t["pnl"]
        balance += pnl
        equity_curve.append(balance)

        if pnl >= 0:
            wins.append(pnl)
        else:
            losses.append(abs(pnl))

    peak = equity_curve[0]
    max_dd = 0

    for equity in equity_curve:
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd = max(max_dd, dd)

    gross_profit = sum(wins)
    gross_loss = sum(losses)

    return {
        "initial_balance": round(initial_balance, 2),
        "final_balance": round(balance, 2),
        "total_return_pct": round((balance - initial_balance) / initial_balance * 100, 2),
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / max(1, len(trades)) * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        "max_drawdown_pct": round(max_dd, 2),
        "equity_curve": equity_curve,
    }
