from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def calculate_drawdown(equity: pd.Series) -> pd.Series:
    running_max = equity.cummax()
    return equity / running_max - 1.0


def calculate_performance(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    current_holdings: list[str],
    initial_cash: float,
) -> dict[str, object]:
    if equity_curve.empty:
        raise ValueError("权益曲线为空，无法计算绩效")

    equity = equity_curve["equity"].astype(float)
    returns = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    total_return = equity.iloc[-1] / initial_cash - 1.0
    annual_return = (equity.iloc[-1] / initial_cash) ** (1 / years) - 1.0
    max_drawdown = calculate_drawdown(equity).min()
    sharpe = 0.0
    if returns.std(ddof=0) > 0:
        sharpe = float((returns.mean() / returns.std(ddof=0)) * np.sqrt(252))

    real_trades = trades[trades["action"].isin(["BUY", "SELL"])] if not trades.empty else trades
    sell_trades = trades[trades["action"] == "SELL"] if not trades.empty else trades
    profitable_sells = sell_trades[sell_trades.get("realized_pnl", pd.Series(dtype=float)) > 0]
    win_rate = float(len(profitable_sells) / len(sell_trades)) if len(sell_trades) else 0.0

    avg_equity = equity.mean()
    turnover = 0.0
    if avg_equity > 0 and not real_trades.empty:
        turnover = float(real_trades["notional"].sum() / avg_equity / years)

    last_rebalance_date = None
    if not real_trades.empty:
        last_rebalance_date = str(pd.to_datetime(real_trades["date"]).max().date())

    start_date = str(equity.index[0].date())
    end_date = str(equity.index[-1].date())
    calmar = float(annual_return / abs(max_drawdown)) if max_drawdown < 0 else 0.0
    result = {
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "max_drawdown": float(max_drawdown),
        "sharpe_ratio": float(sharpe),
        "sharpe": float(sharpe),
        "calmar_ratio": calmar,
        "calmar": calmar,
        "trade_count": int(len(real_trades)),
        "win_rate": float(win_rate),
        "annual_turnover": float(turnover),
        "yearly_turnover": float(turnover),
        "start_date": start_date,
        "end_date": end_date,
        "is_complete_backtest": True,
        "warning": "",
        "current_holdings": current_holdings,
        "last_rebalance_date": last_rebalance_date,
        "final_equity": float(equity.iloc[-1]),
    }
    return result


def save_performance(performance: dict[str, object], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(performance, f, ensure_ascii=False, indent=2)
