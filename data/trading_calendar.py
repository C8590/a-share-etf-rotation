from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Iterable

import pandas as pd


MARKET_OPEN_TIME = time(9, 30)
MARKET_CLOSE_TIME = time(15, 0)


def load_a_share_trading_calendar(start: str = "20100101", end: str = "20301231") -> pd.DatetimeIndex:
    try:
        import akshare as ak

        raw = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
        dates = pd.to_datetime(raw[col], errors="coerce").dropna()
        dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
        if not dates.empty:
            return pd.DatetimeIndex(sorted(dates.dt.normalize().unique()))
    except Exception:
        pass
    return pd.bdate_range(start=start, end=end)


def _normalize_calendar(trading_calendar: Iterable[object] | pd.DatetimeIndex | None) -> pd.DatetimeIndex:
    if trading_calendar is None:
        return load_a_share_trading_calendar()
    dates = pd.to_datetime(list(trading_calendar), errors="coerce")
    dates = pd.DatetimeIndex(dates).dropna()
    return pd.DatetimeIndex(sorted(dates.normalize().unique()))


def _as_date(value: object) -> date:
    return pd.Timestamp(value).date()


def is_trading_day(day: date | datetime | pd.Timestamp, trading_calendar: Iterable[object] | pd.DatetimeIndex | None = None) -> bool:
    calendar = _normalize_calendar(trading_calendar)
    return pd.Timestamp(_as_date(day)).normalize() in calendar


def get_market_phase(now: datetime, trading_calendar: Iterable[object] | pd.DatetimeIndex | None = None) -> str:
    calendar = _normalize_calendar(trading_calendar)
    today = pd.Timestamp(now.date()).normalize()
    if today not in calendar:
        return "非交易日"
    if now.time() < MARKET_OPEN_TIME:
        return "盘前"
    if MARKET_OPEN_TIME <= now.time() < MARKET_CLOSE_TIME:
        return "盘中"
    return "已收盘"


def get_previous_trading_day(day: date | datetime | pd.Timestamp, trading_calendar: Iterable[object] | pd.DatetimeIndex | None = None) -> date:
    calendar = _normalize_calendar(trading_calendar)
    current = pd.Timestamp(_as_date(day)).normalize()
    previous = calendar[calendar < current]
    if previous.empty:
        raise ValueError(f"无法找到 {current.date()} 之前的 A股交易日")
    return pd.Timestamp(previous[-1]).date()


def get_next_trading_day(day: date | datetime | pd.Timestamp, trading_calendar: Iterable[object] | pd.DatetimeIndex | None = None) -> date:
    calendar = _normalize_calendar(trading_calendar)
    current = pd.Timestamp(_as_date(day)).normalize()
    future = calendar[calendar > current]
    if future.empty:
        raise ValueError(f"无法找到 {current.date()} 之后的 A股交易日")
    return pd.Timestamp(future[0]).date()


def get_current_trading_day(now: datetime, trading_calendar: Iterable[object] | pd.DatetimeIndex | None = None) -> date:
    calendar = _normalize_calendar(trading_calendar)
    today = pd.Timestamp(now.date()).normalize()
    if today in calendar:
        return today.date()
    previous = calendar[calendar < today]
    if previous.empty:
        raise ValueError(f"无法找到 {today.date()} 之前的 A股交易日")
    return pd.Timestamp(previous[-1]).date()


def check_selected_date_available(selected_signal_date: date, data_cutoff_date: date) -> None:
    if selected_signal_date > data_cutoff_date:
        raise ValueError(
            f"用户选择 {selected_signal_date.isoformat()}，但本地日线数据只更新到 {data_cutoff_date.isoformat()}，"
            f"无法生成 {selected_signal_date.isoformat()} 信号。请先刷新行情，或改为复盘 {data_cutoff_date.isoformat()}。"
        )


@dataclass(frozen=True)
class SignalContext:
    selected_signal_date: date | None
    actual_signal_date: date | None
    data_cutoff_date: date | None
    execution_date: date | None
    market_phase: str
    data_mode: str
    data_quality_status: str
    trade_usage_level: str
    status_message: str
    use_realtime_close_patch: bool = False


