from __future__ import annotations

from pathlib import Path
from typing import Any
import math

import numpy as np
import pandas as pd
import yaml
from datetime import datetime
from zoneinfo import ZoneInfo

from backtest.portfolio import FeeConfig, calculate_trade_cost
from data.quotes import get_etf_quotes
from strategy.etf_rotation import DailyMomentumRotationStrategy, get_rebalance_dates
from signal.trade_policy import (
    QUALITY_LIGHT,
    QUALITY_NORMAL,
    QUALITY_SEVERE,
    QUALITY_UNAVAILABLE,
    apply_data_quality_to_trade_amount,
    build_intraday_execution_plan,
    build_sell_execution_plan,
    calculate_intraday_entry_prices,
    calculate_ladder_orders,
    classify_data_quality,
    translate_buy_reason,
)


DEFAULT_POSITION = {"cash": 0.0, "holdings": [], "current_empty": False, "positions": {}}
INSUFFICIENT_DATA_WARNING = "数据不足，今日不建议买入。"
NO_POSITION_FILE_REASON = "未找到当前持仓文件，仅生成目标组合，不生成完整买卖计划。"
NO_POSITION_INPUT_REASON = "你还没有填写当前持仓。系统只能展示目标组合，无法生成完整买入/卖出计划。请先填写当前持仓和可用现金。"
EMPTY_POSITION_REASON = "当前按空仓处理，本次只生成买入计划，不生成卖出计划。"
MANUAL_EXECUTION_NOTE = "执行日盘中人工限价单确认"
LADDER_EXECUTION_NOTE = "今日只在价格回落到三档买入价附近时分批买入，不建议开盘无条件追高。"
BUY_RISK_NOTE = "如果盘中跌破 60 日均线或触发数据质量异常，取消今日买入。"


def _format_pct(value: float) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{value * 100:.2f}%"


def _fmt_money(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):.2f} 元"


def _fmt_price(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):.3f}"


def _holding_symbol(value: Any) -> str:
    raw = str(value or "").strip()
    return raw.zfill(6) if raw else ""


def _normalize_current_position(data: dict[str, Any], exists: bool) -> dict[str, Any]:
    cash = float(data.get("cash", 0) or 0)
    holdings: list[dict[str, Any]] = []
    raw_holdings = data.get("holdings")

    if isinstance(raw_holdings, list):
        for item in raw_holdings:
            if not isinstance(item, dict):
                continue
            symbol = _holding_symbol(item.get("symbol"))
            shares = float(item.get("shares", 0) or 0)
            if symbol and shares > 0:
                average_price = float(item.get("average_buy_price", item.get("cost_price", 0)) or 0)
                holdings.append(
                    {
                        "symbol": symbol,
                        "shares": shares,
                        "name": str(item.get("name", "")),
                        "average_buy_price": average_price,
                        "cost_price": average_price,
                        "last_buy_date": str(item.get("last_buy_date", "")),
                        "note": str(item.get("note", "")),
                    }
                )
    elif isinstance(data.get("positions"), dict):
        for raw_symbol, item in data.get("positions", {}).items():
            item = item if isinstance(item, dict) else {}
            symbol = _holding_symbol(raw_symbol)
            shares = float(item.get("shares", 0) or 0)
            if symbol and shares > 0:
                average_price = float(item.get("average_buy_price", item.get("cost_price", 0)) or 0)
                holdings.append(
                    {
                        "symbol": symbol,
                        "shares": shares,
                        "name": str(item.get("name", "")),
                        "average_buy_price": average_price,
                        "cost_price": average_price,
                        "last_buy_date": str(item.get("last_buy_date", "")),
                        "note": str(item.get("note", "")),
                    }
                )

    current_empty = bool(data.get("current_empty", False))
    if current_empty:
        holdings = []

    positions = {item["symbol"]: item for item in holdings}
    configured = current_empty or bool(holdings)
    if not exists:
        reason = NO_POSITION_FILE_REASON
    elif current_empty:
        reason = EMPTY_POSITION_REASON
    elif not holdings:
        reason = NO_POSITION_INPUT_REASON
    else:
        reason = ""

    return {
        "cash": cash,
        "holdings": holdings,
        "positions": positions,
        "current_empty": current_empty,
        "position_configured": configured,
        "position_file_exists": exists,
        "position_status_reason": reason,
    }


def ensure_current_position(path: str | Path = "config/current_position.yaml") -> dict[str, Any]:
    current_path = Path(path)
    if not current_path.exists():
        return _normalize_current_position(DEFAULT_POSITION.copy(), exists=False)

    with current_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        data = {}
    return _normalize_current_position(data, exists=True)


def _position_lines(positions: dict[str, Any], etf_info: dict[str, dict[str, str]], prices: pd.Series | None = None) -> list[str]:
    if not positions:
        return ["- 无"]
    lines = []
    for symbol, item in positions.items():
        name = item.get("name") or etf_info.get(symbol, {}).get("name", symbol)
        shares = float(item.get("shares", 0))
        cost_price = float(item.get("cost_price", 0))
        cost_text = f"，平均买入价 {cost_price:.3f}，持仓成本 {shares * cost_price:.2f} 元" if cost_price > 0 else ""
        amount_text = ""
        if prices is not None:
            price = prices.get(symbol)
            if price is not None and not pd.isna(price):
                amount_text = f"，当前估算金额 {shares * float(price):.2f} 元"
        lines.append(f"- {symbol} {name}: {shares:.0f} 份{amount_text}{cost_text}")
    return lines


def _round_buy_shares(target_notional: float, price: float, cash: float, lot_size: int, fee_config: FeeConfig) -> tuple[float, float, float]:
    if target_notional <= 0 or price <= 0 or cash <= 0:
        return 0.0, 0.0, cash
    shares = np.floor((target_notional / price) / lot_size) * lot_size
    while shares >= lot_size:
        notional = float(shares * price)
        costs = calculate_trade_cost(notional, "buy", fee_config)
        if notional + costs["total_cost"] <= cash + 1e-8:
            return float(shares), notional, cash - notional - costs["total_cost"]
        shares -= lot_size
    return 0.0, 0.0, cash


def _symbol_market_frame(strategy: DailyMomentumRotationStrategy, symbol: str, signal_date: pd.Timestamp, market_data: dict[str, pd.DataFrame] | None) -> pd.DataFrame:
    if market_data and symbol in market_data and not market_data[symbol].empty:
        frame = market_data[symbol].copy()
        if "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame = frame.set_index("date")
        frame = frame.sort_index()
        return frame.loc[frame.index <= signal_date].copy()
    if symbol in strategy.close.columns:
        close = strategy.close.loc[strategy.close.index <= signal_date, [symbol]].rename(columns={symbol: "close"})
        return close.copy()
    return pd.DataFrame()


