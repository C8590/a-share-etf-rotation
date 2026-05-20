from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .config import HistoricalMLConfig


def _safe_zscore(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    std = s.std(skipna=True, ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean(skipna=True)) / std


def _max_drawdown(values: pd.Series) -> float:
    v = pd.to_numeric(values, errors="coerce").dropna()
    if len(v) < 2:
        return np.nan
    running_max = v.cummax()
    dd = v / running_max - 1.0
    return float(dd.min())


def _last_or_nan(values: pd.Series, idx_from_end: int) -> float:
    if len(values) <= idx_from_end:
        return np.nan
    return float(values.iloc[-1] / values.iloc[-1 - idx_from_end] - 1.0)


def _basic_features_for_date(price_df: pd.DataFrame, trade_date, config: HistoricalMLConfig) -> pd.DataFrame:
    """Compute cross-sectional features for one date using rows <= trade_date only."""

    trade_date = pd.Timestamp(trade_date).normalize()
    history = price_df.loc[price_df["date"] <= trade_date].copy()
    today = history.loc[history["date"] == trade_date].copy()
    if today.empty:
        return pd.DataFrame()

    records = []
    w20, w60, w120 = config.momentum_windows
    for code, g in history.groupby("code", sort=False):
        g = g.sort_values("date")
        if g.empty or g["date"].iloc[-1] != trade_date:
            continue
        latest = g.iloc[-1]
        close = pd.to_numeric(g["close"], errors="coerce")
        high = pd.to_numeric(g.get("high", close), errors="coerce")
        low = pd.to_numeric(g.get("low", close), errors="coerce")
        amount = pd.to_numeric(g.get("amount", pd.Series(0, index=g.index)), errors="coerce")

        r20 = _last_or_nan(close, w20)
        r60 = _last_or_nan(close, w60)
        r120 = _last_or_nan(close, w120)
        ma20 = float(close.tail(w20).mean()) if len(close.dropna()) >= max(2, min(w20, len(close))) else np.nan
        ma60 = float(close.tail(w60).mean()) if len(close.dropna()) >= max(2, min(w60, len(close))) else np.nan
        ma120 = float(close.tail(w120).mean()) if len(close.dropna()) >= max(2, min(w120, len(close))) else np.nan
        avg_amount_20d = float(amount.tail(20).mean()) if len(amount.dropna()) else 0.0
        vol20 = float(close.pct_change().tail(20).std(ddof=0)) if len(close) >= 5 else np.nan
        maxdd60 = _max_drawdown(close.tail(60))
        missing_ratio_60d = float(close.tail(60).isna().mean()) if len(close) else 1.0
        data_quality_flag = "ok"
        if len(g) < config.min_history_days:
            data_quality_flag = "insufficient_history"
        elif missing_ratio_60d > config.max_missing_ratio_60d:
            data_quality_flag = "missing_data"
        elif avg_amount_20d < config.min_avg_amount_20d:
            data_quality_flag = "low_liquidity"
        elif pd.isna(latest["close"]) or latest["close"] <= 0:
            data_quality_flag = "bad_price"

        close_today = float(latest["close"]) if pd.notna(latest["close"]) else np.nan
        abs_trend_score = 0.0
        if pd.notna(close_today):
            abs_trend_score += 0.35 if pd.notna(r60) and r60 > 0 else 0.0
            abs_trend_score += 0.35 if pd.notna(r120) and r120 > 0 else 0.0
            abs_trend_score += 0.30 if pd.notna(ma60) and close_today > ma60 else 0.0

        # Higher trend_maturity means more mature / more likely chase-high risk.
        runup_20 = close_today / close.tail(20).min() - 1.0 if len(close.dropna()) >= 5 and close.tail(20).min() > 0 else 0.0
        ma60_gap = close_today / ma60 - 1.0 if pd.notna(ma60) and ma60 > 0 else 0.0
        trend_maturity = float(np.clip(0.5 * (runup_20 / 0.25) + 0.5 * (max(ma60_gap, 0) / 0.18), 0, 1))
        overheat_score = float(np.clip(0.6 * max(runup_20, 0) / 0.35 + 0.4 * max(ma60_gap, 0) / 0.25, 0, 1))

        records.append(
            {
                "trade_date": trade_date,
                "code": str(code),
                "name": str(latest.get("name", code)),
                "sector": str(latest.get("sector", "UNKNOWN")),
                "sector_l1": str(latest.get("sector_l1", latest.get("sector", "UNKNOWN"))),
                "close": close_today,
                "r20": r20,
                "r60": r60,
                "r120": r120,
                "ma20": ma20,
                "ma60": ma60,
                "ma120": ma120,
                "avg_amount_20d": avg_amount_20d,
                "vol20": vol20,
                "max_drawdown_60d": maxdd60,
                "missing_ratio_60d": missing_ratio_60d,
                "abs_trend_score": abs_trend_score,
                "trend_maturity": trend_maturity,
                "overheat_score": overheat_score,
                "data_quality_flag": data_quality_flag,
            }
        )

    out = pd.DataFrame(records)
    if out.empty:
        return out

    z20 = _safe_zscore(out["r20"])
    z60 = _safe_zscore(out["r60"])
    z120 = _safe_zscore(out["r120"])
    mw20, mw60, mw120 = config.momentum_weights
    out["momentum_score"] = mw20 * z20 + mw60 * z60 + mw120 * z120
    out["liquidity_score"] = _safe_zscore(np.log1p(out["avg_amount_20d"].fillna(0)))
    out["risk_score"] = _safe_zscore(out["vol20"].fillna(out["vol20"].median())) + _safe_zscore((-out["max_drawdown_60d"]).fillna(0))
    return out


def compute_market_state(price_df: pd.DataFrame, trade_date, config: HistoricalMLConfig) -> str:
    """Classify market state from data available by trade_date."""

    trade_date = pd.Timestamp(trade_date).normalize()
    history = price_df.loc[price_df["date"] <= trade_date].copy()
    today_features = _basic_features_for_date(price_df, trade_date, config)
    if today_features.empty:
        return "unknown"

    if config.market_index_code and config.market_index_code in set(history["code"].astype(str)):
        g = history.loc[history["code"].astype(str) == config.market_index_code].sort_values("date")
        close = g["close"]
        r60 = _last_or_nan(close, 60)
        r120 = _last_or_nan(close, 120)
        breadth = float((today_features["close"] > today_features["ma60"]).mean())
    else:
        r60 = float(today_features["r60"].mean(skipna=True))
        r120 = float(today_features["r120"].mean(skipna=True))
        breadth = float((today_features["close"] > today_features["ma60"]).mean())

    if pd.isna(r60):
        r60 = 0.0
    if pd.isna(r120):
        r120 = 0.0

    if r60 > 0 and r120 > 0 and breadth >= 0.55:
        return "offense"
    if r60 < 0 and r120 < 0 and breadth <= 0.45:
        return "defense"
    return "neutral"


def build_sector_features_for_day(etf_features: pd.DataFrame, config: HistoricalMLConfig) -> pd.DataFrame:
    if etf_features.empty:
        return pd.DataFrame()

    records = []
    for sector, g in etf_features.groupby("sector", sort=False):
        g = g.copy()
        n_top = max(1, int(np.ceil(len(g) * 0.30)))
        top = g.nlargest(n_top, "momentum_score")
        sector_momentum = float(top["momentum_score"].mean(skipna=True))
        sector_acceleration = float(g.loc[g["acceleration_score"] > 0, "acceleration_score"].mean(skipna=True))
        if pd.isna(sector_acceleration):
            sector_acceleration = 0.0
        breadth = (
            0.40 * float((g["r60"] > 0).mean())
            + 0.40 * float((g["close"] > g["ma60"]).mean())
            + 0.20 * float((g["acceleration_score"] > 0).mean())
        )
        sector_risk = float((g["risk_score"].fillna(0)).mean())
        proxy = sector_momentum + 0.3 * sector_acceleration + 0.2 * breadth - 0.1 * sector_risk
        records.append(
            {
                "trade_date": g["trade_date"].iloc[0],
                "sector": sector,
                "sector_l1": g["sector_l1"].iloc[0],
                "market_state": g["market_state"].iloc[0],
                "sector_momentum_score": sector_momentum,
                "sector_acceleration_score": sector_acceleration,
                "sector_breadth_score": breadth,
                "sector_risk_score": sector_risk,
                "sector_entry_success_proxy": proxy,
                "candidate_count": int(len(g)),
            }
        )

    sectors = pd.DataFrame(records)
    sectors["sector_score"] = (
        0.50 * _safe_zscore(sectors["sector_momentum_score"])
        + 0.20 * _safe_zscore(sectors["sector_acceleration_score"])
        + 0.20 * _safe_zscore(sectors["sector_breadth_score"])
        - 0.10 * _safe_zscore(sectors["sector_risk_score"])
    )
    sectors["sector_rank"] = sectors["sector_score"].rank(ascending=False, method="first").astype(int)
    sectors["sector_state"] = np.select(
        [sectors["sector_score"] >= 0.5, sectors["sector_score"] <= -0.5],
        ["strong", "weak"],
        default="neutral",
    )
    sectors["source"] = config.source
    return sectors.sort_values("sector_rank").reset_index(drop=True)


def build_etf_features_for_day(price_df: pd.DataFrame, trade_date, config: HistoricalMLConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build ETF and sector feature samples for trade_date with no future data."""

    trade_date = pd.Timestamp(trade_date).normalize()
    today = _basic_features_for_date(price_df, trade_date, config)
    if today.empty:
        return today, pd.DataFrame()

    all_dates = sorted(pd.to_datetime(price_df.loc[price_df["date"] <= trade_date, "date"].unique()))
    lag_idx = max(0, len(all_dates) - 1 - config.acceleration_lag)
    lag_date = all_dates[lag_idx]
    lag = _basic_features_for_date(price_df, lag_date, config)[["code", "momentum_score"]].rename(
        columns={"momentum_score": "momentum_score_lag"}
    )
    today = today.merge(lag, on="code", how="left")
    today["acceleration_score"] = today["momentum_score"] - today["momentum_score_lag"].fillna(0.0)

    market_state = compute_market_state(price_df, trade_date, config)
    today["market_state"] = market_state
    today["entry_score"] = (
        0.55 * today["momentum_score"].fillna(0)
        + 0.20 * today["acceleration_score"].fillna(0)
        + 0.10 * today["abs_trend_score"].fillna(0)
        + 0.05 * today["liquidity_score"].fillna(0)
        - 0.10 * today["risk_score"].fillna(0)
        - 0.05 * today["overheat_score"].fillna(0)
    )

    sectors = build_sector_features_for_day(today, config)
    if not sectors.empty:
        today = today.merge(
            sectors[["sector", "sector_rank", "sector_state", "sector_score"]],
            on="sector",
            how="left",
        )
    else:
        today["sector_rank"] = np.nan
        today["sector_state"] = "unknown"
        today["sector_score"] = np.nan

    today["etf_rank"] = today.groupby("sector")["entry_score"].rank(ascending=False, method="first").astype(int)
    today["global_rank"] = today["entry_score"].rank(ascending=False, method="first").astype(int)
    today["source"] = config.source
    return today.reset_index(drop=True), sectors
