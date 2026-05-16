from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from backtest.portfolio import FeeConfig, calculate_trade_cost
from strategy.etf_rotation import ETFRotationStrategy, get_rebalance_dates
from signal.trade_policy import (
    QUALITY_LIGHT,
    QUALITY_NORMAL,
    QUALITY_SEVERE,
    QUALITY_UNAVAILABLE,
    apply_data_quality_to_trade_amount,
    build_intraday_execution_plan,
    calculate_intraday_entry_prices,
    calculate_ladder_orders,
    classify_data_quality,
    translate_buy_reason,
)


DEFAULT_POSITION = {"cash": 0.0, "holdings": [], "current_empty": False, "positions": {}}
INSUFFICIENT_DATA_WARNING = "当前有效ETF数量不足，结果仅用于流程测试，不代表策略有效性。"
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
    return f"{float(value):.4f}"


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
                holdings.append(
                    {
                        "symbol": symbol,
                        "shares": shares,
                        "name": str(item.get("name", "")),
                        "cost_price": float(item.get("cost_price", 0) or 0),
                    }
                )
    elif isinstance(data.get("positions"), dict):
        for raw_symbol, item in data.get("positions", {}).items():
            item = item if isinstance(item, dict) else {}
            symbol = _holding_symbol(raw_symbol)
            shares = float(item.get("shares", 0) or 0)
            if symbol and shares > 0:
                holdings.append(
                    {
                        "symbol": symbol,
                        "shares": shares,
                        "name": str(item.get("name", "")),
                        "cost_price": float(item.get("cost_price", 0) or 0),
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
        cost_text = f"，成本价 {cost_price:.4f}" if cost_price > 0 else ""
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


def _symbol_market_frame(strategy: ETFRotationStrategy, symbol: str, signal_date: pd.Timestamp, market_data: dict[str, pd.DataFrame] | None) -> pd.DataFrame:
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
    strategy: ETFRotationStrategy,
    signal_weekday: int,
    rebalance_frequency: str = "weekly",
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
    strategy: ETFRotationStrategy,
    etf_info: dict[str, dict[str, str]],
    signal_date: pd.Timestamp,
    current_position_path: str | Path = "config/current_position.yaml",
    fee_config: FeeConfig | None = None,
    lot_size: int = 100,
    enable_lot_rounding: bool = True,
    observation_cash: float | None = None,
    market_data: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    fee_config = fee_config or FeeConfig()
    signal_date = pd.Timestamp(signal_date)
    current_position = ensure_current_position(current_position_path)
    config_cash = float(current_position.get("cash", 0))
    cash = float(observation_cash) if observation_cash is not None else config_cash
    positions = current_position.get("positions", {}) or {}
    current_holdings = [symbol for symbol, item in positions.items() if float(item.get("shares", 0)) > 0]

    # Target selection must be independent of current holdings and observation cash.
    signal = strategy.generate_target(signal_date, [])
    reason_signal = strategy.generate_target(signal_date, current_holdings) if current_holdings else signal
    target = list(signal["target"])
    ranks = signal["ranks"].copy()
    latest_prices = strategy.close.loc[signal_date]
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
        return {
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
            "hold_plan": [],
            "skipped_buy_plan": [],
            "intraday_execution_plan": [],
            "no_action_reasons": no_action_reasons,
        }

    sell_plan: list[dict[str, Any]] = []
    hold_plan: list[dict[str, Any]] = []
    buy_plan: list[dict[str, Any]] = []
    skipped_buy_plan: list[dict[str, Any]] = []
    current_values: dict[str, float] = {}
    cash_for_buy = cash

    for symbol in current_holdings:
        item = positions.get(symbol, {})
        shares = float(item.get("shares", 0))
        price = latest_prices.get(symbol)
        name = etf_info.get(symbol, {}).get("name", item.get("name") or symbol)
        if symbol not in target:
            if pd.isna(price):
                amount = None
                reason = "目标组合中没有该 ETF，但信号日缺少参考价格，请执行日人工确认后处理"
            else:
                amount = shares * float(price)
                costs = calculate_trade_cost(amount, "sell", fee_config)
                cash_for_buy += amount - costs["total_cost"]
                reason = "当前持仓不在目标组合中"
            reason = reason_signal.get("sell_reasons", {}).get(symbol, reason)
            sell_plan.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": name,
                    "当前持有份额": shares,
                    "建议卖出份额": shares,
                    "卖出原因": reason,
                    "参考估算价格": None if pd.isna(price) else float(price),
                    "预计卖出金额": amount,
                    "实际成交说明": MANUAL_EXECUTION_NOTE,
                }
            )
        elif not pd.isna(price):
            current_values[symbol] = shares * float(price)

    total_value = cash_for_buy + sum(current_values.values())
    target_value = total_value * target_weight if target else 0.0
    for item in target_plan:
        item["目标金额"] = target_value

    for symbol in sorted(set(current_holdings) & set(target)):
        item = positions.get(symbol, {})
        shares = float(item.get("shares", 0))
        price = latest_prices.get(symbol)
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
        price = latest_prices.get(symbol)
        name = etf_info.get(symbol, {}).get("name", symbol)
        rank_context = _rank_context(ranks, symbol)
        quality = classify_data_quality({**rank_context, "close": price})
        quality_level = str(quality["level"])
        current_value = current_values.get(symbol, 0.0)
        adjusted_target_value = target_value * float(quality["amount_multiplier"])
        buy_gap = max(adjusted_target_value - current_value, 0.0)
        if buy_gap <= 0:
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
        market_frame = _symbol_market_frame(strategy, symbol, signal_date, market_data)
        entry_prices = calculate_intraday_entry_prices(market_frame)
        if not entry_prices:
            entry_prices = {
                "第一买入价": round(float(price), 3),
                "第二买入价": round(float(price), 3),
                "第三买入价": round(float(price), 3),
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
                    "交易动作": _quality_trade_action(quality_level),
                    "目标权重": f"{target_weight:.0%}",
                    "目标金额": adjusted_target_value,
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

    return {
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
        "hold_plan": hold_plan,
        "skipped_buy_plan": skipped_buy_plan,
        "intraday_execution_plan": build_intraday_execution_plan(buy_plan, market_data),
        "no_action_reasons": no_action_reasons,
    }


def generate_weekly_signal_text(
    strategy: ETFRotationStrategy,
    equity_curve: pd.DataFrame,
    etf_info: dict[str, dict[str, str]],
    signal_weekday: int,
    output_path: str | Path = "output/weekly_signal.txt",
    current_position_path: str | Path = "config/current_position.yaml",
    fee_config: FeeConfig | None = None,
    lot_size: int = 100,
    enable_lot_rounding: bool = True,
    effective_etf_count: int | None = None,
    min_effective_etf_count: int = 5,
    rebalance_frequency: str = "weekly",
    rebalance_timing: str = "month_end",
    rebalance_day: int | None = None,
    rebalance_day_of_month: int | None = None,
    rebalance_roll: str = "next",
    signal_date: pd.Timestamp | None = None,
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
    )
    positions = plan["current_position"].get("positions", {}) or {}
    target = plan["target"]
    ranks = plan["ranks"]
    latest_prices = strategy.close.loc[latest_signal_date]

    lines: list[str] = []
    lines.append("A股ETF低频轮动系统 - 最新周信号")
    lines.append("=" * 42)
    lines.append(f"信号日期: {latest_signal_date.date()}")
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
