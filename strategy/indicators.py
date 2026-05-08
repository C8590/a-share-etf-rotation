from __future__ import annotations

import pandas as pd


def calculate_momentum(close: pd.DataFrame, period: int) -> pd.DataFrame:
    """Past-period return using only historical closes up to each row."""
    return close / close.shift(period) - 1.0


def calculate_moving_average(close: pd.DataFrame, period: int) -> pd.DataFrame:
    return close.rolling(window=period, min_periods=period).mean()