def _should_use_current_quotes(strategy: DailyMomentumRotationStrategy, signal_date: pd.Timestamp) -> bool:
    if strategy.close.empty:
        return False
    latest_data_day = pd.Timestamp(strategy.close.index.max()).date()
    signal_day = pd.Timestamp(signal_date).date()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return signal_day == latest_data_day and 0 <= (today - signal_day).days <= 4


def _build_quote_map(symbols: set[str], enabled: bool) -> dict[str, dict[str, Any]]:
    if not enabled:
        return {}
    try:
        return get_etf_quotes(symbols)
    except Exception as exc:  # noqa: BLE001
        return {
            symbol: {
                "code": symbol,
                "latest_price": None,
                "price_status": "数据不可用",
                "status": "数据不可用",
                "source": "行情接口",
                "quote_date": "",
                "quote_time": "",
                "price_actionable": False,
                "frontend_message": "行情不可用，等待刷新或人工确认。",
                "debug_message": str(exc),
            }
            for symbol in sorted(symbols)
        }


def _quote_price_or_nan(symbol: str, quote_map: dict[str, dict[str, Any]], fallback: Any) -> float:
    quote = quote_map.get(symbol)
    if quote:
        if quote.get("price_actionable") and quote.get("latest_price"):
            return float(quote["latest_price"])
        return float("nan")
    return float(fallback) if fallback is not None and not pd.isna(fallback) else float("nan")


def _quote_block_reason(symbol: str, quote_map: dict[str, dict[str, Any]]) -> str:
    quote = quote_map.get(symbol, {})
    status = quote.get("price_status") or quote.get("status") or "数据不可用"
    if status == "昨日价格，需刷新":
        return "行情非今日数据，今日不生成盘中执行价，暂停新增买入和卖出执行计划。"
    if status == "价格异常，已停用":
        return "行情源返回价格异常，系统已停用该价格，等待人工确认。"
    return "行情不可用，今日不生成盘中执行价。"


