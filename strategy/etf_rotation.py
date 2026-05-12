from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategy.base import BaseStrategy
from strategy.indicators import calculate_momentum, calculate_moving_average


@dataclass(frozen=True)
class StrategyConfig:
    strategy_type: str = "rotation"
    momentum_period: int = 20
    ma_period: int = 60
    max_positions: int = 2
    sell_rank_threshold: int = 4
    rebalance_frequency: str = "weekly"
    rebalance_timing: str = "month_end"
    rebalance_day: int | None = None
    rebalance_day_of_month: int | None = None
    rebalance_roll: str = "next"
    enable_market_filter: bool = False
    market_filter_symbol: str = "510300"
    market_filter_ma_window: int = 200
    enable_cash_etf_fallback: bool = False
    cash_etf_symbol: str = "511880"
    min_momentum_threshold: float | None = None
    max_industry_etf_weight: float | None = None
    selected_symbols: tuple[str, ...] = ()


class ETFRotationStrategy(BaseStrategy):
    """20-day momentum plus 60-day trend filter with a rank-buffer sell rule."""

    strategy_name = "rotation"

    def __init__(self, close: pd.DataFrame, etf_info: dict[str, dict[str, str]], config: StrategyConfig):
        self.close = close.sort_index()
        self.etf_info = etf_info
        self.config = config
        self.momentum = calculate_momentum(self.close, config.momentum_period)
        self.ma = calculate_moving_average(self.close, config.ma_period)
        self.market_ma = calculate_moving_average(self.close, config.market_filter_ma_window)

    def get_rank_table(self, signal_date: pd.Timestamp) -> pd.DataFrame:
        if signal_date not in self.close.index:
            raise ValueError(f"信号日期不在行情数据中: {signal_date.date()}")

        columns = list(self.close.columns)
        if self.config.selected_symbols:
            selected = set(self.config.selected_symbols)
            columns = [symbol for symbol in columns if symbol in selected]

        snapshot = pd.DataFrame(
            {
                "symbol": columns,
                "name": [self.etf_info.get(s, {}).get("name", s) for s in columns],
                "close": self.close.loc[signal_date, columns],
                "momentum": self.momentum.loc[signal_date, columns],
                "ma": self.ma.loc[signal_date, columns],
            }
        )
        snapshot["above_ma"] = snapshot["close"] > snapshot["ma"]
        snapshot = snapshot.dropna(subset=["close", "momentum", "ma"])
        snapshot = snapshot.sort_values("momentum", ascending=False).reset_index(drop=True)
        snapshot["rank"] = range(1, len(snapshot) + 1)
        return snapshot

    def generate_target(
        self,
        signal_date: pd.Timestamp,
        current_holdings: list[str],
    ) -> dict[str, object]:
        ranks = self.get_rank_table(signal_date)
        eligible = ranks[ranks["above_ma"]].copy()
        if self.config.min_momentum_threshold is not None:
            eligible = eligible[eligible["momentum"] > self.config.min_momentum_threshold]
        market_filter_passed = self.is_market_filter_passed(signal_date)
        if not market_filter_passed:
            eligible = eligible.iloc[0:0]
        top_candidates = eligible.head(self.config.max_positions)["symbol"].tolist()
        top_candidates = self._apply_industry_limit(top_candidates)

        rank_by_symbol = ranks.set_index("symbol").to_dict("index") if not ranks.empty else {}
        survivors: list[str] = []
        sell_reasons: dict[str, str] = {}
        keep_reasons: dict[str, str] = {}

        for symbol in current_holdings:
            row = rank_by_symbol.get(symbol)
            name = self.etf_info.get(symbol, {}).get("name", symbol)
            if row is None:
                sell_reasons[symbol] = f"{name} 缺少足够指标或当日行情，卖出规避数据风险"
                continue
            if not bool(row["above_ma"]):
                sell_reasons[symbol] = f"{name} 收盘价跌破 {self.config.ma_period} 日均线"
                continue
            if self.config.min_momentum_threshold is not None and float(row["momentum"]) <= self.config.min_momentum_threshold:
                sell_reasons[symbol] = f"{name} momentum is not above configured threshold"
                continue
            if int(row["rank"]) > self.config.sell_rank_threshold:
                sell_reasons[symbol] = f"{name} 动量排名跌出前 {self.config.sell_rank_threshold}"
                continue
            survivors.append(symbol)
            keep_reasons[symbol] = (
                f"{name} 高于 {self.config.ma_period} 日均线且排名未跌出前 "
                f"{self.config.sell_rank_threshold}，按缓冲规则继续持有"
            )

        target = survivors[: self.config.max_positions]
        for symbol in top_candidates:
            if len(target) >= self.config.max_positions:
                break
            if symbol not in target:
                target.append(symbol)

        if not target and self.config.enable_cash_etf_fallback:
            cash_symbol = self.config.cash_etf_symbol
            if cash_symbol in self.close.columns and cash_symbol not in target:
                target = [cash_symbol]

        buy_reasons = {
            symbol: (
                f"{self.etf_info.get(symbol, {}).get('name', symbol)} 高于 {self.config.ma_period} 日均线，"
                f"{self.config.momentum_period} 日动量位于前 {self.config.max_positions}"
            )
            for symbol in target
            if symbol not in current_holdings
        }

        return {
            "signal_date": signal_date,
            "target": target,
            "ranks": ranks,
            "eligible": eligible,
            "buy_reasons": buy_reasons,
            "sell_reasons": sell_reasons,
            "keep_reasons": keep_reasons,
            "market_filter_passed": market_filter_passed,
        }

    def is_market_filter_passed(self, signal_date: pd.Timestamp) -> bool:
        if not self.config.enable_market_filter:
            return True
        symbol = self.config.market_filter_symbol
        if symbol not in self.close.columns or symbol not in self.market_ma.columns:
            return False
        price = self.close.loc[signal_date, symbol]
        ma_value = self.market_ma.loc[signal_date, symbol]
        if pd.isna(price) or pd.isna(ma_value):
            return False
        return bool(price >= ma_value)

    def _apply_industry_limit(self, candidates: list[str]) -> list[str]:
        limit = self.config.max_industry_etf_weight
        if limit is None or limit >= 1 or not candidates:
            return candidates
        max_industry_count = int(limit * self.config.max_positions)
        selected: list[str] = []
        industry_count = 0
        for symbol in candidates:
            if self._is_industry_etf(symbol):
                if industry_count >= max_industry_count:
                    continue
                industry_count += 1
            selected.append(symbol)
            if len(selected) >= self.config.max_positions:
                break
        return selected

    def _is_industry_etf(self, symbol: str) -> bool:
        category = self.etf_info.get(symbol, {}).get("category", "")
        return any(keyword in category for keyword in ["证券", "半导体", "消费"])


