from __future__ import annotations

from pathlib import Path

import pandas as pd

from analysis.performance import compact_yearly_field, summarize_equity, write_json, yearly_stats


def buy_and_hold_equity(close: pd.DataFrame, symbol: str, initial_cash: float) -> pd.Series:
    price = close[symbol].dropna()
    if price.empty:
        return pd.Series(dtype=float)
    return (initial_cash / price.iloc[0]) * price


def equal_weight_monthly_equity(close: pd.DataFrame, initial_cash: float) -> pd.Series:
    prices = close.ffill().dropna(how="all")
    if prices.empty:
        return pd.Series(dtype=float)

    cash = 0.0
    shares = pd.Series(0.0, index=prices.columns)
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    rebalance_dates = set(pd.Series(prices.index, index=prices.index).resample("ME").last().dropna())

    first_prices = prices.iloc[0].dropna()
    weight_cash = initial_cash / len(first_prices)
    shares.loc[first_prices.index] = weight_cash / first_prices

    for date, row in prices.iterrows():
        valid = row.dropna()
        equity = cash + float((shares.loc[valid.index] * valid).sum())
        equity_rows.append((date, equity))
        if date in rebalance_dates and len(valid) > 0:
            target_value = equity / len(valid)
            shares.loc[:] = 0.0
            shares.loc[valid.index] = target_value / valid
            cash = 0.0

    return pd.Series(dict(equity_rows)).sort_index()


def build_benchmark_report(
    close: pd.DataFrame,
    strategy_equity: pd.Series,
    initial_cash: float,
    output_dir: str | Path = "output",
    extra_benchmarks: dict[str, pd.Series] | None = None,
) -> pd.DataFrame:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    benchmarks: dict[str, pd.Series] = {
        "buy_hold_510300": buy_and_hold_equity(close, "510300", initial_cash),
        "cash_etf_511880": buy_and_hold_equity(close, "511880", initial_cash),
        "all_etf_equal_weight_monthly": equal_weight_monthly_equity(close, initial_cash),
        "current_strategy": strategy_equity.dropna(),
    }
    if extra_benchmarks:
        benchmarks.update({name: equity.dropna() for name, equity in extra_benchmarks.items()})

    rows = []
    details = {}
    for name, equity in benchmarks.items():
        stats = summarize_equity(equity)
        years = yearly_stats(equity)
        row = {
            "benchmark": name,
            "total_return": stats["total_return"],
            "annual_return": stats["annual_return"],
            "max_drawdown": stats["max_drawdown"],
            "sharpe_ratio": stats["sharpe_ratio"],
            "sharpe": stats["sharpe_ratio"],
            "calmar_ratio": stats["calmar_ratio"],
            "calmar": stats["calmar_ratio"],
            "annual_returns": compact_yearly_field(years, "return"),
            "annual_max_drawdowns": compact_yearly_field(years, "max_drawdown"),
        }
        rows.append(row)
        details[name] = {"summary": stats, "yearly": years}

    report = pd.DataFrame(rows)
    report.to_csv(output_path / "benchmark_report.csv", index=False, encoding="utf-8-sig")
    write_json(details, output_path / "benchmark_report.json")
    return report