def _rank_context(ranks: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if ranks.empty or "symbol" not in ranks.columns:
        return {}
    matched = ranks[ranks["symbol"].astype(str).str.zfill(6) == str(symbol).zfill(6)]
    if matched.empty:
        return {}
    return matched.iloc[0].to_dict()


def _quality_trade_action(level: str) -> str:
    if level == QUALITY_LIGHT:
        return "降低金额买入"
    if level in {QUALITY_SEVERE, QUALITY_UNAVAILABLE}:
        return "禁止买入"
    return "买入"


def _latest_executable_signal_date(market_dates: pd.DatetimeIndex, signal_dates: list[pd.Timestamp]) -> pd.Timestamp:
    date_list = list(pd.DatetimeIndex(sorted(pd.to_datetime(market_dates).unique())))
    executable = [pd.Timestamp(day) for day in signal_dates if pd.Timestamp(day) in date_list and date_list.index(pd.Timestamp(day)) + 1 < len(date_list)]
    if not executable:
        raise ValueError("没有可执行的信号日：最新信号日之后缺少下一交易日，无法模拟执行价")
    return executable[-1]


def resolve_signal_date(
    strategy: DailyMomentumRotationStrategy,
    signal_weekday: int,
    rebalance_frequency: str = "daily",
    rebalance_timing: str = "month_end",
    rebalance_day: int | None = None,
    rebalance_day_of_month: int | None = None,
    rebalance_roll: str = "next",
    signal_date: pd.Timestamp | None = None,
) -> pd.Timestamp:
    if signal_date is not None:
        return pd.Timestamp(signal_date)
    signal_dates = get_rebalance_dates(
        strategy.close.index,
        rebalance_frequency,
        signal_weekday,
        rebalance_timing=rebalance_timing,
        rebalance_day=rebalance_day,
        rebalance_day_of_month=rebalance_day_of_month,
        rebalance_roll=rebalance_roll,
    )
    if not signal_dates:
        raise ValueError("行情日期为空，无法生成信号")
    return _latest_executable_signal_date(strategy.close.index, signal_dates)


def build_signal_trade_plan(
    strategy: DailyMomentumRotationStrategy,
    etf_info: dict[str, dict[str, str]],
    signal_date: pd.Timestamp,
    current_position_path: str | Path = "config/current_position.yaml",
    fee_config: FeeConfig | None = None,
    lot_size: int = 100,
    enable_lot_rounding: bool = True,
    observation_cash: float | None = None,
    market_data: dict[str, pd.DataFrame] | None = None,
    execution_date: pd.Timestamp | None = None,
    daily_risk_check_date: pd.Timestamp | None = None,
) -> dict[str, Any]:
    fee_config = fee_config or FeeConfig()
    signal_date = pd.Timestamp(signal_date)
    current_position = ensure_current_position(current_position_path)
    pricing_date = pd.Timestamp(daily_risk_check_date) if daily_risk_check_date is not None else signal_date
    config_cash = float(current_position.get("cash", 0))
    cash = float(observation_cash) if observation_cash is not None else config_cash
    positions = current_position.get("positions", {}) or {}
    current_holdings = [symbol for symbol, item in positions.items() if float(item.get("shares", 0)) > 0]

    # Target selection must be independent of current holdings and observation cash.
    signal = strategy.generate_target(signal_date, [])
    reason_signal = strategy.generate_target(signal_date, current_holdings) if current_holdings else signal
    target = list(signal["target"])
    ranks = signal["ranks"].copy()
    available_price_dates = strategy.close.index[strategy.close.index <= pricing_date]
    if len(available_price_dates) == 0:
        raise ValueError(f"缺少 {pricing_date.date()} 及以前的行情价格，无法生成执行计划")
    latest_prices = strategy.close.loc[available_price_dates[-1]]
    use_current_quotes = _should_use_current_quotes(strategy, signal_date)
    quote_symbols = set(current_holdings) | (set(target) if len(target) <= 10 else set())
    quote_map = _build_quote_map(quote_symbols, use_current_quotes)
    target_weight = 1 / len(target) if target else 0.0
    target_plan = [
        {
            "ETF代码": symbol,
            "ETF名称": etf_info.get(symbol, {}).get("name", symbol),
            "目标权重": f"{target_weight:.0%}",
            "目标金额": 0.0,
            "入选原因": signal.get("buy_reasons", {}).get(symbol, ""),
        }
        for symbol in target
    ]

    no_action_reasons: list[str] = []
    status_reason = str(current_position.get("position_status_reason", ""))
    if status_reason:
        no_action_reasons.append(status_reason)
    if not current_position.get("position_configured"):
        plan = {
            "signal": signal,
            "target": target,
            "ranks": ranks,
            "current_position": current_position,
            "cash": cash,
            "config_cash": config_cash,
            "target_weight": target_weight,
            "target_value": 0.0,
            "estimated_cash": cash,
            "target_plan": target_plan,
            "buy_plan": [],
            "sell_plan": [],
            "sell_execution_plan": [],
            "hold_plan": [],
            "skipped_buy_plan": [],
            "intraday_execution_plan": [],
            "no_action_reasons": no_action_reasons,
        }
        _annotate_plan_dates(plan, signal_date, execution_date)
        return plan

    sell_plan: list[dict[str, Any]] = []
    sell_execution_plan: list[dict[str, Any]] = []
    hold_plan: list[dict[str, Any]] = []
    buy_plan: list[dict[str, Any]] = []
    skipped_buy_plan: list[dict[str, Any]] = []
    current_values: dict[str, float] = {}
    current_prices: dict[str, float] = {}

    for symbol in current_holdings:
        item = positions.get(symbol, {})
        shares = float(item.get("shares", 0))
        price = _quote_price_or_nan(symbol, quote_map, latest_prices.get(symbol))
        if not pd.isna(price):
            current_values[symbol] = shares * float(price)
            current_prices[symbol] = float(price)

    total_value = cash + sum(current_values.values())
    target_value = total_value * target_weight if target else 0.0
    for item in target_plan:
        item["目标金额"] = target_value

    target_shares: dict[str, float] = {}
    for symbol in target:
        price = _quote_price_or_nan(symbol, quote_map, latest_prices.get(symbol))
        target_shares[symbol] = 0.0 if pd.isna(price) or price <= 0 else float(math.floor(target_value / float(price) / lot_size) * lot_size)

    sell_market_frames = {symbol: _symbol_market_frame(strategy, symbol, pricing_date, market_data) for symbol in current_holdings}
    sell_execution_plan = build_sell_execution_plan(
        positions,
        sell_market_frames,
        set(target),
        lot_size=lot_size,
        quote_map=quote_map,
        target_shares=target_shares,
        signal_date=str(signal_date.date()),
        execution_date=str(pd.Timestamp(execution_date).date()) if execution_date is not None else "",
    )
    sell_plan = [
        {
            **item,
            "ETF代码": item.get("ETF代码"),
            "ETF名称": item.get("ETF名称"),
            "当前持有份额": item.get("持有份额", item.get("current_shares", 0.0)),
            "建议卖出份额": item.get("建议卖出份额", item.get("suggested_sell_shares", 0.0)),
            "卖出原因": reason_signal.get("sell_reasons", {}).get(item.get("ETF代码"))
            or item.get("sell_reason")
            or item.get("卖出说明")
            or item.get("触发说明", ""),
            "参考估算价格": item.get("当前价格", item.get("current_price")),
            "预计卖出金额": (
                float(item.get("建议卖出份额", item.get("suggested_sell_shares", 0.0)) or 0)
                * float(item.get("当前价格", item.get("current_price", 0)) or 0)
            ),
            "实际成交说明": MANUAL_EXECUTION_NOTE,
        }
        for item in sell_execution_plan
        if float(item.get("建议卖出份额", item.get("suggested_sell_shares", 0.0)) or 0) > 0
    ]
    cash_for_buy = cash

    for symbol in sorted(set(current_holdings) & set(target)):
        item = positions.get(symbol, {})
        shares = float(item.get("shares", 0))
        price = _quote_price_or_nan(symbol, quote_map, latest_prices.get(symbol))
        name = etf_info.get(symbol, {}).get("name", item.get("name") or symbol)
        current_value = current_values.get(symbol, 0.0)
        if pd.isna(price):
            action = "不操作"
            reason = "缺少信号日参考价格，不能用其他价格替代"
        elif current_value < target_value * 0.99:
            action = "需要补仓"
            reason = "当前估算金额低于目标金额"
        elif current_value > target_value * 1.01:
            action = "需要减仓"
            reason = "当前估算金额高于目标金额"
        else:
            action = "不操作"
            reason = "当前估算金额接近目标金额"
        reason = reason_signal.get("keep_reasons", {}).get(symbol, reason)
        hold_plan.append(
            {
                "ETF代码": symbol,
                "ETF名称": name,
                "当前份额": shares,
                "当前估算金额": current_value,
                "目标金额": target_value,
                "是否需要补仓 / 减仓 / 不操作": action,
                "原因": reason,
            }
        )

    estimated_cash = cash_for_buy
    for symbol in target:
        price = _quote_price_or_nan(symbol, quote_map, latest_prices.get(symbol))
        name = etf_info.get(symbol, {}).get("name", symbol)
        rank_context = _rank_context(ranks, symbol)
        quality = classify_data_quality({**rank_context, "close": price})
        quality_level = str(quality["level"])
        current_value = current_values.get(symbol, 0.0)
        adjusted_target_value = target_value * float(quality["amount_multiplier"])
        buy_gap = max(adjusted_target_value - current_value, 0.0)
        if buy_gap <= 0:
            continue
        if symbol in quote_map and (not quote_map[symbol].get("price_actionable") or not quote_map[symbol].get("daily_history_valid", True)):
            history_message = quote_map[symbol].get("daily_history_message")
            skipped_buy_plan.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": name,
                    "交易动作": "暂停买入",
                    "数据质量": "行情价格不可用",
                    "资金不足时的提示": history_message or _quote_block_reason(symbol, quote_map),
                    "一手所需资金": None,
                    "当前可用现金": estimated_cash,
                    "价格状态": quote_map[symbol].get("price_status"),
                    "报价日期": quote_map[symbol].get("quote_date"),
                    "报价时间": quote_map[symbol].get("quote_time"),
                    "价格来源": quote_map[symbol].get("source"),
                }
            )
            continue
        if pd.isna(price):
            skipped_buy_plan.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": name,
                    "资金不足时的提示": "缺少信号日参考价格，不能用其他价格替代",
                    "一手所需资金": None,
                    "当前可用现金": estimated_cash,
                }
            )
            continue
        if quality_level in {QUALITY_SEVERE, QUALITY_UNAVAILABLE}:
            skipped_buy_plan.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": name,
                    "交易动作": "禁止买入",
                    "数据质量": quality_level,
                    "资金不足时的提示": f"{quality['前端说明']}；{quality['交易动作']}",
                    "一手所需资金": None,
                    "当前可用现金": estimated_cash,
                }
            )
            continue

        planned_amount = apply_data_quality_to_trade_amount(max(target_value - current_value, 0.0), quality_level)
        planned_amount = min(planned_amount, estimated_cash)
        market_frame = _symbol_market_frame(strategy, symbol, pricing_date, market_data)
        entry_prices = calculate_intraday_entry_prices(market_frame, realtime_price=float(price))
        if not entry_prices:
            ladder = calculate_intraday_entry_prices(pd.DataFrame({"close": [float(price)], "high": [float(price)], "low": [float(price)]}))
            entry_prices = ladder or {
                "第一买入价": round(float(price), 3),
                "第二买入价": round(max(float(price) * 0.985, 0.001), 3),
                "第三买入价": round(max(float(price) * 0.970, 0.001), 3),
                "失效条件": "若触发失效条件，今日三档买入价全部取消，不再新增买入。",
            }
        ladder_orders = calculate_ladder_orders(planned_amount, entry_prices, lot_size=lot_size)
        executable_ladders = [item for item in ladder_orders if float(item.get("建议买入份额", 0) or 0) >= lot_size]
        if enable_lot_rounding:
            shares = float(sum(float(item["建议买入份额"]) for item in executable_ladders))
            notional = float(sum(float(item["建议买入份额"]) * float(item["买入价"]) for item in executable_ladders))
            costs = calculate_trade_cost(notional, "buy", fee_config)
            if notional + costs["total_cost"] > estimated_cash and executable_ladders:
                affordable_amount = max(estimated_cash - costs["total_cost"], 0.0)
                ladder_orders = calculate_ladder_orders(affordable_amount, entry_prices, lot_size=lot_size)
                executable_ladders = [item for item in ladder_orders if float(item.get("建议买入份额", 0) or 0) >= lot_size]
                shares = float(sum(float(item["建议买入份额"]) for item in executable_ladders))
                notional = float(sum(float(item["建议买入份额"]) * float(item["买入价"]) for item in executable_ladders))
            estimated_cash -= notional + calculate_trade_cost(notional, "buy", fee_config)["total_cost"]
        else:
            shares = planned_amount / float(price)
            notional = planned_amount
            estimated_cash -= notional + calculate_trade_cost(notional, "buy", fee_config)["total_cost"]
        if shares > 0:
            buy_reason = translate_buy_reason(
                reason_signal.get("buy_reasons", {}).get(symbol, ""),
                {
                    **rank_context,
                    "name": name,
                    "momentum_period": getattr(getattr(strategy, "config", None), "momentum_period", 60),
                    "ma_period": getattr(getattr(strategy, "config", None), "ma_period", 60),
                    "quality_level": quality_level,
                    "in_actual_buy_plan": True,
                },
            )
            buy_plan.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": name,
                    "交易动作": "已有持仓，计划加仓" if current_value > 0 else _quality_trade_action(quality_level),
                    "当前持仓份额": float(positions.get(symbol, {}).get("shares", 0) or 0),
                    "目标权重": f"{target_weight:.0%}",
                    "目标金额": adjusted_target_value,
                    "当前已持仓市值": current_value,
                    "仍需买入金额": planned_amount,
                    "今日建议买入金额": notional,
                    "第一买入价": entry_prices.get("第一买入价"),
                    "第二买入价": entry_prices.get("第二买入价"),
                    "第三买入价": entry_prices.get("第三买入价"),
                    "建议买入份额": shares,
                    "预计买入金额": notional,
                    "数据质量": quality_level,
                    "资金不足时的提示": "",
                    "执行说明": LADDER_EXECUTION_NOTE,
                    "实际成交说明": LADDER_EXECUTION_NOTE,
                    "买入原因": buy_reason,
                    "风险提示": BUY_RISK_NOTE,
                    "价格状态": quote_map.get(symbol, {}).get("price_status", "信号日参考价"),
                    "报价日期": quote_map.get(symbol, {}).get("quote_date", ""),
                    "报价时间": quote_map.get(symbol, {}).get("quote_time", ""),
                    "价格来源": quote_map.get(symbol, {}).get("source", ""),
                    "失效条件": entry_prices.get("失效条件"),
                    "每档计划": ladder_orders,
                    "reason": buy_reason,
                }
            )
        else:
            first_price = float(entry_prices.get("第一买入价") or price)
            one_lot_notional = first_price * lot_size
            one_lot_cash = one_lot_notional + calculate_trade_cost(one_lot_notional, "buy", fee_config)["total_cost"]
            if estimated_cash < one_lot_cash:
                reason = "当前可用现金不足以买入一手"
            elif planned_amount < one_lot_cash:
                reason = "目标补足金额不足一手，按交易单位取整后跳过"
            else:
                reason = "按交易单位和费用约束取整后无法买入一手"
            skipped_buy_plan.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": name,
                    "交易动作": "仅观察",
                    "数据质量": quality_level,
                    "资金不足时的提示": reason,
                    "一手所需资金": one_lot_cash,
                    "当前可用现金": estimated_cash,
                }
            )

    if current_position.get("position_configured") and not sell_plan and not buy_plan and not skipped_buy_plan:
        no_action_reasons.append("当前持仓与目标组合接近，或无需按 100 份交易单位调整。")

    plan = {
        "signal": signal,
        "target": target,
        "ranks": ranks,
        "current_position": current_position,
        "cash": cash,
        "config_cash": config_cash,
        "target_weight": target_weight,
        "target_value": target_value,
        "estimated_cash": estimated_cash,
        "target_plan": target_plan,
        "buy_plan": buy_plan,
        "sell_plan": sell_plan,
        "sell_execution_plan": sell_execution_plan,
        "hold_plan": hold_plan,
        "skipped_buy_plan": skipped_buy_plan,
        "intraday_execution_plan": build_intraday_execution_plan(buy_plan, market_data),
        "no_action_reasons": no_action_reasons,
    }
    _annotate_plan_dates(plan, signal_date, execution_date)
    return plan


