from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from strategy.base import BaseStrategy
from strategy.indicators import calculate_momentum, calculate_moving_average


@dataclass(frozen=True)
class StrategyConfig:
    strategy_type: str = "daily_confirm_momentum_rotation"
    momentum_period: int = 20
    ma_period: int = 60
    max_positions: int = 2
    sell_rank_threshold: int = 4
    rebalance_frequency: str = "daily"
    rebalance_timing: str = "month_end"
    rebalance_day: int | None = None
    rebalance_day_of_month: int | None = None
    rebalance_roll: str = "next"
    enable_market_filter: bool = False
    market_filter_symbol: str = "510300"
    market_filter_ma_window: int = 200
    enable_cash_etf_fallback: bool = False
    cash_etf_symbol: str = "511880"
    enable_trend_filter: bool = True
    enable_min_momentum_filter: bool = False
    min_momentum_threshold: float | None = None
    max_industry_etf_weight: float | None = None
    selected_symbols: tuple[str, ...] = ()
    enable_universe_filter: bool = True
    min_trading_days: int = 120
    avg_amount_window: int = 20
    min_avg_amount: float = 20_000_000.0
    min_data_completeness: float = 0.95
    max_stale_days: int = 7
    max_zero_amount_days: int = 0


class DailyMomentumRotationStrategy(BaseStrategy):
    """Daily right-side confirmation ETF momentum rotation strategy."""

    strategy_name = "日频右侧确认型 ETF 动量轮动策略"

    def __init__(
        self,
        close: pd.DataFrame,
        etf_info: dict[str, dict[str, str]],
        config: StrategyConfig,
        amount: pd.DataFrame | None = None,
    ):
        if close.empty:
            raise ValueError("日频动量轮动策略需要可用的日线收盘价数据")
        self.close = close.sort_index()
        self.etf_info = etf_info
        self.config = config
        self.amount = amount.sort_index() if amount is not None else None
        self.momentum = calculate_momentum(self.close, config.momentum_period)
        self.ma = calculate_moving_average(self.close, config.ma_period)

    def _candidate_columns(self) -> list[str]:
        columns = list(self.close.columns)
        if self.config.selected_symbols:
            selected = set(self.config.selected_symbols)
            columns = [symbol for symbol in columns if symbol in selected]
        return columns

    @staticmethod
    def _indicator_cache_path() -> Path:
        path = Path("data") / "cache" / "indicator_cache.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _load_cached_metrics(self, signal_date: pd.Timestamp, columns: list[str]) -> pd.DataFrame:
        if self.amount is None:
            return pd.DataFrame()
        path = self._indicator_cache_path()
        if not path.exists():
            return pd.DataFrame()
        try:
            cached = pd.read_csv(path, dtype={"symbol": str}, low_memory=False).fillna("")
        except Exception:
            return pd.DataFrame()
        if cached.empty or "symbol" not in cached.columns:
            return pd.DataFrame()
        cached["symbol"] = cached["symbol"].astype(str).str.zfill(6)
        cached = cached[cached["symbol"].isin(columns)].copy()
        if "signal_date" in cached.columns:
            cached = cached[cached["signal_date"].astype(str).eq(str(signal_date.date()))]
        if "ma_period" in cached.columns:
            cached = cached[pd.to_numeric(cached["ma_period"], errors="coerce").eq(self.config.ma_period)]
        if "momentum_period" in cached.columns:
            cached = cached[pd.to_numeric(cached["momentum_period"], errors="coerce").eq(self.config.momentum_period)]
        return cached

    def _save_cached_metrics(self, metrics: pd.DataFrame, signal_date: pd.Timestamp) -> None:
        if metrics.empty or self.amount is None:
            return
        out = metrics.copy()
        out["signal_date"] = str(signal_date.date())
        out["ma_period"] = self.config.ma_period
        out["momentum_period"] = self.config.momentum_period
        path = self._indicator_cache_path()
        existing = pd.DataFrame()
        if path.exists():
            try:
                existing = pd.read_csv(path, dtype={"symbol": str}, low_memory=False).fillna("")
            except Exception:
                existing = pd.DataFrame()
        if not existing.empty:
            existing["symbol"] = existing["symbol"].astype(str).str.zfill(6)
            keep = ~(
                existing["symbol"].isin(out["symbol"])
                & existing.get("signal_date", "").astype(str).eq(str(signal_date.date()))
                & pd.to_numeric(existing.get("ma_period", -1), errors="coerce").eq(self.config.ma_period)
                & pd.to_numeric(existing.get("momentum_period", -1), errors="coerce").eq(self.config.momentum_period)
            )
            out = pd.concat([existing[keep], out], ignore_index=True)
        out.to_csv(path, index=False, encoding="utf-8-sig")

    def _metric_snapshot(self, signal_date: pd.Timestamp, columns: list[str]) -> pd.DataFrame:
        signal_date = pd.Timestamp(signal_date)
        close = self.close.loc[:signal_date, columns]
        amount = self.amount.loc[:signal_date, columns] if self.amount is not None else pd.DataFrame(index=close.index, columns=columns)
        lookback = max(self.config.min_trading_days, self.config.avg_amount_window)

        latest_dates = close.apply(lambda series: "" if series.dropna().empty else str(series.dropna().index[-1].date()))
        trading_days = close.count()
        if self.amount is None:
            avg_amount = pd.Series(self.config.min_avg_amount, index=columns, dtype=float)
            zero_amount_days = pd.Series(0, index=columns, dtype=int)
            amount_completeness = pd.Series(1.0, index=columns, dtype=float)
        else:
            avg_amount = amount.tail(self.config.avg_amount_window).mean()
            zero_amount_days = (amount.tail(self.config.avg_amount_window) <= 0).sum()
            amount_completeness = amount.tail(lookback).notna().mean().fillna(0.0)
        close_completeness = close.tail(lookback).notna().mean()
        data_completeness = pd.concat([close_completeness, amount_completeness], axis=1).min(axis=1)
        latest_ts = pd.to_datetime(latest_dates.replace("", pd.NaT))
        stale_days = (signal_date.normalize() - latest_ts).dt.days.fillna(999999).astype(int)
        volatility_20 = close.pct_change().rolling(20, min_periods=10).std().loc[signal_date]
        recent_60 = close.tail(60)
        max_drawdown_60 = (recent_60 / recent_60.cummax() - 1.0).min()

        metrics = pd.DataFrame(
            {
                "symbol": columns,
                "latest_date": latest_dates.reindex(columns).values,
                "listed_days": trading_days.reindex(columns).astype(int).values,
                "avg_amount_20": avg_amount.reindex(columns).values,
                "data_completeness": data_completeness.reindex(columns).values,
                "stale_days": stale_days.reindex(columns).values,
                "zero_amount_days_20": zero_amount_days.reindex(columns).astype(int).values,
                "momentum_20": (close / close.shift(20) - 1.0).loc[signal_date].reindex(columns).values,
                "momentum_60": (close / close.shift(60) - 1.0).loc[signal_date].reindex(columns).values,
                "momentum_120": (close / close.shift(120) - 1.0).loc[signal_date].reindex(columns).values,
                "volatility_20": volatility_20.reindex(columns).values,
                "max_drawdown_60": max_drawdown_60.reindex(columns).values,
            }
        )
        reasons: list[str] = []
        for _, row in metrics.iterrows():
            row_reasons: list[str] = []
            if int(row["listed_days"]) < self.config.min_trading_days:
                row_reasons.append(f"listed_days<{self.config.min_trading_days}")
            if int(row["stale_days"]) > self.config.max_stale_days:
                row_reasons.append(f"stale>{self.config.max_stale_days}d")
            if pd.isna(row["avg_amount_20"]) or float(row["avg_amount_20"]) < self.config.min_avg_amount:
                row_reasons.append(f"avg_amount<{self.config.min_avg_amount:.0f}")
            if float(row["data_completeness"]) < self.config.min_data_completeness:
                row_reasons.append(f"completeness<{self.config.min_data_completeness:.0%}")
            if int(row["zero_amount_days_20"]) > self.config.max_zero_amount_days:
                row_reasons.append("zero_amount")
            close_value = self.close.loc[signal_date, row["symbol"]]
            if pd.isna(close_value) or float(close_value) <= 0:
                row_reasons.append("invalid_close")
            reasons.append(";".join(row_reasons))
        metrics["filter_reason"] = reasons
        metrics["filter_passed"] = metrics["filter_reason"].eq("")
        return metrics

    def _momentum_at(self, symbol: str, signal_date: pd.Timestamp, period: int) -> float:
        series = self.close[symbol].loc[:signal_date].dropna()
        if len(series) <= period:
            return float("nan")
        return float(series.iloc[-1] / series.iloc[-period - 1] - 1.0)

    def _volatility_at(self, symbol: str, signal_date: pd.Timestamp, period: int = 20) -> float:
        series = self.close[symbol].loc[:signal_date].dropna().pct_change().dropna().tail(period)
        if len(series) < max(5, period // 2):
            return float("nan")
        return float(series.std())

    def _max_drawdown_at(self, symbol: str, signal_date: pd.Timestamp, period: int = 60) -> float:
        series = self.close[symbol].loc[:signal_date].dropna().tail(period)
        if len(series) < max(5, period // 2):
            return float("nan")
        running_max = series.cummax()
        drawdown = series / running_max - 1.0
        return float(drawdown.min())

    def _filter_snapshot(self, symbol: str, signal_date: pd.Timestamp) -> dict[str, object]:
        close_raw = self.close[symbol].loc[:signal_date]
        close_valid = close_raw.dropna()
        latest_date = pd.Timestamp(close_valid.index[-1]) if not close_valid.empty else pd.NaT
        trading_days = int(len(close_valid))
        stale_days = 999999 if pd.isna(latest_date) else int((signal_date.normalize() - latest_date.normalize()).days)

        amount_valid = pd.Series(dtype=float)
        if self.amount is not None and symbol in self.amount.columns:
            amount_valid = pd.to_numeric(self.amount[symbol].loc[:signal_date], errors="coerce").dropna()
        if self.amount is None:
            avg_amount = self.config.min_avg_amount
            zero_amount_days = 0
        else:
            avg_amount = float(amount_valid.tail(self.config.avg_amount_window).mean()) if not amount_valid.empty else float("nan")
            zero_amount_days = int((amount_valid.tail(self.config.avg_amount_window) <= 0).sum()) if not amount_valid.empty else self.config.avg_amount_window

        lookback = max(self.config.min_trading_days, self.config.avg_amount_window)
        close_recent = close_raw.tail(lookback)
        close_completeness = float(close_recent.notna().mean()) if len(close_recent) else 0.0
        amount_completeness = 1.0
        if self.amount is not None and symbol in self.amount.columns:
            amount_recent = self.amount[symbol].loc[:signal_date].tail(lookback)
            amount_completeness = float(amount_recent.notna().mean()) if len(amount_recent) else 0.0
        data_completeness = min(close_completeness, amount_completeness)

        reasons: list[str] = []
        if trading_days < self.config.min_trading_days:
            reasons.append(f"listed_days<{self.config.min_trading_days}")
        if stale_days > self.config.max_stale_days:
            reasons.append(f"stale>{self.config.max_stale_days}d")
        if pd.isna(avg_amount) or avg_amount < self.config.min_avg_amount:
            reasons.append(f"avg_amount<{self.config.min_avg_amount:.0f}")
        if data_completeness < self.config.min_data_completeness:
            reasons.append(f"completeness<{self.config.min_data_completeness:.0%}")
        if zero_amount_days > self.config.max_zero_amount_days:
            reasons.append("zero_amount")
        if close_valid.empty or float(close_valid.iloc[-1]) <= 0:
            reasons.append("invalid_close")

        return {
            "latest_date": "" if pd.isna(latest_date) else str(latest_date.date()),
            "listed_days": trading_days,
            "avg_amount_20": avg_amount,
            "data_completeness": data_completeness,
            "stale_days": stale_days,
            "zero_amount_days_20": zero_amount_days,
            "filter_passed": not reasons,
            "filter_reason": ";".join(reasons),
        }

    def get_rank_table(self, signal_date: pd.Timestamp) -> pd.DataFrame:
        signal_date = pd.Timestamp(signal_date)
        if signal_date not in self.close.index:
            raise ValueError(f"signal date is not in close data: {signal_date.date()}")

        columns = self._candidate_columns()
        cached = self._load_cached_metrics(signal_date, columns)
        if cached.empty or set(cached["symbol"]) != set(columns):
            meta = self._metric_snapshot(signal_date, columns)
            self._save_cached_metrics(meta, signal_date)
        else:
            meta = cached.copy()
        for col in ["momentum_20", "momentum_60", "momentum_120", "volatility_20", "max_drawdown_60", "avg_amount_20", "data_completeness"]:
            if col in meta.columns:
                meta[col] = pd.to_numeric(meta[col], errors="coerce")
        if "filter_passed" in meta.columns:
            meta["filter_passed"] = meta["filter_passed"].astype(str).str.lower().isin(["true", "1", "yes"])
        vol_penalty = meta["volatility_20"].fillna(0.0)
        dd_penalty = meta["max_drawdown_60"].abs().fillna(0.0)
        meta["score"] = (
            0.45 * meta["momentum_60"].fillna(0.0)
            + 0.30 * meta["momentum_20"].fillna(0.0)
            + 0.25 * meta["momentum_120"].fillna(0.0)
            - 0.50 * vol_penalty
            - 0.20 * dd_penalty
        )
        meta["name"] = meta["symbol"].map(lambda symbol: self.etf_info.get(symbol, {}).get("name", symbol))
        meta["exchange"] = meta["symbol"].map(lambda symbol: self.etf_info.get(symbol, {}).get("exchange", ""))
        meta["asset_class"] = meta["symbol"].map(lambda symbol: self.etf_info.get(symbol, {}).get("asset_class", ""))
        meta["category"] = meta["symbol"].map(lambda symbol: self.etf_info.get(symbol, {}).get("category", ""))
        meta["tracking_index"] = meta["symbol"].map(lambda symbol: self.etf_info.get(symbol, {}).get("tracking_index", self.etf_info.get(symbol, {}).get("sector", "")))
        meta["theme"] = meta["symbol"].map(lambda symbol: self.etf_info.get(symbol, {}).get("theme", ""))
        meta["sector"] = meta["symbol"].map(lambda symbol: self.etf_info.get(symbol, {}).get("sector", self.etf_info.get(symbol, {}).get("category", "")))

        snapshot = pd.DataFrame(
            {
                "symbol": columns,
                "close": self.close.loc[signal_date, columns],
                "momentum": self.momentum.loc[signal_date, columns],
                "ma": self.ma.loc[signal_date, columns],
            }
        )
        snapshot = snapshot.merge(meta, on="symbol", how="left")
        snapshot["above_ma"] = snapshot["close"] > snapshot["ma"]
        threshold = self.config.min_momentum_threshold
        if threshold is None and self.config.enable_min_momentum_filter:
            threshold = 0.0
        snapshot["momentum_passed"] = True if threshold is None else snapshot["momentum"] > threshold
        snapshot["data_quality_passed"] = snapshot[["close", "momentum", "ma"]].notna().all(axis=1)
        if self.config.enable_universe_filter and self.amount is not None:
            snapshot["data_quality_passed"] = snapshot["data_quality_passed"] & snapshot["filter_passed"].fillna(False)
        trend_passed = snapshot["above_ma"] if self.config.enable_trend_filter else True
        snapshot["trend_passed"] = trend_passed
        snapshot["eligible"] = snapshot["data_quality_passed"] & snapshot["trend_passed"] & snapshot["momentum_passed"]

        ranked = snapshot[snapshot["data_quality_passed"]].sort_values("score", ascending=False).reset_index(drop=True)
        ranked["rank"] = range(1, len(ranked) + 1)
        ranked["selected"] = False
        ranked["final_signal"] = "watch"
        ranked["selection_reason"] = ""
        return ranked

    def generate_target(
        self,
        signal_date: pd.Timestamp,
        current_holdings: list[str],
    ) -> dict[str, object]:
        signal_date = pd.Timestamp(signal_date)
        ranks = self.get_rank_table(signal_date)
        eligible = ranks[ranks["eligible"]].copy()
        target = eligible.head(self.config.max_positions)["symbol"].tolist()
        fallback_reason = ""
        if not target and self.config.enable_cash_etf_fallback:
            cash_symbol = self.config.cash_etf_symbol
            if cash_symbol in self.close.columns and pd.notna(self.close.loc[signal_date, cash_symbol]):
                target = [cash_symbol]
                fallback_reason = "没有 ETF 同时通过动量和趋势条件，策略启用现金类 ETF 备用规则。"

        if not ranks.empty:
            ranks.loc[ranks["symbol"].isin(target), "selected"] = True
            ranks.loc[ranks["symbol"].isin(target), "final_signal"] = "selected"
            ranks.loc[~ranks["symbol"].isin(target), "final_signal"] = np.where(
                ranks.loc[~ranks["symbol"].isin(target), "eligible"],
                "eligible_not_selected",
                "filtered_out",
            )

        rank_by_symbol = ranks.set_index("symbol").to_dict("index") if not ranks.empty else {}
        buy_reasons: dict[str, str] = {}
        sell_reasons: dict[str, str] = {}
        keep_reasons: dict[str, str] = {}

        selection_reasons: dict[str, str] = {}
        for _, row in ranks.iterrows():
            symbol = str(row["symbol"])
            name = self.etf_info.get(symbol, {}).get("name", symbol)
            if symbol in target:
                selection_reasons[symbol] = (
                    f"入选：{name} 按 {self.config.momentum_period} 日收盘动量排名第 {int(row['rank'])}，"
                    f"满足趋势和动量过滤。"
                )
            elif not bool(row.get("data_quality_passed", False)):
                selection_reasons[symbol] = "未入选：信号日收盘价、动量或均线数据不足。"
            elif self.config.enable_trend_filter and not bool(row["above_ma"]):
                selection_reasons[symbol] = f"未入选：收盘价跌破 {self.config.ma_period} 日均线。"
            elif not bool(row["momentum_passed"]):
                selection_reasons[symbol] = "未入选：动量低于配置阈值。"
            else:
                selection_reasons[symbol] = f"未入选：动量排名未进入前 {self.config.max_positions}。"
        if selection_reasons:
            ranks["selection_reason"] = ranks["symbol"].map(selection_reasons).fillna("")

        for symbol in target:
            row = rank_by_symbol.get(symbol)
            name = self.etf_info.get(symbol, {}).get("name", symbol)
            if fallback_reason and symbol == self.config.cash_etf_symbol:
                reason = fallback_reason
            elif row is not None:
                reason = (
                    f"{name}近 {self.config.momentum_period} 个交易日动量排名第 {int(row['rank'])}，"
                    f"收盘价站上 {self.config.ma_period} 日均线，且动量为正。"
                )
            else:
                reason = f"{name}由策略备用规则选中。"
            if symbol not in current_holdings:
                buy_reasons[symbol] = reason
            else:
                keep_reasons[symbol] = reason

        for symbol in current_holdings:
            if symbol in target:
                continue
            row = rank_by_symbol.get(symbol)
            name = self.etf_info.get(symbol, {}).get("name", symbol)
            if row is None:
                sell_reasons[symbol] = f"{name}在信号日缺少收盘价、动量或均线数据。"
            elif self.config.enable_trend_filter and not bool(row["above_ma"]):
                sell_reasons[symbol] = f"{name}收盘价跌破 {self.config.ma_period} 日均线。"
            elif not bool(row["momentum_passed"]):
                sell_reasons[symbol] = f"{name}动量未通过策略阈值。"
            elif int(row["rank"]) > self.config.sell_rank_threshold:
                sell_reasons[symbol] = f"{name} 动量排名跌出卖出阈值 {self.config.sell_rank_threshold}。"
            else:
                sell_reasons[symbol] = f"{name} 不在本次目标组合中。"

        return {
            "signal_date": signal_date,
            "target": target,
            "ranks": ranks,
            "eligible": eligible,
            "buy_reasons": buy_reasons,
            "sell_reasons": sell_reasons,
            "keep_reasons": keep_reasons,
            "market_filter_passed": True,
            "fallback_reason": fallback_reason,
        }


def get_signal_dates(dates: pd.DatetimeIndex, signal_weekday: int = 4) -> list[pd.Timestamp]:
    if dates.empty:
        return []
    unique_dates = pd.DatetimeIndex(sorted(pd.to_datetime(dates).unique()))
    return [pd.Timestamp(d).normalize() for d in unique_dates.tolist()]


def get_rebalance_dates(
    dates: pd.DatetimeIndex,
    frequency: str = "daily",
    signal_weekday: int = 4,
    rebalance_timing: str = "",
    rebalance_day: int | None = None,
    rebalance_day_of_month: int | None = None,
    rebalance_roll: str = "next",
) -> list[pd.Timestamp]:
    if dates.empty:
        return []
    if str(frequency or "daily") != "daily":
        raise ValueError(f"当前项目只支持日频信号，不支持调仓频率: {frequency}")
    return get_signal_dates(dates, signal_weekday)