def resolve_signal_context(
    selected_signal_date: date | str | None,
    mode: str,
    now: datetime,
    data_cutoff_date: date | str | None,
    trading_calendar: Iterable[object] | pd.DatetimeIndex | None = None,
    use_realtime_close_patch: bool = False,
) -> SignalContext:
    calendar = _normalize_calendar(trading_calendar)
    cutoff = pd.Timestamp(data_cutoff_date).date() if data_cutoff_date else None
    selected = pd.Timestamp(selected_signal_date).date() if selected_signal_date else None
    phase = get_market_phase(now, calendar)
    today_trade_day = get_current_trading_day(now, calendar)

    if mode == "manual_selected_date":
        if selected is None:
            raise ValueError("手动模式必须提供 selected_signal_date")
        if cutoff is None:
            raise ValueError(f"用户选择 {selected.isoformat()}，但本地没有可用日线数据。请先刷新行情。")
        check_selected_date_available(selected, cutoff)
        actual = selected
        data_mode = "实时收盘补全日线" if use_realtime_close_patch and actual == today_trade_day else "本地历史数据"
    elif mode == "auto_latest_available":
        if cutoff is None:
            raise ValueError("本地没有可用日线数据，无法自动生成信号。")
        selected = None
        actual = cutoff
        data_mode = f"自动使用最新可用数据日：{actual.isoformat()}"
    elif mode == "latest_after_refresh":
        today_ts = pd.Timestamp(now.date()).normalize()
        if today_ts not in calendar:
            target = today_trade_day
            expected_mode = "本地历史数据"
        elif now.time() < time(15, 30):
            target = get_previous_trading_day(today_trade_day, calendar)
            expected_mode = "本地历史数据"
        else:
            target = today_trade_day
            expected_mode = "今日收盘数据"
        if cutoff is None or cutoff < target:
            cutoff_text = cutoff.isoformat() if cutoff else "无"
            if cutoff is None:
                raise ValueError(
                    "快速生成只能使用本地数据。当前本地没有可用日线数据，无法生成收盘信号。"
                    "请点击“刷新行情并生成最新信号”。"
                )
            selected = cutoff
            actual = cutoff
            data_mode = f"数据源尚未更新到今日收盘，当前只能生成截至 {cutoff_text} 的信号"
        else:
            selected = target
            actual = target
            data_mode = "实时收盘补全日线" if use_realtime_close_patch and target == today_trade_day else expected_mode
    else:
        raise ValueError(f"未知信号日期模式: {mode}")

    execution = get_next_trading_day(actual, calendar)
    if phase == "已收盘" and actual == today_trade_day:
        status = "今日已收盘，当前信号用于下一交易日执行"
    elif phase == "已收盘" and cutoff and cutoff < today_trade_day:
        status = f"今日已收盘，但本地日线未更新到 {today_trade_day.isoformat()}"
    elif phase == "盘中":
        status = "盘中，只能使用上一完整交易日数据做今日执行参考"
    elif phase == "盘前":
        status = "盘前，使用上一完整交易日数据做今日执行参考"
    else:
        status = "非交易日，使用最近完整交易日数据生成下一交易日计划"

    return SignalContext(
        selected_signal_date=selected,
        actual_signal_date=actual,
        data_cutoff_date=cutoff,
        execution_date=execution,
        market_phase=phase,
        data_mode=data_mode,
        data_quality_status="数据正常",
        trade_usage_level="允许买入",
        status_message=status,
        use_realtime_close_patch=use_realtime_close_patch,
    )


def build_signal_date_status_card(context: SignalContext) -> dict[str, str]:
    def fmt(value: date | None) -> str:
        return value.isoformat() if value else "--"

    return {
        "selected_signal_date": fmt(context.selected_signal_date),
        "actual_signal_date": fmt(context.actual_signal_date),
        "data_cutoff_date": fmt(context.data_cutoff_date),
        "execution_date": fmt(context.execution_date),
        "market_phase": context.market_phase,
        "data_mode": context.data_mode,
        "use_realtime_close_patch": "是" if context.use_realtime_close_patch else "否",
        "data_quality_status": context.data_quality_status,
        "trade_usage_level": context.trade_usage_level,
        "status_message": context.status_message,
    }