def _annotate_plan_dates(plan: dict[str, Any], signal_date: pd.Timestamp, execution_date: pd.Timestamp | None) -> None:
    signal_text = str(pd.Timestamp(signal_date).date())
    execution_text = str(pd.Timestamp(execution_date).date()) if execution_date is not None else ""
    buy_note = f"本计划基于 {signal_text} 收盘数据生成，用于 {execution_text} 盘中执行。" if execution_text else ""
    sell_note = f"本卖出计划基于 {signal_text} 收盘后的持仓和行情生成，用于 {execution_text} 盘中风控或止盈。" if execution_text else ""
    for key in ["buy_plan", "intraday_execution_plan", "skipped_buy_plan"]:
        for item in plan.get(key, []) or []:
            item["信号依据日期"] = signal_text
            item["计划执行日期"] = execution_text
            if buy_note:
                item["执行说明"] = buy_note
    for key in ["sell_plan", "sell_execution_plan"]:
        for item in plan.get(key, []) or []:
            item["信号依据日期"] = signal_text
            item["计划执行日期"] = execution_text
            if sell_note:
                item["卖出说明"] = item.get("卖出说明") or item.get("卖出原因", sell_note)
                item["实际成交说明"] = sell_note
    for key in ["hold_plan", "target_plan"]:
        for item in plan.get(key, []) or []:
            item["信号依据日期"] = signal_text
            item["计划执行日期"] = execution_text


