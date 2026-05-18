"""ETF pre-selection engine.

This module owns candidate discovery only. It ranks ETFs with a two-layer
right-side momentum model and writes pre_selection_result.csv for downstream
modules; it never emits buy or sell actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from contracts.signal_schema import MarketState, PRE_SELECTION_RESULT_FIELDS
from data.storage import load_etf_data, normalize_symbol

OUTPUT_FILE = "pre_selection_result.csv"
REQUIRED_OUTPUT_FIELDS = PRE_SELECTION_RESULT_FIELDS


@dataclass(frozen=True)
class PreSelectionConfig:
    """Tunable parameters for the right-side two-layer momentum model."""

    short_momentum_window: int = 20
    medium_momentum_window: int = 60
    long_momentum_window: int = 120
    trend_window: int = 60
    volatility_window: int = 20
    drawdown_window: int = 60
    liquidity_window: int = 20
    min_trading_days: int = 120
    min_avg_amount: float = 20_000_000.0
    min_data_completeness: float = 0.95
    max_stale_days: int = 7
    max_zero_amount_days: int = 0
    max_abs_daily_return: float = 0.12
    max_candidates: int = 5
    balanced_candidates: int = 3
    defense_candidates: int = 0
    min_candidate_score: float = 0.0
    min_sector_breadth: float = 0.25


@dataclass(frozen=True)
class _EtfMetrics:
    symbol: str
    name: str
    sector: str
    filter_passed: bool
    filter_reason: str
    close: float
    avg_amount: float
    momentum_short: float
    momentum_medium: float
    momentum_long: float
    acceleration: float
    volatility: float
    max_drawdown: float
    above_trend: bool


class PreSelectionEngine:
    """Produce ETF pre-selection rows that match REQUIRED_OUTPUT_FIELDS."""

    def __init__(
        self,
        etf_pool: Sequence[Mapping[str, Any]] | None = None,
        market_data: Mapping[str, pd.DataFrame] | None = None,
        config: PreSelectionConfig | None = None,
        signal_date: str | pd.Timestamp | None = None,
    ) -> None:
        self.etf_pool = [dict(item) for item in etf_pool] if etf_pool is not None else None
        self.market_data = {normalize_symbol(key): value.copy() for key, value in (market_data or {}).items()}
        self.config = config or PreSelectionConfig()
        self.signal_date = pd.Timestamp(signal_date).normalize() if signal_date is not None else None

    def run(
        self,
        input_data: Sequence[Mapping[str, Any]] | None = None,
        output_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Rank ETFs, write output/pre_selection_result.csv, and return rows."""

        etf_pool, market_data = self._resolve_inputs(input_data)
        signal_date = self._resolve_signal_date(market_data)
        generated_at = datetime.now().isoformat(timespec="seconds")

        metrics = [self._build_metrics(item, market_data, signal_date) for item in etf_pool]
        market_state = self.determine_market_state(metrics)
        sector_scores = self.rank_sectors(metrics)
        rows = self._build_output_rows(metrics, sector_scores, market_state, signal_date, generated_at)

        output_path = Path(output_dir or "output")
        output_path.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=REQUIRED_OUTPUT_FIELDS).to_csv(
            output_path / OUTPUT_FILE,
            index=False,
            encoding="utf-8-sig",
        )
        return rows

    def determine_market_state(self, metrics: Sequence[_EtfMetrics]) -> str:
        """Classify the market as 进攻、均衡 or 防守 from broad ETF breadth."""

        usable = [item for item in metrics if item.filter_passed]
        if not usable:
            return MarketState.DEFENSE.value

        broad = [
            item
            for item in usable
            if item.sector in {"沪深300", "中证500", "中证1000", "创业板", "科创50", "上证50"}
            or "宽基" in item.sector
        ]
        sample = broad or usable
        avg_short = float(np.nanmean([item.momentum_short for item in sample]))
        avg_medium = float(np.nanmean([item.momentum_medium for item in sample]))
        positive_breadth = float(np.mean([item.momentum_short > 0 for item in sample]))
        trend_breadth = float(np.mean([item.above_trend for item in sample]))

        if avg_short > 0.02 and avg_medium > 0 and positive_breadth >= 0.55 and trend_breadth >= 0.60:
            return MarketState.ATTACK.value
        if avg_short < -0.02 or avg_medium < -0.03 or positive_breadth < 0.35 or trend_breadth < 0.35:
            return MarketState.DEFENSE.value
        return MarketState.BALANCED.value

    def rank_sectors(self, metrics: Sequence[_EtfMetrics]) -> pd.DataFrame:
        """Return sector-level momentum, acceleration, breadth and risk scores."""

        rows: list[dict[str, float | str]] = []
        for sector in sorted({item.sector for item in metrics if item.filter_passed}):
            members = [item for item in metrics if item.filter_passed and item.sector == sector]
            if not members:
                continue
            sector_momentum = float(
                np.nanmean([0.60 * item.momentum_medium + 0.40 * item.momentum_short for item in members])
            )
            sector_acceleration = float(np.nanmean([item.acceleration for item in members]))
            sector_breadth = float(np.mean([item.momentum_short > 0 and item.above_trend for item in members]))
            sector_risk = float(
                np.nanmean([max(item.volatility, 0.0) + abs(min(item.max_drawdown, 0.0)) * 0.5 for item in members])
            )
            score = 0.50 * sector_momentum + 0.25 * sector_acceleration + 0.20 * sector_breadth - 0.35 * sector_risk
            rows.append(
                {
                    "sector": sector,
                    "sector_momentum": sector_momentum,
                    "sector_acceleration": sector_acceleration,
                    "sector_breadth": sector_breadth,
                    "sector_risk": sector_risk,
                    "sector_score": score,
                }
            )
        if not rows:
            return pd.DataFrame(
                columns=[
                    "sector",
                    "sector_momentum",
                    "sector_acceleration",
                    "sector_breadth",
                    "sector_risk",
                    "sector_score",
                    "sector_rank",
                ]
            )
        ranked = pd.DataFrame(rows).sort_values("sector_score", ascending=False).reset_index(drop=True)
        ranked["sector_rank"] = range(1, len(ranked) + 1)
        return ranked

    def _resolve_inputs(
        self,
        input_data: Sequence[Mapping[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame]]:
        if input_data is not None:
            etf_pool, market_data = self._coerce_input_data(input_data)
        else:
            etf_pool, market_data = [], {}

        etf_pool = etf_pool or [dict(item) for item in (self.etf_pool or [])]
        market_data = {**self.market_data, **market_data}

        if not etf_pool:
            from data.downloader import load_etf_pool

            etf_pool = [dict(item) for item in load_etf_pool()]

        if not market_data:
            for item in etf_pool:
                symbol = normalize_symbol(item.get("symbol", ""))
                if not symbol:
                    continue
                try:
                    market_data[symbol] = load_etf_data(symbol, name=str(item.get("name", ""))).reset_index()
                except Exception:
                    market_data[symbol] = pd.DataFrame()

        return etf_pool, market_data

    def _coerce_input_data(
        self,
        input_data: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame]]:
        etf_pool: list[dict[str, Any]] = []
        market_data: dict[str, pd.DataFrame] = {}
        flat_rows: list[dict[str, Any]] = []

        for item in input_data:
            row = dict(item)
            symbol = normalize_symbol(row.get("symbol", ""))
            if not symbol:
                continue
            history = row.get("history", row.get("data", row.get("prices")))
            if isinstance(history, pd.DataFrame):
                market_data[symbol] = history.copy()
                etf_pool.append(self._metadata_from_row(row, symbol))
            elif isinstance(history, Sequence) and not isinstance(history, (str, bytes, bytearray)):
                market_data[symbol] = pd.DataFrame(list(history))
                etf_pool.append(self._metadata_from_row(row, symbol))
            else:
                flat_rows.append(row)

        if flat_rows:
            frame = pd.DataFrame(flat_rows)
            if {"symbol", "date"}.issubset(frame.columns):
                for symbol, group in frame.groupby(frame["symbol"].map(normalize_symbol)):
                    if not symbol:
                        continue
                    market_data[symbol] = group.copy()
                    first = group.iloc[0].to_dict()
                    etf_pool.append(self._metadata_from_row(first, symbol))

        return etf_pool, market_data

    @staticmethod
    def _metadata_from_row(row: Mapping[str, Any], symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "name": str(row.get("name") or symbol),
            "sector": str(row.get("sector") or row.get("category") or row.get("theme") or "未分类"),
            "category": str(row.get("category") or ""),
            "asset_class": str(row.get("asset_class") or ""),
        }

    def _resolve_signal_date(self, market_data: Mapping[str, pd.DataFrame]) -> pd.Timestamp:
        if self.signal_date is not None:
            return self.signal_date
        latest_dates: list[pd.Timestamp] = []
        for frame in market_data.values():
            normalized = self._normalize_frame(frame)
            if not normalized.empty:
                latest_dates.append(pd.Timestamp(normalized.index.max()).normalize())
        if not latest_dates:
            raise ValueError("预选模型没有可用行情数据，无法确定交易日期。")
        return max(latest_dates)

    def _build_metrics(
        self,
        item: Mapping[str, Any],
        market_data: Mapping[str, pd.DataFrame],
        signal_date: pd.Timestamp,
    ) -> _EtfMetrics:
        symbol = normalize_symbol(item.get("symbol", ""))
        name = str(item.get("name") or symbol)
        sector = str(item.get("sector") or item.get("category") or item.get("theme") or "未分类")
        frame = self._normalize_frame(market_data.get(symbol, pd.DataFrame()))
        history = frame.loc[:signal_date].copy() if not frame.empty else pd.DataFrame()
        reasons = self._filter_reasons(history, signal_date)

        close = self._last_float(history.get("close"))
        avg_amount = self._tail_mean(history.get("amount"), self.config.liquidity_window)
        momentum_short = self._momentum(history.get("close"), self.config.short_momentum_window)
        momentum_medium = self._momentum(history.get("close"), self.config.medium_momentum_window)
        momentum_long = self._momentum(history.get("close"), self.config.long_momentum_window)
        acceleration = momentum_short - momentum_medium / 3.0 if np.isfinite(momentum_short) and np.isfinite(momentum_medium) else 0.0
        volatility = self._volatility(history.get("close"), self.config.volatility_window)
        max_drawdown = self._max_drawdown(history.get("close"), self.config.drawdown_window)
        moving_average = self._tail_mean(history.get("close"), self.config.trend_window)
        above_trend = bool(np.isfinite(close) and np.isfinite(moving_average) and close > moving_average)

        return _EtfMetrics(
            symbol=symbol,
            name=name,
            sector=sector,
            filter_passed=not reasons,
            filter_reason="；".join(reasons),
            close=close,
            avg_amount=avg_amount,
            momentum_short=momentum_short,
            momentum_medium=momentum_medium,
            momentum_long=momentum_long,
            acceleration=acceleration,
            volatility=volatility,
            max_drawdown=max_drawdown,
            above_trend=above_trend,
        )

    def _filter_reasons(self, history: pd.DataFrame, signal_date: pd.Timestamp) -> list[str]:
        reasons: list[str] = []
        required = {"open", "high", "low", "close", "volume", "amount"}
        missing = sorted(required - set(history.columns))
        if history.empty:
            return ["缺少行情数据"]
        if missing:
            reasons.append(f"缺少字段：{','.join(missing)}")
            return reasons

        if len(history) < self.config.min_trading_days:
            reasons.append(f"上市或可用交易日不足{self.config.min_trading_days}天")

        latest_date = pd.Timestamp(history.index.max()).normalize()
        stale_days = int((signal_date.normalize() - latest_date).days)
        if stale_days > self.config.max_stale_days:
            reasons.append(f"最新行情滞后{stale_days}天")

        lookback = max(self.config.min_trading_days, self.config.liquidity_window)
        completeness = float(history.tail(lookback)[["close", "amount"]].notna().mean().min())
        if completeness < self.config.min_data_completeness:
            reasons.append(f"数据完整度不足{self.config.min_data_completeness:.0%}")

        amount = pd.to_numeric(history["amount"], errors="coerce")
        avg_amount = float(amount.tail(self.config.liquidity_window).mean())
        if not np.isfinite(avg_amount) or avg_amount < self.config.min_avg_amount:
            reasons.append(f"近{self.config.liquidity_window}日成交额不足")
        if int((amount.tail(self.config.liquidity_window) <= 0).sum()) > self.config.max_zero_amount_days:
            reasons.append("近期存在零成交额")

        open_ = pd.to_numeric(history["open"], errors="coerce")
        high = pd.to_numeric(history["high"], errors="coerce")
        low = pd.to_numeric(history["low"], errors="coerce")
        close = pd.to_numeric(history["close"], errors="coerce")
        if close.isna().any() or (close <= 0).any():
            reasons.append("收盘价缺失或非正数")
        if ((high < low) | (high < open_) | (high < close) | (low > open_) | (low > close)).any():
            reasons.append("OHLC价格关系异常")

        recent_return = close.pct_change().tail(self.config.volatility_window)
        if bool((recent_return.abs() > self.config.max_abs_daily_return).any()):
            reasons.append(f"近期单日涨跌幅超过{self.config.max_abs_daily_return:.0%}")
        return reasons

    def _build_output_rows(
        self,
        metrics: Sequence[_EtfMetrics],
        sectors: pd.DataFrame,
        market_state: str,
        signal_date: pd.Timestamp,
        generated_at: str,
    ) -> list[dict[str, Any]]:
        sector_by_name = sectors.set_index("sector").to_dict("index") if not sectors.empty else {}
        rankable: list[dict[str, Any]] = []
        rows_by_symbol: dict[str, dict[str, Any]] = {}

        for item in metrics:
            sector_info = sector_by_name.get(item.sector, {})
            sector_score = float(sector_info.get("sector_score", 0.0) or 0.0)
            sector_breadth = float(sector_info.get("sector_breadth", 0.0) or 0.0)
            sector_rank = int(sector_info.get("sector_rank", 0) or 0)
            raw_score = (
                0.40 * item.momentum_medium
                + 0.25 * item.momentum_short
                + 0.15 * item.momentum_long
                + 0.15 * sector_score
                + 0.10 * item.acceleration
                - 0.30 * max(item.volatility, 0.0)
                - 0.20 * abs(min(item.max_drawdown, 0.0))
            )
            eligible = (
                item.filter_passed
                and item.above_trend
                and item.momentum_short > 0
                and item.momentum_medium > 0
                and sector_score > 0
                and sector_breadth >= self.config.min_sector_breadth
                and raw_score > self.config.min_candidate_score
            )
            rankable.append({"symbol": item.symbol, "score": raw_score, "eligible": eligible})
            rows_by_symbol[item.symbol] = {
                "trade_date": str(signal_date.date()),
                "symbol": item.symbol,
                "name": item.name,
                "sector": item.sector,
                "market_state": market_state,
                "score": round(raw_score * 100.0, 4) if np.isfinite(raw_score) else 0.0,
                "rank": "",
                "selected": False,
                "reason": "",
                "generated_at": generated_at,
                "_eligible": eligible,
                "_sector_rank": sector_rank,
                "_sector_score": sector_score,
                "_sector_breadth": sector_breadth,
                "_filter_reason": item.filter_reason,
                "_above_trend": item.above_trend,
                "_momentum_short": item.momentum_short,
                "_momentum_medium": item.momentum_medium,
            }

        ranked = sorted(rankable, key=lambda row: float(row["score"]), reverse=True)
        for rank, row in enumerate(ranked, start=1):
            rows_by_symbol[str(row["symbol"])]["rank"] = rank

        candidate_limit = self._candidate_limit(market_state)
        selected_symbols = [
            str(row["symbol"])
            for row in ranked
            if bool(row["eligible"]) and candidate_limit > 0
        ][:candidate_limit]

        for symbol in selected_symbols:
            rows_by_symbol[symbol]["selected"] = True

        rows: list[dict[str, Any]] = []
        for symbol in [str(row["symbol"]) for row in ranked]:
            row = rows_by_symbol[symbol]
            row["reason"] = self._reason(row, bool(row["selected"]), market_state, candidate_limit)
            rows.append({field: row[field] for field in REQUIRED_OUTPUT_FIELDS})
        return rows

    def _candidate_limit(self, market_state: str) -> int:
        if market_state == MarketState.ATTACK.value:
            return self.config.max_candidates
        if market_state == MarketState.BALANCED.value:
            return min(self.config.max_candidates, self.config.balanced_candidates)
        return min(self.config.max_candidates, self.config.defense_candidates)

    @staticmethod
    def _reason(row: Mapping[str, Any], selected: bool, market_state: str, candidate_limit: int) -> str:
        if row["_filter_reason"]:
            return f"过滤：{row['_filter_reason']}。"
        if market_state == MarketState.DEFENSE.value and candidate_limit <= 0:
            return "未入选：市场状态为防守，预选模块不输出候选。"
        if not bool(row["_above_trend"]):
            return "未入选：价格未站上趋势均线，右侧确认不足。"
        if float(row["_momentum_short"]) <= 0 or float(row["_momentum_medium"]) <= 0:
            return "未入选：短中期动量未同时转正。"
        if float(row["_sector_score"]) <= 0:
            return "未入选：所属板块动量得分不为正。"
        if float(row["_sector_breadth"]) < PreSelectionConfig.min_sector_breadth:
            return "未入选：所属板块广度不足。"
        if selected:
            return (
                f"入选：市场状态为{market_state}，板块排名第{int(row['_sector_rank'])}，"
                f"ETF预选排名第{int(row['rank'])}，动量、加速度、广度和风险综合得分靠前。"
            )
        return f"未入选：满足右侧条件，但综合排名未进入前{candidate_limit}。"

    @staticmethod
    def _normalize_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        data = frame.copy()
        if "date" in data.columns:
            data["date"] = pd.to_datetime(data["date"], errors="coerce")
            data = data.dropna(subset=["date"]).set_index("date")
        else:
            data.index = pd.to_datetime(data.index, errors="coerce")
            data = data[~pd.isna(data.index)]
        data = data.sort_index()
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            if column in data.columns:
                data[column] = pd.to_numeric(data[column], errors="coerce")
        return data

    @staticmethod
    def _last_float(series: pd.Series | None) -> float:
        if series is None:
            return 0.0
        clean = pd.to_numeric(series, errors="coerce").dropna()
        return float(clean.iloc[-1]) if not clean.empty else 0.0

    @staticmethod
    def _tail_mean(series: pd.Series | None, window: int) -> float:
        if series is None:
            return 0.0
        clean = pd.to_numeric(series, errors="coerce").dropna().tail(window)
        return float(clean.mean()) if not clean.empty else 0.0

    @staticmethod
    def _momentum(series: pd.Series | None, window: int) -> float:
        if series is None:
            return 0.0
        clean = pd.to_numeric(series, errors="coerce").dropna()
        if len(clean) <= window:
            return 0.0
        base = float(clean.iloc[-window - 1])
        return 0.0 if base <= 0 else float(clean.iloc[-1] / base - 1.0)

    @staticmethod
    def _volatility(series: pd.Series | None, window: int) -> float:
        if series is None:
            return 0.0
        returns = pd.to_numeric(series, errors="coerce").dropna().pct_change().dropna().tail(window)
        return float(returns.std()) if len(returns) >= max(5, window // 2) else 0.0

    @staticmethod
    def _max_drawdown(series: pd.Series | None, window: int) -> float:
        if series is None:
            return 0.0
        clean = pd.to_numeric(series, errors="coerce").dropna().tail(window)
        if len(clean) < max(5, window // 2):
            return 0.0
        return float((clean / clean.cummax() - 1.0).min())