def get_weekly_signal_dates(dates: pd.DatetimeIndex, signal_weekday: int = 4) -> list[pd.Timestamp]:
    if dates.empty:
        return []

    unique_dates = pd.DatetimeIndex(sorted(pd.to_datetime(dates).unique()))
    weekday_alias = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][signal_weekday]
    by_week = pd.Series(unique_dates, index=unique_dates).resample(f"W-{weekday_alias}").last().dropna()
    by_week = by_week[by_week.index <= unique_dates[-1]]
    # If the configured weekday is closed, resample().last() picks the last trading
    # day in that weekly bucket, which still avoids using future data.
    return [pd.Timestamp(d) for d in by_week.tolist()]


def get_rebalance_dates(
    dates: pd.DatetimeIndex,
    frequency: str = "weekly",
    signal_weekday: int = 4,
    rebalance_timing: str = "month_end",
    rebalance_day: int | None = None,
    rebalance_day_of_month: int | None = None,
    rebalance_roll: str = "next",
) -> list[pd.Timestamp]:
    weekly_dates = get_weekly_signal_dates(dates, signal_weekday)
    if frequency == "weekly":
        return weekly_dates
    if frequency == "biweekly":
        return weekly_dates[::2]
    if frequency == "monthly":
        timing = str(rebalance_timing or "month_end")
        if timing == "month_end":
            if not weekly_dates:
                return []
            series = pd.Series(weekly_dates, index=pd.DatetimeIndex(weekly_dates))
            return [pd.Timestamp(d) for d in series.resample("ME").last().dropna().tolist()]

        if pd.DatetimeIndex(dates).empty:
            return []
        trading_dates = pd.DatetimeIndex(sorted(pd.to_datetime(dates).unique()))
        by_month = pd.Series(trading_dates, index=trading_dates).groupby(trading_dates.to_period("M"))
        if timing == "month_start":
            return [pd.Timestamp(month_dates.iloc[0]) for _, month_dates in by_month]
        if timing == "nth_trading_day":
            if rebalance_day is None:
                raise ValueError("rebalance_day is required when rebalance_timing is nth_trading_day")
            day = int(rebalance_day)
            if day < 1:
                raise ValueError("rebalance_day must be >= 1")
            return [pd.Timestamp(month_dates.iloc[min(day - 1, len(month_dates) - 1)]) for _, month_dates in by_month]
        if timing == "day_of_month":
            if rebalance_day_of_month is None:
                raise ValueError("rebalance_day_of_month is required when rebalance_timing is day_of_month")
            day_of_month = int(rebalance_day_of_month)
            if day_of_month < 1 or day_of_month > 31:
                raise ValueError("rebalance_day_of_month must be between 1 and 31")
            roll = str(rebalance_roll or "next")
            if roll not in {"next", "previous", "nearest"}:
                raise ValueError(f"Unsupported rebalance roll: {rebalance_roll}")
            return _get_day_of_month_rebalance_dates(trading_dates, day_of_month, roll)
        raise ValueError(f"Unsupported rebalance timing: {rebalance_timing}")
    raise ValueError(f"Unsupported rebalance frequency: {frequency}")


def _get_day_of_month_rebalance_dates(
    trading_dates: pd.DatetimeIndex,
    day_of_month: int,
    roll: str,
) -> list[pd.Timestamp]:
    result: list[pd.Timestamp] = []
    months = pd.PeriodIndex(trading_dates.to_period("M")).unique()
    for month in months:
        month_end = month.to_timestamp(how="end").normalize()
        target_day = min(day_of_month, int(month_end.day))
        target = pd.Timestamp(year=month.year, month=month.month, day=target_day)
        previous_dates = trading_dates[trading_dates <= target]
        next_dates = trading_dates[trading_dates >= target]
        previous_date = pd.Timestamp(previous_dates[-1]) if len(previous_dates) else None
        next_date = pd.Timestamp(next_dates[0]) if len(next_dates) else None

        if roll == "next":
            chosen = next_date
        elif roll == "previous":
            chosen = previous_date
        else:
            if previous_date is None:
                chosen = next_date
            elif next_date is None:
                chosen = previous_date
            else:
                previous_distance = target - previous_date
                next_distance = next_date - target
                chosen = previous_date if previous_distance <= next_distance else next_date

        if chosen is not None and (not result or result[-1] != chosen):
            result.append(chosen)
    return result