def generate_daily_signal_text(
    strategy: DailyMomentumRotationStrategy,
    equity_curve: pd.DataFrame,
    etf_info: dict[str, dict[str, str]],
    signal_weekday: int,
    output_path: str | Path = "output/daily_signal.txt",
    current_position_path: str | Path = "config/current_position.yaml",
    fee_config: FeeConfig | None = None,
    lot_size: int = 100,
    enable_lot_rounding: bool = True,
    effective_etf_count: int | None = None,
    min_effective_etf_count: int = 5,
    rebalance_frequency: str = "daily",
    rebalance_timing: str = "month_end",
    rebalance_day: int | None = None,
    rebalance_day_of_month: int | None = None,
    rebalance_roll: str = "next",
    signal_date: pd.Timestamp | None = None,
    execution_date: pd.Timestamp | None = None,
    observation_cash: float | None = None,
    market_data: dict[str, pd.DataFrame] | None = None,
) -> str:
    latest_signal_date = resolve_signal_date(
        strategy,
        signal_weekday,
        rebalance_frequency=rebalance_frequency,
        rebalance_timing=rebalance_timing,
        rebalance_day=rebalance_day,
        rebalance_day_of_month=rebalance_day_of_month,
        rebalance_roll=rebalance_roll,
        signal_date=signal_date,
    )
    if latest_signal_date not in equity_curve.index:
        raise ValueError(f"权益曲线缺少信号日期: {latest_signal_date.date()}")

    plan = build_signal_trade_plan(
        strategy,
        etf_info,
        latest_signal_date,
        current_position_path=current_position_path,
        fee_config=fee_config,
        lot_size=lot_size,
        enable_lot_rounding=enable_lot_rounding,
        observation_cash=observation_cash,
        market_data=market_data,
        execution_date=execution_date,
    )
    positions = plan["current_position"].get("positions", {}) or {}
    target = plan["target"]
    ranks = plan["ranks"]
    latest_prices = strategy.close.loc[latest_signal_date]

    lines: list[str] = []
    lines.append("日频右侧确认型 ETF 动量轮动策略 - 最新信号")
    lines.append("=" * 42)
    lines.append(f"信号日期: {latest_signal_date.date()}")
    execution_text = str(pd.Timestamp(execution_date).date()) if execution_date is not None else "下一个交易日"
    lines.append(f"计划执行日期: {execution_text}")
    lines.append("执行时间: 下一个交易日盘中人工限价单确认；不自动下单，不连接券商")
    if effective_etf_count is not None and effective_etf_count < min_effective_etf_count:
        lines.append(f"数据质量提示: {INSUFFICIENT_DATA_WARNING}")
        lines.append(f"当前有效ETF数量: {effective_etf_count}/{min_effective_etf_count}")
    lines.append("")

    lines.append("【当前持仓】")
    lines.append(f"可用现金：{plan['cash']:.2f} 元")
    lines.append("持仓列表：")
    lines.extend(_position_lines(positions, etf_info, latest_prices))
    for reason in plan["no_action_reasons"]:
        if reason in {NO_POSITION_FILE_REASON, NO_POSITION_INPUT_REASON, EMPTY_POSITION_REASON}:
            lines.append(f"- {reason}")
    lines.append("")

    lines.append("【目标组合】")
    if target:
        for item in plan["target_plan"]:
            reason = f"，入选原因：{item['入选原因']}" if item.get("入选原因") else ""
            lines.append(f"- {item['ETF代码']} {item['ETF名称']}: 目标权重 {item['目标权重']}，目标金额 {_fmt_money(item['目标金额'])}{reason}")
    else:
        lines.append("- 空仓: 100% 现金")
    lines.append("")

    lines.append("【买入计划】")
    lines.append(f"本计划基于 {latest_signal_date.date()} 收盘数据生成，用于 {execution_text} 盘中执行。")
    if plan["buy_plan"]:
        for item in plan["buy_plan"]:
            lines.append(
                f"- {item['ETF代码']} {item['ETF名称']}: 目标权重 {item['目标权重']}，目标金额 {_fmt_money(item['目标金额'])}，"
                f"交易动作：{item['交易动作']}，建议买入 {item['建议买入份额']:.0f} 份，"
                f"今日建议买入金额 {_fmt_money(item['今日建议买入金额'])}。"
                f"三档买入价：{item['第一买入价']:.3f} / {item['第二买入价']:.3f} / {item['第三买入价']:.3f}。"
                f"买入原因：{item.get('买入原因', item.get('reason', ''))}。执行说明：{item['执行说明']}"
            )
    elif not plan["current_position"].get("position_configured"):
        lines.append(f"- {plan['current_position'].get('position_status_reason')}")
    else:
        lines.append("- 无")
    for item in plan["skipped_buy_plan"]:
        lines.append(
            f"- {item['ETF代码']} {item['ETF名称']}: {item['资金不足时的提示']}；"
            f"一手所需资金 {_fmt_money(item['一手所需资金'])}；当前可用现金 {_fmt_money(item['当前可用现金'])}"
        )
    lines.append("")

    lines.append("【盘中买入执行计划】")
    lines.append(f"信号依据日期：{latest_signal_date.date()}；计划执行日期：{execution_text}")
    if plan["intraday_execution_plan"]:
        for item in plan["intraday_execution_plan"]:
            price_text = "" if item.get("档位") == "不生成" else f"买入价 {_fmt_price(item.get('买入价'))}，"
            amount_text = "" if item.get("档位") == "不生成" else f"建议金额 {_fmt_money(item.get('建议买入金额'))}，建议份额 {float(item.get('建议买入份额') or 0):.0f} 份，"
            lines.append(
                f"- {item['ETF代码']} {item['ETF名称']} {item['档位']}：{price_text}{amount_text}"
                f"{item.get('触发说明', '')}"
            )
        lines.append("- 若触发失效条件，今日三档买入价全部取消，不再新增买入。")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("【卖出计划】")
    lines.append(f"本卖出计划基于 {latest_signal_date.date()} 收盘后的持仓和行情生成，用于 {execution_text} 盘中风控或止盈。")
    if plan["sell_plan"]:
        for item in plan["sell_plan"]:
            lines.append(
                f"- {item['ETF代码']} {item['ETF名称']}: 当前持有 {item['当前持有份额']:.0f} 份，建议卖出 {item['建议卖出份额']:.0f} 份，"
                f"卖出原因：{item['卖出原因']}，参考估算价格 {_fmt_price(item['参考估算价格'])}，预计卖出金额 {_fmt_money(item['预计卖出金额'])}。"
                f"实际成交说明：{item['实际成交说明']}"
            )
    elif plan["current_position"].get("current_empty"):
        lines.append(f"- {EMPTY_POSITION_REASON}")
    elif not plan["current_position"].get("position_configured"):
        lines.append(f"- {plan['current_position'].get('position_status_reason')}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("【持仓卖出执行计划】")
    lines.append(f"信号依据日期：{latest_signal_date.date()}；计划执行日期：{execution_text}")
    if plan["sell_execution_plan"]:
        for item in plan["sell_execution_plan"]:
            trigger_display = item.get("risk_trigger_display")
            if trigger_display in ("", None):
                trigger_display = _fmt_price(item.get("风控触发价"))
            trigger_source = item.get("risk_trigger_source") or item.get("触发价来源") or "未标注"
            trigger_warning = item.get("risk_trigger_warning") or ""
            lines.append(
                f"- {item['ETF代码']} {item['ETF名称']}: 交易动作：{item['交易动作']}，持有 {item['持有份额']:.0f} 份，"
                f"平均买入价 {_fmt_price(item['平均买入价'])}，当前价格 {_fmt_price(item['当前价格'])}，"
                f"浮动盈亏率 {_format_pct(item['浮动盈亏率'])}。"
                f"三档止盈价：{_fmt_price(item['第一止盈价'])} / {_fmt_price(item['第二止盈价'])} / {_fmt_price(item['第三止盈价'])}；"
                f"风控触发价 {trigger_display}（来源：{trigger_source}）{f'；{trigger_warning}' if trigger_warning else ''}；"
                f"三档卖出价：{_fmt_price(item.get('第一卖出价'))} / {_fmt_price(item.get('第二卖出价'))} / {_fmt_price(item.get('第三卖出价'))}。"
                f"卖出说明：{item['卖出说明']} 风险提示：{item['风险提示']}"
            )
    elif plan["current_position"].get("current_empty"):
        lines.append("- 当前为空仓，暂无卖出计划。系统只生成买入计划。")
    elif not plan["current_position"].get("position_configured"):
        lines.append(f"- {plan['current_position'].get('position_status_reason')}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("【继续持有】")
    if plan["hold_plan"]:
        for item in plan["hold_plan"]:
            lines.append(
                f"- {item['ETF代码']} {item['ETF名称']}: 当前份额 {item['当前份额']:.0f} 份，"
                f"当前估算金额 {_fmt_money(item['当前估算金额'])}，目标金额 {_fmt_money(item['目标金额'])}，"
                f"{item['是否需要补仓 / 减仓 / 不操作']}。原因：{item['原因']}"
            )
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("【不操作原因】")
    reasons = plan["no_action_reasons"] or ["无"]
    lines.extend(f"- {reason}" for reason in reasons)
    lines.append(f"- 预计剩余现金：{plan['estimated_cash']:.2f} 元")

    lines.append("")
    lines.append("当前排名与指标:")
    if ranks.empty:
        lines.append("- 指标不足，无法排名")
    else:
        momentum_period = getattr(getattr(strategy, "config", None), "momentum_period", 20)
        ma_period = getattr(getattr(strategy, "config", None), "ma_period", 60)
        for _, row in ranks.iterrows():
            above_text = "是" if bool(row["above_ma"]) else "否"
            selected_text = "是" if bool(row.get("selected", row["symbol"] in target)) else "否"
            lines.append(
                f"- 第{int(row['rank'])}名 {row['symbol']} {row['name']}: "
                f"{momentum_period}日动量 {_format_pct(row['momentum'])}, "
                f"高于{ma_period}日均线: {above_text}, "
                f"是否入选: {selected_text}, "
                f"收盘价 {row['close']:.4f}, {ma_period}日均线 {row['ma']:.4f}"
            )

    lines.append("")
    lines.append("操作原因:")
    if not plan["sell_plan"] and not plan["buy_plan"]:
        lines.append("- 当前没有可执行的买入或卖出计划，详见【不操作原因】。")
    if target and enable_lot_rounding:
        lines.append(f"- 买入建议已按 A 股 ETF 最小交易单位 {lot_size} 份取整，剩余现金保留。")

    lines.append("")
    lines.append("风险提示:")
    lines.append("- 本项目不构成投资建议，信号只用于小资金人工验证。")
    lines.append("- 回测不代表未来收益，ETF 可能出现连续回撤、流动性变化和跟踪误差。")
    lines.append("- 本系统不自动下单；实盘下单前请手动确认价格、份额、手续费和账户可用资金。")

    text = "\n".join(lines) + "\n"
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def run_modular_signal_pipeline(
    *,
    etf_pool: list[dict[str, Any]] | None = None,
    market_data: dict[str, pd.DataFrame] | None = None,
    holdings: list[dict[str, Any]] | None = None,
    closed_trades: list[dict[str, Any]] | None = None,
    output_dir: str | Path = "output",
    signal_date: str | pd.Timestamp | None = None,
    current_position_path: str | Path = "config/current_position.yaml",
    write_daily_csv: bool = True,
) -> dict[str, Any]:
    """Run the four modular signal engines as an additive daily workflow.

    The legacy daily signal path remains untouched. This orchestration writes
    the four contract CSV files plus a compact ``daily_signal.csv`` summary, and
    returns fields that callers can merge into the existing compare signal row.
    Missing inputs degrade to empty module outputs with Chinese warnings.
    """

    import csv
    import json

    from contracts.signal_schema import (
        ENTRY_SIGNAL_FIELDS,
        EXIT_SIGNAL_FIELDS,
        LEARNING_REPORT_FIELDS,
        PRE_SELECTION_RESULT_FIELDS,
        MarketState,
    )
    from signal.control_data_foundation import write_signal_cases
    from signal.entry import EntryEngine
    from signal.exit import ExitEngine
    from signal.learning import LearningEngine
    from signal.pre_selection import PreSelectionEngine

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    generated_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    trade_date = str(pd.Timestamp(signal_date).date()) if signal_date is not None else generated_at[:10]

    def _write_contract(filename: str, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
        with (output_path / filename).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fields})

    try:
        pre_selection_rows = PreSelectionEngine(
            etf_pool=etf_pool,
            market_data=market_data,
            signal_date=signal_date,
        ).run(output_dir=output_path)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"选前模型降级：{exc}")
        pre_selection_rows = []
        _write_contract("pre_selection_result.csv", PRE_SELECTION_RESULT_FIELDS, pre_selection_rows)

    try:
        entry_rows = EntryEngine(generated_at=generated_at).run(pre_selection_rows, output_dir=output_path)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"买入模型降级：{exc}")
        entry_rows = []
        _write_contract("entry_signal.csv", ENTRY_SIGNAL_FIELDS, entry_rows)

    try:
        write_signal_cases(
            pre_selection_rows,
            entry_rows,
            output_dir=output_path,
            signal_version="V2_MODULAR",
            market_data=market_data,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"总控信号病例库降级：{exc}")

    market_state = _first_text(pre_selection_rows, "market_state", default=MarketState.BALANCED.value)
    selected_rows = [row for row in pre_selection_rows if _modular_truthy(row.get("selected"))]
    entry_by_symbol = {_modular_symbol(row.get("symbol")): row for row in entry_rows}
    pre_by_symbol = {_modular_symbol(row.get("symbol")): row for row in pre_selection_rows}
    exit_candidates = [_merge_signal_rows(row, entry_by_symbol.get(_modular_symbol(row.get("symbol")), {})) for row in selected_rows]

    if holdings is None:
        try:
            current_position = ensure_current_position(current_position_path)
            holdings = [dict(item) for item in current_position.get("holdings", [])]
            if not current_position.get("position_configured"):
                reason = current_position.get("position_status_reason") or "未配置当前持仓，退出模型按空持仓处理。"
                warnings.append(str(reason))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"当前持仓读取失败，退出模型按空持仓处理：{exc}")
            holdings = []
    else:
        holdings = [dict(item) for item in holdings]

    enriched_holdings = [
        _enrich_holding_for_exit(
            holding,
            trade_date=trade_date,
            market_state=market_state,
            pre_row=pre_by_symbol.get(_modular_symbol(holding.get("symbol")), {}),
            entry_row=entry_by_symbol.get(_modular_symbol(holding.get("symbol")), {}),
            market_data=market_data or {},
            signal_date=signal_date,
        )
        for holding in holdings
    ]

    try:
        exit_rows = ExitEngine().run(
            enriched_holdings,
            output_dir=output_path,
            market_context={"market_state": market_state, "trade_date": trade_date},
            candidates=exit_candidates,
            trade_date=trade_date,
            source_file="current_position",
            generated_at=generated_at,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"退出模型降级：{exc}")
        exit_rows = []
        _write_contract("exit_signal.csv", EXIT_SIGNAL_FIELDS, exit_rows)

    if closed_trades is None:
        closed_trades = _load_closed_trades_for_learning(Path("data") / "portfolio_trades.csv")
        if not closed_trades:
            warnings.append("暂无可复盘的已完成交易，学习模型输出为空报告。")
    else:
        closed_trades = [dict(item) for item in closed_trades]

    learning_engine = LearningEngine()
    for row in entry_rows:
        trade_id = _modular_symbol(row.get("symbol"))
        if trade_id:
            learning_engine.record_buy_snapshot(trade_id, row)
    for row in exit_rows:
        trade_id = _modular_symbol(row.get("symbol"))
        if trade_id:
            learning_engine.record_sell_snapshot(trade_id, row)

    try:
        learning_rows = learning_engine.run(closed_trades, output_dir=output_path)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"学习模型降级：{exc}")
        learning_rows = []
        _write_contract("learning_report.csv", LEARNING_REPORT_FIELDS, learning_rows)

    summary_fields = _modular_summary_fields(
        market_state=market_state,
        pre_selection_rows=pre_selection_rows,
        entry_rows=entry_rows,
        exit_rows=exit_rows,
        learning_rows=learning_rows,
        warnings=warnings,
        generated_at=generated_at,
    )
    decision_chain = _modular_decision_chain(
        market_state=market_state,
        pre_selection_rows=pre_selection_rows,
        entry_rows=entry_rows,
        exit_rows=exit_rows,
        learning_rows=learning_rows,
        fallback_reason=summary_fields.get("fallback_reason", "无"),
    )

    if write_daily_csv:
        pd.DataFrame([summary_fields]).to_csv(output_path / "daily_signal.csv", index=False, encoding="utf-8-sig")
        (output_path / "daily_signal_modular.json").write_text(
            json.dumps(
                {
                    **decision_chain,
                    "summary": summary_fields,
                    "pre_selection": pre_selection_rows,
                    "entry": entry_rows,
                    "exit": exit_rows,
                    "learning": learning_rows,
                    "warnings": warnings,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return {
        "pre_selection": pre_selection_rows,
        "entry": entry_rows,
        "exit": exit_rows,
        "learning": learning_rows,
        "warnings": warnings,
        "summary_fields": summary_fields,
        "decision_chain": decision_chain,
    }


def _modular_summary_fields(
    *,
    market_state: str,
    pre_selection_rows: list[dict[str, Any]],
    entry_rows: list[dict[str, Any]],
    exit_rows: list[dict[str, Any]],
    learning_rows: list[dict[str, Any]],
    warnings: list[str],
    generated_at: str,
) -> dict[str, Any]:
    selected_rows = [row for row in pre_selection_rows if _modular_truthy(row.get("selected"))]
    selected_sectors = _unique_text(row.get("sector") for row in selected_rows)
    selected_symbols = [_modular_symbol(row.get("symbol")) for row in selected_rows if _modular_symbol(row.get("symbol"))]
    candidate_etfs = [f"{row.get('symbol', '')} {row.get('name', '')}".strip() for row in selected_rows]
    selected_symbol_set = set(selected_symbols)
    buy_actions = [
        f"{row.get('symbol', '')}:{row.get('buy_action', '')}"
        for row in entry_rows
        if _modular_symbol(row.get("symbol")) in selected_symbol_set and str(row.get("buy_action", "")).strip()
    ]
    exit_actions = [f"{row.get('symbol', '')}:{row.get('sell_action', '')}" for row in exit_rows if str(row.get("sell_action", "")).strip()]
    learning_advice = [
        str(row.get("adjustment") or row.get("lesson") or "").strip()
        for row in learning_rows
        if str(row.get("adjustment") or row.get("lesson") or "").strip()
    ]
    if not learning_advice:
        learning_advice = ["暂无完成交易可复盘，学习模型本次仅保持观察。"]

    return {
        "modular_pipeline_status": "已完成" if not warnings else "已完成（含降级）",
        "modular_pipeline_warnings": " | ".join(warnings) if warnings else "无",
        "modular_market_state": market_state or "均衡",
        "modular_selected_sectors": "、".join(selected_sectors) if selected_sectors else "无",
        "modular_candidate_etfs": "、".join(candidate_etfs) if candidate_etfs else "无",
        "modular_buy_actions": " | ".join(buy_actions) if buy_actions else "无",
        "modular_exit_actions": " | ".join(exit_actions) if exit_actions else "无",
        "modular_learning_advice": " | ".join(learning_advice[:3]),
        "modular_pre_selection_count": len(pre_selection_rows),
        "modular_candidate_count": len(selected_rows),
        "modular_entry_count": len(entry_rows),
        "modular_exit_count": len(exit_rows),
        "modular_learning_count": len(learning_rows),
        "modular_generated_at": generated_at,
        "v2_selected_etfs": ",".join(selected_symbols),
        "v2_market_state": market_state or "均衡",
        "v2_selected_sectors": "、".join(selected_sectors) if selected_sectors else "无",
        "v2_entry_actions": " | ".join(buy_actions) if buy_actions else "无",
        "v2_reason": _modular_v2_reason(selected_rows, entry_rows),
        "fallback_reason": " | ".join(warnings) if warnings else "无",
    }


def _modular_v2_reason(selected_rows: list[dict[str, Any]], entry_rows: list[dict[str, Any]]) -> str:
    selected_symbols = {_modular_symbol(row.get("symbol")) for row in selected_rows}
    reasons = [
        str(row.get("reason", "")).strip()
        for row in selected_rows
        if str(row.get("reason", "")).strip()
    ]
    reasons.extend(
        str(row.get("entry_reason", "")).strip()
        for row in entry_rows
        if _modular_symbol(row.get("symbol")) in selected_symbols and str(row.get("entry_reason", "")).strip()
    )
    return " | ".join(reasons[:8]) if reasons else "V2 无入选候选，通常由防守市场、趋势过滤或数据不足触发。"


def _modular_decision_chain(
    *,
    market_state: str,
    pre_selection_rows: list[dict[str, Any]],
    entry_rows: list[dict[str, Any]],
    exit_rows: list[dict[str, Any]],
    learning_rows: list[dict[str, Any]],
    fallback_reason: str,
) -> dict[str, Any]:
    selected_rows = [row for row in pre_selection_rows if _modular_truthy(row.get("selected"))]
    return {
        "market_state": market_state,
        "selected_sectors": _unique_text(row.get("sector") for row in selected_rows),
        "pre_selection_candidates": selected_rows,
        "entry_signals": entry_rows,
        "exit_signals": exit_rows,
        "learning_summary": [
            {
                "trade_id": row.get("trade_id", ""),
                "symbol": row.get("symbol", ""),
                "failure_attribution": row.get("failure_attribution", ""),
                "lesson": row.get("lesson", ""),
                "adjustment": row.get("adjustment", ""),
            }
            for row in learning_rows
        ],
        "fallback_reason": fallback_reason,
    }


def _merge_signal_rows(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    merged.update({key: value for key, value in right.items() if value not in ("", None)})
    if "buy_quality" not in merged and "confidence" in merged:
        merged["buy_quality"] = merged.get("confidence")
    return merged


def _enrich_holding_for_exit(
    holding: dict[str, Any],
    *,
    trade_date: str,
    market_state: str,
    pre_row: dict[str, Any],
    entry_row: dict[str, Any],
    market_data: dict[str, pd.DataFrame],
    signal_date: str | pd.Timestamp | None,
) -> dict[str, Any]:
    row = dict(holding)
    symbol = _modular_symbol(row.get("symbol"))
    row["symbol"] = symbol
    row.setdefault("trade_date", trade_date)
    row.setdefault("market_state", pre_row.get("market_state") or market_state)
    row.setdefault("sector", pre_row.get("sector", ""))
    row.setdefault("current_score", pre_row.get("score", 0))
    row.setdefault("buy_action", entry_row.get("buy_action", ""))
    row.setdefault("source_file", "current_position")

    frame = market_data.get(symbol)
    if frame is not None and not frame.empty:
        prices = _modular_close_series(frame, signal_date)
        if not prices.empty:
            row.setdefault("current_price", float(prices.iloc[-1]))
            row.setdefault("peak_price", float(prices.tail(60).max()))
            row.setdefault("trend_line", float(prices.tail(60).mean()))
    if "current_price" not in row and row.get("average_buy_price"):
        row["current_price"] = row.get("average_buy_price")
        row.setdefault("data_warning", "缺少最新行情，临时使用持仓成本作为退出模型参考价。")
    return row


def _modular_close_series(frame: pd.DataFrame, signal_date: str | pd.Timestamp | None) -> pd.Series:
    data = frame.copy()
    if "date" in data.columns:
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        data = data.set_index("date")
    data = data.sort_index()
    if signal_date is not None:
        data = data.loc[data.index <= pd.Timestamp(signal_date)]
    if "close" not in data.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(data["close"], errors="coerce").dropna()


def _load_closed_trades_for_learning(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        trades = pd.read_csv(path, dtype=str).fillna("")
    except Exception:
        return []
    if trades.empty:
        return []

    rows: list[dict[str, Any]] = []
    for _, row in trades.iterrows():
        action = str(row.get("操作类型", row.get("action", "")))
        if "卖" not in action and "sell" not in action.lower() and "清仓" not in action:
            continue
        symbol = _modular_symbol(row.get("ETF代码", row.get("symbol", "")))
        if not symbol:
            continue
        rows.append(
            {
                "trade_id": f"{symbol}-{row.get('日期', row.get('sell_date', ''))}",
                "symbol": symbol,
                "name": row.get("ETF名称", row.get("name", "")),
                "sell_date": row.get("日期", row.get("sell_date", "")),
                "sell_price": row.get("成交价格", row.get("sell_price", "")),
                "shares": row.get("成交份额", row.get("shares", "")),
                "source_file": str(path),
            }
        )
    return rows


def _first_text(rows: list[dict[str, Any]], key: str, default: str = "") -> str:
    for row in rows:
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return default


def _unique_text(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _modular_symbol(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text else ""


def _modular_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是", "入选", "selected"}
