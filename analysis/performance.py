from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def drawdown(equity: pd.Series) -> pd.Series:
    equity = equity.astype(float)
    return equity / equity.cummax() - 1.0


def annualized_return(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0)


def sharpe_ratio(equity: pd.Series) -> float:
    returns = equity.pct_change().dropna()
    if returns.empty or returns.std(ddof=0) <= 0:
        return 0.0
    return float((returns.mean() / returns.std(ddof=0)) * np.sqrt(252))


def summarize_equity(equity: pd.Series) -> dict[str, float]:
    equity = equity.dropna().astype(float)
    if equity.empty:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "calmar_ratio": 0.0,
        }
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    ann_return = annualized_return(equity)
    max_dd = float(drawdown(equity).min())
    sharpe = sharpe_ratio(equity)
    calmar = float(ann_return / abs(max_dd)) if max_dd < 0 else 0.0
    return {
        "total_return": total_return,
        "annual_return": ann_return,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
        "calmar_ratio": calmar,
    }


def yearly_stats(equity: pd.Series) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    for year, series in equity.groupby(equity.index.year):
        series = series.dropna()
        if series.empty:
            continue
        rows[str(year)] = {
            "return": float(series.iloc[-1] / series.iloc[0] - 1.0),
            "max_drawdown": float(drawdown(series).min()),
        }
    return rows


def compact_yearly_field(values: dict[str, dict[str, float]], key: str) -> str:
    return ";".join(f"{year}:{items[key]:.4f}" for year, items in values.items())


def write_json(data: Any, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
