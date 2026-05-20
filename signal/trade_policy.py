from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd


QUALITY_NORMAL = "数据正常"
QUALITY_LIGHT = "轻微缺失"
QUALITY_SEVERE = "严重缺失"
QUALITY_UNAVAILABLE = "数据不可用"

QUALITY_FRONTEND_TEXT = {
    QUALITY_NORMAL: "数据正常，可按计划执行",
    QUALITY_LIGHT: "数据有轻微缺失，建议降低买入金额",
    QUALITY_SEVERE: "数据缺失较多，今日不建议买入",
    QUALITY_UNAVAILABLE: "行情获取失败，已排除",
}

QUALITY_TRADE_ACTION = {
    QUALITY_NORMAL: "允许买入",
    QUALITY_LIGHT: "买入金额 × 50%",
    QUALITY_SEVERE: "禁止新增买入，只允许观察或卖出风控",
    QUALITY_UNAVAILABLE: "不参与排名，不参与买入，不生成买入计划",
}

SELL_TYPE_LABELS = {
    "hold": "暂不卖出",
    "trend_break": "趋势破坏",
    "risk_reduce": "风控减仓",
    "stop_loss": "止损卖出",
    "take_profit": "止盈卖出",
    "rebalance_sell": "轮动调仓卖出",
}

RISK_SELL_TYPES = {"trend_break", "risk_reduce", "stop_loss"}
CURRENT_PRICE_SELL_TYPES = RISK_SELL_TYPES | {"rebalance_sell"}
RISK_TRIGGER_SOURCES = {"60日均线", "120日均线", "止损线", "成本回撤线", "趋势破坏线", "数据异常", "20日均线"}
RISK_TRIGGER_MAX_RATIO = 1.30
RISK_TRIGGER_MIN_RATIO = 0.70


def _text(value: Any) -> str:
    if value in ("", None):
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return str(value)


def normalize_error_message(raw_error: str) -> dict[str, str]:
    raw = _text(raw_error)
    lower = raw.lower()
    if not raw:
        error_type = "历史行情缺失"
        frontend = "该 ETF 暂时没有可用的历史行情，已自动排除，不参与今日信号计算"
        action = "已排除，不参与买入"
    elif any(key in lower for key in ["timeout", "timed out", "connection", "network", "rate", "限流", "too many", "网络"]):
        error_type = "网络不可用或接口限流"
        frontend = "行情接口暂时访问不稳定，已暂缓该 ETF，不参与今日买入"
        action = "暂缓观察，不参与买入"
    elif any(key in lower for key in ["incompatible source fields", "csv 字段缺失", "missing required columns"]):
        error_type = "akshare 接口字段变化或 CSV 字段缺失"
        frontend = "行情字段格式变化或本地 CSV 字段异常，已排除该 ETF"
        action = "检查字段后重试"
    elif any(key in lower for key in ["source returned empty", "empty dataframe", "has no valid rows"]):
        error_type = "接口返回空 DataFrame"
        frontend = "数据源返回空行情，当前无法使用该 ETF"
        action = "等待数据源恢复或人工确认"
    elif any(key in lower for key in ["not found", "unsupported", "不支持", "不存在", "退市", "delist", "no such symbol"]):
        error_type = "ETF 已退市或数据源不支持"
        frontend = "该 ETF 可能已退市、长期停牌或暂不被数据源支持"
        action = "跳过，不计入下载失败"
    elif any(key in lower for key in ["local data", "local csv", "本地缓存", "not found at data"]):
        error_type = "本地缓存不存在"
        frontend = "本地没有可用缓存，且联网更新未成功"
        action = "联网恢复后刷新行情"
    elif any(key in lower for key in ["日期解析失败", "date parse", "invalid date"]):
        error_type = "日期解析失败"
        frontend = "本地或接口行情日期字段无法解析"
        action = "检查 CSV 日期列"
    elif any(key in lower for key in ["all data sources failed", "akshare", "fund_etf_hist"]):
        error_type = "数据源不可用"
        frontend = "该 ETF 暂时无法获取历史行情，已自动排除，不参与今日信号计算"
        action = "已排除，不参与买入"
    elif any(key in lower for key in ["invalid", "代码", "symbol"]):
        error_type = "ETF代码可能无效"
        frontend = "该 ETF 代码可能已失效或暂不被数据源支持，已排除，不参与今日信号计算"
        action = "已排除，不参与买入"
    elif any(key in lower for key in ["too few rows", "missing", "缺少", "empty", "no usable", "history"]):
        error_type = "历史行情缺失"
        frontend = "该 ETF 历史行情不完整，当前只参与观察，不进入今日买入计划"
        action = "数据不足，只参与观察"
    else:
        error_type = "未知错误"
        frontend = "该 ETF 数据状态异常，系统已先排除，不参与今日买入"
        action = "已排除，不参与买入"
    return {
        "错误类型": error_type,
        "前端说明": frontend,
        "处理动作": action,
        "技术详情": raw,
    }


def classify_data_quality(etf_data: Any) -> dict[str, Any]:
    data = dict(etf_data) if isinstance(etf_data, (dict, pd.Series)) else {}
    success_text = _text(data.get("success")).lower()
    status = _text(data.get("status")).lower()
    raw_error = _text(data.get("failure_reason") or data.get("errors") or data.get("filter_reason"))
    close = pd.to_numeric(data.get("close"), errors="coerce")
    quality_passed = data.get("data_quality_passed")
    filter_passed = data.get("filter_passed")
    missing_count = pd.to_numeric(data.get("missing_count"), errors="coerce")
    duplicate_count = pd.to_numeric(data.get("duplicate_count"), errors="coerce")
    completeness = pd.to_numeric(data.get("data_completeness"), errors="coerce")
    warnings = _text(data.get("warnings"))

    if success_text in {"false", "0", "no", "否"} or status == "failed" or (raw_error and pd.isna(close) and "filter" not in raw_error.lower()):
        level = QUALITY_UNAVAILABLE
    elif pd.isna(close) or quality_passed is False:
        level = QUALITY_UNAVAILABLE
    elif filter_passed is False and quality_passed is not True:
        level = QUALITY_SEVERE
    elif (not pd.isna(completeness) and completeness < 0.90) or (not pd.isna(missing_count) and missing_count >= 20):
        level = QUALITY_SEVERE
    elif (
        status == "warning"
        or warnings
        or (not pd.isna(completeness) and completeness < 0.99)
        or (not pd.isna(missing_count) and missing_count > 0)
        or (not pd.isna(duplicate_count) and duplicate_count > 0)
    ):
        level = QUALITY_LIGHT
    else:
        level = QUALITY_NORMAL

    return {
        "level": level,
        "数据质量": level,
        "前端说明": QUALITY_FRONTEND_TEXT[level],
        "交易动作": QUALITY_TRADE_ACTION[level],
        "amount_multiplier": 0.5 if level == QUALITY_LIGHT else 1.0 if level == QUALITY_NORMAL else 0.0,
        "allow_buy": level in {QUALITY_NORMAL, QUALITY_LIGHT},
        "技术详情": raw_error,
    }


def apply_data_quality_to_trade_amount(amount: float, quality_level: str) -> float:
    if quality_level == QUALITY_LIGHT:
        return max(float(amount), 0.0) * 0.5
    if quality_level in {QUALITY_SEVERE, QUALITY_UNAVAILABLE}:
        return 0.0
    return max(float(amount), 0.0)


def translate_strategy_status(status_code: str) -> str:
    return {
        "recommended_for_observation": "暂不买入，只观察",
        "research_observation_candidate": "可买入候选，但等待价格确认",
        "research_only": "不参与实盘，只作研究对照",
        "defensive_only": "防守模式参考，不作为主动买入信号",
        "rejected": "今日不买入",
    }.get(status_code, status_code or "未知状态")


def translate_buy_reason(raw_reason: str, context: dict[str, Any] | None = None) -> str:
    context = context or {}
    name = _text(context.get("name")) or _text(context.get("ETF名称")) or "该 ETF"
    rank = context.get("rank")
    momentum_period = int(context.get("momentum_period") or 60)
    ma_period = int(context.get("ma_period") or 60)
    quality_level = _text(context.get("quality_level") or context.get("数据质量") or QUALITY_NORMAL)
    in_plan = bool(context.get("in_actual_buy_plan", True))
    quality_text = {
        QUALITY_NORMAL: "数据质量正常，不需要调整买入金额",
        QUALITY_LIGHT: "但数据有轻微缺失，今日买入金额已减半",
        QUALITY_SEVERE: "但数据缺失较多，禁止新增买入",
        QUALITY_UNAVAILABLE: "但行情不可用，已排除",
    }.get(quality_level, "数据质量状态已纳入买入金额判断")
    plan_text = "已进入实际买入计划" if in_plan else "未进入实际买入计划"

    if rank not in ("", None) and not pd.isna(rank):
        return (
            f"{name}近 {momentum_period} 个交易日动量排名第 {int(rank)}，"
            f"且最新收盘价站上 {ma_period} 日均线，趋势条件通过，{quality_text}，因此{plan_text}。"
        )
    raw = _text(raw_reason)
    if "fallback" in raw.lower():
        return f"{name}由策略备用规则选中，趋势和数据条件已重新检查，{quality_text}，因此{plan_text}。"
    return f"{name}近 {momentum_period} 个交易日表现靠前，趋势条件已通过，{quality_text}，因此{plan_text}。"


def calculate_atr(df: pd.DataFrame, window: int = 14) -> float:
    if df.empty or not {"high", "low", "close"}.issubset(df.columns):
        return float("nan")
    frame = df.copy()
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    previous_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.dropna().tail(window).mean()
    return float(atr) if not pd.isna(atr) else float("nan")


def _round_tick(value: float, tick_size: float = 0.001) -> float:
    if pd.isna(value) or tick_size <= 0:
        return float("nan")
    return round(round(float(value) / tick_size) * tick_size, 3)


def _force_descending_prices(prices: list[float], tick_size: float = 0.001) -> list[float]:
    adjusted = [_round_tick(price, tick_size) for price in prices]
    for idx in range(1, len(adjusted)):
        max_allowed = _round_tick(adjusted[idx - 1] - tick_size, tick_size)
        if adjusted[idx] >= adjusted[idx - 1]:
            adjusted[idx] = max_allowed
    return [max(price, tick_size) for price in adjusted]


def _force_ascending_prices(prices: list[float], tick_size: float = 0.001) -> list[float]:
    adjusted = [_round_tick(price, tick_size) for price in prices]
    for idx in range(1, len(adjusted)):
        min_allowed = _round_tick(adjusted[idx - 1] + tick_size, tick_size)
        if adjusted[idx] <= adjusted[idx - 1]:
            adjusted[idx] = min_allowed
    return adjusted


def build_buy_price_ladder(
    close_price: float,
    atr: float | None = None,
    volatility: float | None = None,
    ma20: float | None = None,
    tick_size: float = 0.001,
) -> dict[str, Any]:
    close = float(close_price)
    if close <= 0 or pd.isna(close):
        return {}
    volatility_value = float(volatility) if volatility is not None and not pd.isna(volatility) else float("nan")
    atr_value = float(atr) if atr is not None and not pd.isna(atr) else float("nan")
    if atr_value > 0:
        step = min(max(atr_value / close, 0.015), 0.04)
        source = "ATR 回撤档位"
    elif volatility_value > 0:
        step = min(max(volatility_value / close, 0.015), 0.04)
        source = "波动率回撤档位"
    else:
        step = 0.015
        source = "兜底 1.5% 回撤档位"
    prices = _force_descending_prices([close, close * (1 - step), close * (1 - 2 * step)], tick_size)
    ma20_text = "" if ma20 is None or pd.isna(ma20) else f"；若跌破 20 日均线 {float(ma20):.3f}，暂停新增买入"
    return {
        "first_buy_price": prices[0],
        "second_buy_price": prices[1],
        "third_buy_price": prices[2],
        "first_trigger_text": f"第一档接近最新完整交易日收盘价，作为试探仓价格{ma20_text}。",
        "second_trigger_text": f"第二档较第一档回撤约 {step * 100:.1f}%，只在趋势未破坏时执行。",
        "third_trigger_text": f"第三档较第一档回撤约 {step * 2 * 100:.1f}%，属于深回调加仓档。",
        "ladder_source": source,
    }


def build_sell_price_ladder(
    current_price: float,
    atr: float | None = None,
    volatility: float | None = None,
    tick_size: float = 0.001,
) -> dict[str, Any]:
    price = float(current_price)
    if price <= 0 or pd.isna(price):
        return {}
    atr_value = float(atr) if atr is not None and not pd.isna(atr) else float("nan")
    volatility_value = float(volatility) if volatility is not None and not pd.isna(volatility) else float("nan")
    if atr_value > 0:
        step = min(max(atr_value / price, 0.01), 0.03)
    elif volatility_value > 0:
        step = min(max(volatility_value / price, 0.01), 0.03)
    else:
        step = 0.01
    prices = _force_ascending_prices([price, price * (1 + step), price * (1 + 2 * step)], tick_size)
    return {
        "first_sell_price": prices[0],
        "second_sell_price": prices[1],
        "third_sell_price": prices[2],
        "first_sell_trigger_text": "第一档按当前参考价或最新收盘价给出，适合先执行风险控制或试探卖出。",
        "second_sell_trigger_text": f"第二档较第一档上浮约 {step * 100:.1f}%，用于分批兑现。",
        "third_sell_trigger_text": f"第三档较第一档上浮约 {step * 2 * 100:.1f}%，用于更高价继续兑现。",
    }


def build_risk_sell_price_ladder(current_price: float, tick_size: float = 0.001) -> dict[str, Any]:
    price = float(current_price)
    if price <= 0 or pd.isna(price):
        return {}
    prices = _force_descending_prices([price, price * 0.995, price * 0.990], tick_size)
    return {
        "first_sell_price": prices[0],
        "second_sell_price": prices[1],
        "third_sell_price": prices[2],
    }


def _numeric_or_nan(value: Any) -> float:
    if value in ("", None):
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _append_warning(row: dict[str, Any], warning: str) -> None:
    existing = _text(row.get("warning") or row.get("validation_warning"))
    warnings = [item for item in [existing, warning] if item]
    merged = "；".join(dict.fromkeys(warnings))
    row["warning"] = merged
    row["validation_warning"] = merged
    risk_tip = _text(row.get("风险提示") or row.get("risk_note"))
    row["风险提示"] = "；".join(dict.fromkeys([item for item in [risk_tip, warning] if item]))
    row["risk_note"] = row["风险提示"]


def validate_risk_trigger_price(row: dict[str, Any]) -> dict[str, Any]:
    """Validate risk trigger price and hide abnormal trigger lines from normal display."""
    out = dict(row)
    sell_type = _text(out.get("sell_type") or out.get("卖出类型"))
    current_price = _numeric_or_nan(out.get("current_price", out.get("当前价格")))
    trigger = float("nan")
    for trigger_key in ["risk_trigger_price", "风控触发价", "raw_risk_trigger_price", "MA60", "风控卖出价"]:
        trigger = _numeric_or_nan(out.get(trigger_key))
        if not pd.isna(trigger) and trigger > 0:
            break
    source = _text(out.get("risk_trigger_source") or out.get("触发价来源") or out.get("风控触发来源"))
    if not source:
        ma60_value = _numeric_or_nan(out.get("MA60"))
        ma120_value = _numeric_or_nan(out.get("MA120"))
        stop_value = _numeric_or_nan(out.get("stop_loss_trigger_price") or out.get("成本止损价"))
        if not pd.isna(trigger) and not pd.isna(ma60_value) and abs(trigger - ma60_value) < 0.0005:
            source = "60日均线"
        elif not pd.isna(trigger) and not pd.isna(ma120_value) and abs(trigger - ma120_value) < 0.0005:
            source = "120日均线"
        elif not pd.isna(trigger) and not pd.isna(stop_value) and abs(trigger - stop_value) < 0.0005:
            source = "成本回撤线"
    if source not in RISK_TRIGGER_SOURCES:
        source = "数据异常" if not source else source
    out["risk_trigger_source"] = source
    out["触发价来源"] = source

    if pd.isna(current_price) or current_price <= 0:
        out["raw_risk_trigger_price"] = None if pd.isna(trigger) else trigger
        out["risk_trigger_price"] = None
        out["风控触发价"] = None
        out["risk_trigger_display"] = "-"
        out["risk_trigger_ratio_to_current"] = None
        out["risk_trigger_warning"] = "当前价格缺失，不能生成风控价。"
        out["数据质量"] = "当前价格缺失，不能生成风控价"
        _append_warning(out, out["risk_trigger_warning"])
        return out

    if pd.isna(trigger) or trigger <= 0:
        out["risk_trigger_price"] = None
        out["风控触发价"] = None
        out["risk_trigger_display"] = "-"
        out["risk_trigger_ratio_to_current"] = None
        out["risk_trigger_warning"] = "风控触发价缺失。"
        return out

    ratio = trigger / current_price
    out["risk_trigger_ratio_to_current"] = ratio
    out["raw_risk_trigger_price"] = out.get("raw_risk_trigger_price", trigger)
    out["raw_risk_trigger_source"] = out.get("raw_risk_trigger_source", source)

    basis_consistent = bool(out.get("price_basis_consistent", True))
    basis_warning = _text(out.get("price_basis_warning"))
    abnormal_high = ratio > RISK_TRIGGER_MAX_RATIO
    abnormal_basis = not basis_consistent
    if abnormal_high or abnormal_basis:
        messages = []
        if abnormal_high:
            messages.append("风控触发价与当前价偏离过大，疑似数据或字段错位，请人工复核。")
        if abnormal_basis and basis_warning:
            messages.append(basis_warning)
        warning = "；".join(dict.fromkeys(messages)) or "风控触发价异常，已隐藏。"
        out["risk_trigger_price"] = None
        out["风控触发价"] = None
        out["risk_trigger_display"] = "异常，已隐藏"
        out["risk_trigger_source"] = "数据异常"
        out["触发价来源"] = "数据异常"
        out["risk_trigger_warning"] = warning
        out["数据质量"] = "价格口径异常" if abnormal_basis else "风控触发价异常"
        _append_warning(out, warning)
        return out

    out["risk_trigger_price"] = round(trigger, 3)
    out["风控触发价"] = round(trigger, 3)
    out["risk_trigger_display"] = round(trigger, 3)
    if RISK_TRIGGER_MIN_RATIO <= ratio <= RISK_TRIGGER_MAX_RATIO:
        if trigger > current_price:
            out["risk_trigger_warning"] = "当前价已跌破风控触发线。"
        elif trigger < current_price and sell_type == "trend_break":
            out["risk_trigger_warning"] = "当前价尚未跌破该风控线，不应仅按趋势破坏卖出。"
            _append_warning(out, out["risk_trigger_warning"])
        else:
            out["risk_trigger_warning"] = ""
    else:
        out["risk_trigger_warning"] = "风控触发价与当前价偏离较大，请人工复核。"
        _append_warning(out, out["risk_trigger_warning"])
    return out


def validate_sell_prices(row: dict[str, Any], tick_size: float = 0.001) -> dict[str, Any]:
    """Validate that executable sell prices are based on this row's current price."""
    out = validate_risk_trigger_price(row)
    sell_type = _text(out.get("sell_type") or out.get("卖出类型"))
    current_price = _numeric_or_nan(out.get("current_price", out.get("当前价格")))

    out["卖出类型"] = SELL_TYPE_LABELS.get(sell_type, sell_type or "暂不卖出")
    out["suggested_sell_shares"] = float(out.get("suggested_sell_shares") or out.get("建议卖出份额") or 0)
    out["sell_ratio"] = float(out.get("sell_ratio") or 0)
    if sell_type in RISK_SELL_TYPES and out.get("risk_trigger_display") == "异常，已隐藏":
        current_shares = _numeric_or_nan(out.get("current_shares", out.get("持有份额")))
        target_shares = _numeric_or_nan(out.get("target_shares", out.get("目标份额")))
        if not pd.isna(current_shares) and not pd.isna(target_shares) and current_shares - target_shares >= 100:
            suggested = math.floor((current_shares - target_shares) / 100) * 100
            sell_type = "rebalance_sell"
            out["sell_type"] = sell_type
            out["suggested_sell_shares"] = float(suggested)
            out["sell_ratio"] = float(suggested) / float(current_shares) if current_shares > 0 else 0.0
            out["execution_note"] = "风控触发价异常，未按趋势破坏生成卖出；当前卖出动作仅来自轮动调仓。"
            out["执行说明"] = out["execution_note"]
        else:
            sell_type = "hold"
            out["sell_type"] = sell_type
            out["suggested_sell_shares"] = 0.0
            out["sell_ratio"] = 0.0
            out["execution_note"] = "风控触发价异常，已跳过趋势破坏卖出。"
            out["执行说明"] = out["execution_note"]
        out["卖出类型"] = SELL_TYPE_LABELS[sell_type]

    if pd.isna(current_price) or current_price <= 0:
        for key in ["first_sell_price", "second_sell_price", "third_sell_price", "risk_limit_price", "第一卖出价", "第二卖出价", "第三卖出价", "风控挂单价"]:
            out[key] = None
        out["suggested_sell_shares"] = 0.0
        out["建议卖出份额"] = 0.0
        out["sell_ratio"] = 0.0
        out["建议卖出比例"] = 0.0
        out["execution_note"] = "价格异常，已跳过该 ETF 卖出计划。"
        out["执行说明"] = "价格异常，已跳过该 ETF 卖出计划。"
        _append_warning(out, "价格缺失，不能生成卖出计划。")
        return out

    if sell_type == "hold":
        for key in ["first_sell_price", "second_sell_price", "third_sell_price", "risk_limit_price", "第一卖出价", "第二卖出价", "第三卖出价", "风控挂单价"]:
            out[key] = None
        out["suggested_sell_shares"] = 0.0
        out["建议卖出份额"] = 0.0
        out["sell_ratio"] = 0.0
        out["建议卖出比例"] = 0.0
        out.setdefault("execution_note", "暂不卖出，仅观察止盈价和风控触发线。")
        out.setdefault("执行说明", out["execution_note"])
        return out

    if sell_type in CURRENT_PRICE_SELL_TYPES:
        risk_limit_price = _numeric_or_nan(out.get("risk_limit_price", out.get("风控挂单价")))
        if pd.isna(risk_limit_price) or risk_limit_price <= 0:
            risk_limit_price = current_price
        if risk_limit_price > current_price * 1.02:
            risk_limit_price = current_price
            _append_warning(out, "风控挂单价异常，已按当前参考价修正。")
        ladder = build_risk_sell_price_ladder(risk_limit_price, tick_size=tick_size)
    elif sell_type == "take_profit":
        ladder = {
            "first_sell_price": _numeric_or_nan(out.get("first_take_profit_price", out.get("第一止盈价"))),
            "second_sell_price": _numeric_or_nan(out.get("second_take_profit_price", out.get("第二止盈价"))),
            "third_sell_price": _numeric_or_nan(out.get("third_take_profit_price", out.get("第三止盈价"))),
        }
        ladder = {key: (_round_tick(value, tick_size) if not pd.isna(value) and value > 0 else None) for key, value in ladder.items()}
        out["risk_limit_price"] = None
        out["风控挂单价"] = None
    else:
        ladder = build_risk_sell_price_ladder(current_price, tick_size=tick_size)

    out["first_sell_price"] = ladder.get("first_sell_price")
    out["second_sell_price"] = ladder.get("second_sell_price")
    out["third_sell_price"] = ladder.get("third_sell_price")
    out["第一卖出价"] = out["first_sell_price"]
    out["第二卖出价"] = out["second_sell_price"]
    out["第三卖出价"] = out["third_sell_price"]
    if sell_type in CURRENT_PRICE_SELL_TYPES:
        out["risk_limit_price"] = out["first_sell_price"]
        out["风控挂单价"] = out["first_sell_price"]
    out["建议卖出份额"] = out["suggested_sell_shares"]
    out["建议卖出比例"] = out["sell_ratio"]
    return out


def calculate_position_pnl(shares: float, average_buy_price: float, current_price: float) -> dict[str, float]:
    position_cost = float(shares) * float(average_buy_price)
    market_value = float(shares) * float(current_price)
    pnl = market_value - position_cost
    pnl_rate = pnl / position_cost if position_cost > 0 else 0.0
    return {
        "持仓成本": position_cost,
        "当前市值": market_value,
        "浮动盈亏": pnl,
        "浮动盈亏率": pnl_rate,
    }


def _close_volatility_proxy(df: pd.DataFrame, window: int = 14) -> float:
    close = pd.to_numeric(df.get("close"), errors="coerce").dropna()
    if len(close) < 2:
        return float("nan")
    return float(close.pct_change().abs().dropna().tail(window).mean() * close.iloc[-1])


def calculate_intraday_entry_prices(df: pd.DataFrame, realtime_price: float | None = None) -> dict[str, Any]:
    if df.empty or "close" not in df.columns:
        return {}
    frame = df.copy().sort_index()
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if close.empty:
        return {}
    latest_close = float(close.iloc[-1])
    atr14 = calculate_atr(frame, 14)
    atr_source = "ATR14"
    if pd.isna(atr14) or atr14 <= 0:
        atr14 = _close_volatility_proxy(frame, 14)
        atr_source = "收盘波动率估算"
    atr_ratio = 0.006 if pd.isna(atr14) or latest_close <= 0 else float(atr14 / latest_close)
    vol_band = min(max(atr_ratio, 0.006), 0.05)
    reference_price = float(realtime_price) if realtime_price and realtime_price > 0 else latest_close
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else float("nan")
    ladder = build_buy_price_ladder(reference_price, atr=atr14, volatility=atr14, ma20=ma60)
    if not ladder:
        return {}
    prices = {
        "第一买入价": ladder["first_buy_price"],
        "第二买入价": ladder["second_buy_price"],
        "第三买入价": ladder["third_buy_price"],
        "第一档触发说明": ladder["first_trigger_text"],
        "第二档触发说明": ladder["second_trigger_text"],
        "第三档触发说明": ladder["third_trigger_text"],
    }
    return {
        **prices,
        "reference_price": round(reference_price, 3),
        "reference_source": "当前实时价" if realtime_price else "最新完整交易日收盘价",
        "atr14": atr14,
        "atr_ratio": atr_ratio,
        "vol_band": vol_band,
        "ma60": ma60,
        "atr_source": atr_source,
        "失效条件": "若实时价或最新收盘价跌破 60 日均线、今日跌幅超过 2 倍 ATR 比例、数据质量转差，或主策略信号变为不买入，今日三档买入价全部取消，不再新增买入。",
    }


def _latest_close_and_vol(df: pd.DataFrame, realtime_price: float | None = None) -> tuple[float, float, float, float]:
    if df.empty or "close" not in df.columns:
        return float("nan"), float("nan"), 0.006, float("nan")
    frame = df.copy().sort_index()
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if close.empty:
        return float("nan"), float("nan"), 0.006, float("nan")
    latest_close = float(close.iloc[-1])
    current_price = float(realtime_price) if realtime_price and realtime_price > 0 else latest_close
    atr14 = calculate_atr(frame, 14)
    if pd.isna(atr14) or atr14 <= 0:
        atr14 = _close_volatility_proxy(frame, 14)
    atr_ratio = 0.006 if pd.isna(atr14) or latest_close <= 0 else float(atr14 / latest_close)
    vol_band = min(max(atr_ratio, 0.006), 0.05)
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else float("nan")
    return current_price, atr14, vol_band, ma60


def _rolling_close_mean(df: pd.DataFrame, window: int) -> float:
    if df.empty or "close" not in df.columns:
        return float("nan")
    close = pd.to_numeric(df.get("close"), errors="coerce").dropna()
    if len(close) < window:
        return float("nan")
    return float(close.tail(window).mean())


def _latest_close(df: pd.DataFrame) -> float:
    if df.empty or "close" not in df.columns:
        return float("nan")
    close = pd.to_numeric(df.get("close"), errors="coerce").dropna()
    return float(close.iloc[-1]) if not close.empty else float("nan")


def _previous_close(df: pd.DataFrame) -> float:
    if df.empty or "close" not in df.columns:
        return float("nan")
    close = pd.to_numeric(df.get("close"), errors="coerce").dropna()
    return float(close.iloc[-2]) if len(close) >= 2 else float("nan")


def _price_basis_diagnostics(df: pd.DataFrame, current_price: float) -> dict[str, Any]:
    latest = _latest_close(df)
    previous = _previous_close(df)
    current = _numeric_or_nan(current_price)
    latest_ratio = current / latest if not pd.isna(current) and not pd.isna(latest) and latest > 0 else float("nan")
    one_day_ratio = latest / previous if not pd.isna(latest) and not pd.isna(previous) and previous > 0 else float("nan")
    warnings: list[str] = []
    consistent = True
    if not pd.isna(latest_ratio) and not 0.98 <= latest_ratio <= 1.02:
        consistent = False
        warnings.append("当前参考价与历史 close 最新价口径不一致。")
    if not pd.isna(one_day_ratio) and not 0.70 <= one_day_ratio <= 1.30:
        consistent = False
        warnings.append("历史 close 序列疑似存在复权口径突变。")
    return {
        "price_basis_consistent": consistent,
        "price_basis_warning": "；".join(warnings),
        "latest_close": latest,
        "previous_close": previous,
        "current_to_latest_close_ratio": latest_ratio,
        "latest_to_previous_close_ratio": one_day_ratio,
    }


def _select_trend_trigger(ma20: float, ma60: float, ma120: float) -> tuple[float, str]:
    if not pd.isna(ma60) and ma60 > 0:
        return ma60, "60日均线"
    if not pd.isna(ma20) and ma20 > 0:
        return ma20, "20日均线"
    if not pd.isna(ma120) and ma120 > 0:
        return ma120, "120日均线"
    return float("nan"), "数据异常"


def calculate_take_profit_prices(
    average_buy_price: float,
    df: pd.DataFrame,
    realtime_price: float | None = None,
) -> dict[str, Any]:
    _, atr14, vol_band, _ = _latest_close_and_vol(df, realtime_price)
    average_price = float(average_buy_price)
    return {
        "第一止盈价": round(average_price * (1 + 1.0 * vol_band), 3),
        "第二止盈价": round(average_price * (1 + 1.8 * vol_band), 3),
        "第三止盈价": round(average_price * (1 + 2.8 * vol_band), 3),
        "atr14": atr14,
        "vol_band": vol_band,
    }


def calculate_risk_exit_price(
    average_buy_price: float,
    df: pd.DataFrame,
    realtime_price: float | None = None,
) -> dict[str, Any]:
    current_price, atr14, _, ma60 = _latest_close_and_vol(df, realtime_price)
    ma20 = _rolling_close_mean(df, 20)
    ma120 = _rolling_close_mean(df, 120)
    cost_stop = float(average_buy_price) * 0.97 if float(average_buy_price) > 0 else float("nan")
    atr_stop = current_price - 1.5 * atr14 if not pd.isna(current_price) and not pd.isna(atr14) else float("nan")
    risk_trigger_price, risk_trigger_source = _select_trend_trigger(ma20, ma60, ma120)
    risk_limit_price = current_price if not pd.isna(current_price) and current_price > 0 else float("nan")
    ratio = risk_trigger_price / current_price if not pd.isna(risk_trigger_price) and not pd.isna(current_price) and current_price > 0 else float("nan")
    basis = _price_basis_diagnostics(df, current_price)
    return {
        "成本止损价": cost_stop,
        "MA20": ma20,
        "MA60": ma60,
        "MA120": ma120,
        "ATR止损价": atr_stop,
        "stop_loss_trigger_price": round(cost_stop, 3) if not pd.isna(cost_stop) else float("nan"),
        "stop_loss_trigger_source": "成本回撤线",
        "risk_trigger_price": round(risk_trigger_price, 3) if not pd.isna(risk_trigger_price) else float("nan"),
        "风控触发价": round(risk_trigger_price, 3) if not pd.isna(risk_trigger_price) else float("nan"),
        "risk_trigger_source": risk_trigger_source,
        "risk_trigger_ratio_to_current": ratio,
        "risk_limit_price": round(risk_limit_price, 3) if not pd.isna(risk_limit_price) else float("nan"),
        "风控挂单价": round(risk_limit_price, 3) if not pd.isna(risk_limit_price) else float("nan"),
        "当前价格": current_price,
        "atr14": atr14,
        **basis,
    }


def _round_lot_shares(shares: float, ratio: float, lot_size: int) -> float:
    return float(math.floor(float(shares) * float(ratio) / lot_size) * lot_size)


def _sell_share_text(shares: float) -> str | float:
    if shares < 100:
        return "持仓份额不足 100 份，跳过该档。"
    return float(shares)


def translate_sell_action(
    current_price: float,
    ma60: float,
    pnl_rate: float,
    first_take_profit_price: float,
    in_target: bool,
) -> str:
    below_ma60 = not pd.isna(ma60) and current_price < ma60
    if below_ma60 and pnl_rate <= -0.03:
        return "风控清仓"
    if below_ma60:
        return "风控减仓"
    if current_price >= first_take_profit_price and pnl_rate > 0:
        return "止盈卖出"
    if not in_target and pnl_rate < 0:
        return "风控减仓"
    if not in_target and pnl_rate >= 0:
        return "止盈卖出"
    return "暂不卖出"


def translate_sell_reason(
    action: str,
    current_price: float,
    ma60: float,
    pnl_rate: float,
    in_target: bool,
) -> tuple[str, str]:
    below_ma60 = not pd.isna(ma60) and current_price < ma60
    if action == "风控清仓":
        return (
            "当前价格已跌破 60 日均线，趋势转弱，且浮动亏损超过 3%。建议清仓，先把风险降下来，再等待下一次明确买入信号。",
            "已同时触发趋势风控和成本止损，继续持有可能放大亏损。",
        )
    if action == "风控减仓":
        if below_ma60:
            return (
                "当前价格已跌破 60 日均线，趋势转弱。建议先卖出 50% 降低风险；如果同时跌破成本止损线，建议清仓。",
                "趋势已经转弱，后续若无法重新站回 60 日均线，应继续降低仓位。",
            )
        return (
            "主策略今日没有把该 ETF 纳入买入候选，且当前仍处于浮亏。建议降低仓位或分批卖出，避免弱势持仓继续占用资金。",
            "该 ETF 已不在今日买入候选中，需要重点观察风控触发价。",
        )
    if action == "止盈卖出":
        if in_target:
            return (
                "当前价格仍在 60 日均线上方，趋势未破坏。若价格上涨到第一止盈价附近，可先卖出 30% 锁定利润；若继续上涨，再按第二、第三止盈价分批卖出。",
                "止盈后仍需观察趋势，若跌破风控触发价，应优先执行风控。",
            )
        return (
            "该 ETF 已不在今日买入候选中，但当前仍有盈利。可以按三档止盈价分批兑现利润，降低单一持仓占用。",
            "主策略信号已经转弱，盈利持仓不宜继续无条件加仓。",
        )
    if pnl_rate < 0:
        return (
            "当前价格仍在 60 日均线上方，但持仓暂时浮亏。建议继续观察，若价格跌破风控触发价，则先减仓控制风险。",
            "尚未触发趋势风控，但亏损持仓要避免扩大。",
        )
    return (
        "当前价格未达到止盈价，也未触发风控线，建议继续持有观察。",
        "未触发卖出条件，可按风控触发价设置人工提醒。",
    )


def _floor_lot(value: float, lot_size: int) -> float:
    if value <= 0:
        return 0.0
    return float(math.floor(float(value) / lot_size) * lot_size)


def _candidate_sell_shares(shares: float, ratio: float, lot_size: int) -> float:
    rounded = _floor_lot(float(shares) * float(ratio), lot_size)
    if rounded < lot_size and shares >= lot_size and ratio > 0:
        return float(lot_size)
    return min(rounded, _floor_lot(shares, lot_size))


def build_sell_decisions(
    positions: dict[str, Any],
    market_frames: dict[str, pd.DataFrame],
    target_shares: dict[str, float] | None = None,
    target_symbols: set[str] | list[str] | tuple[str, ...] = (),
    lot_size: int = 100,
    quote_map: dict[str, dict[str, Any]] | None = None,
    signal_date: str | None = None,
    execution_date: str | None = None,
) -> list[dict[str, Any]]:
    quote_map = quote_map or {}
    target_shares = {str(symbol).zfill(6): float(value or 0) for symbol, value in (target_shares or {}).items()}
    target_set = {str(symbol).zfill(6) for symbol in target_symbols}
    rows: list[dict[str, Any]] = []
    for raw_symbol, item in positions.items():
        symbol = str(raw_symbol).zfill(6)
        shares = float(item.get("shares", 0) or 0)
        if shares <= 0:
            continue
        target_share = float(target_shares.get(symbol, shares if symbol in target_set else 0.0))
        frame = market_frames.get(symbol, pd.DataFrame())
        quote = quote_map.get(symbol, {})
        quote_status = str(quote.get("price_status") or quote.get("status") or "")
        quote_actionable = bool(quote.get("price_actionable", not quote_status))
        daily_history_valid = bool(quote.get("daily_history_valid", True))
        current_price, _, _, ma60 = _latest_close_and_vol(frame)
        if quote and quote_actionable:
            current_price = float(quote.get("latest_price") or current_price)
        if quote and quote_actionable and not daily_history_valid:
            average_buy_price = float(item.get("average_buy_price") or item.get("cost_price") or current_price or 0)
            pnl = calculate_position_pnl(shares, average_buy_price, current_price) if average_buy_price > 0 and current_price > 0 else {"持仓成本": None, "当前市值": None, "浮动盈亏": None, "浮动盈亏率": None}
            rows.append(
                validate_sell_prices(
                    {
                    "ETF代码": symbol,
                    "ETF名称": item.get("name") or symbol,
                    "symbol": symbol,
                    "name": item.get("name") or symbol,
                    "持有份额": shares,
                    "current_shares": shares,
                    "平均买入价": round(average_buy_price, 3) if average_buy_price > 0 else None,
                    "当前价格": round(float(current_price), 3) if current_price > 0 else None,
                    "current_price": round(float(current_price), 3) if current_price > 0 else None,
                    "报价日期": quote.get("quote_date", ""),
                    "报价时间": quote.get("quote_time", ""),
                    "价格来源": quote.get("source", ""),
                    "价格状态": quote_status,
                    "浮动盈亏率": pnl["浮动盈亏率"],
                    "交易动作": "价格异常，等待确认",
                    "sell_type": "hold",
                    "卖出类型": "hold",
                    "sell_ratio": 0.0,
                    "target_shares": target_share,
                    "目标份额": target_share,
                    "first_sell_price": None,
                    "second_sell_price": None,
                    "third_sell_price": None,
                    "第一卖出价": None,
                    "第二卖出价": None,
                    "第三卖出价": None,
                    "第一止盈价": None,
                    "第二止盈价": None,
                    "第三止盈价": None,
                    "风控触发价": None,
                    "risk_trigger_price": None,
                    "risk_limit_price": None,
                    "风控挂单价": None,
                    "第一档卖出份额": "日线价格异常，跳过该档。",
                    "第二档卖出份额": "日线价格异常，跳过该档。",
                    "第三档卖出份额": "日线价格异常，跳过该档。",
                    "suggested_sell_shares": 0.0,
                    "建议卖出份额": 0.0,
                    "触发说明": "当前实时价格可显示，但本地日线价格与实时昨收明显冲突，系统暂停三档止盈价和风控挂单价计算。",
                    "失效条件": "修复或刷新日线数据后重新生成计划。",
                    "卖出说明": "行情源返回的当前价可参考，但历史日线与实时行情不一致，系统已暂停该 ETF 的交易计划，等待人工确认。",
                    "风险提示": quote.get("daily_history_message") or "本地日线可能存在复权或缓存错误。",
                    "是否全卖": "否",
                    "priority": 0,
                    "explanation": "价格异常，未生成卖出决策。",
                    "signal_date": signal_date or "",
                    "execution_date": execution_date or "",
                    "MA60": None,
                    "持仓成本": pnl["持仓成本"],
                    "当前市值": pnl["当前市值"],
                    "浮动盈亏": pnl["浮动盈亏"],
                }
                )
            )
            continue
        elif quote and not quote_actionable:
            average_buy_price = float(item.get("average_buy_price") or item.get("cost_price") or 0)
            rows.append(
                validate_sell_prices(
                    {
                    "ETF代码": symbol,
                    "ETF名称": item.get("name") or symbol,
                    "symbol": symbol,
                    "name": item.get("name") or symbol,
                    "持有份额": shares,
                    "current_shares": shares,
                    "平均买入价": round(average_buy_price, 3) if average_buy_price > 0 else None,
                    "当前价格": None,
                    "current_price": None,
                    "报价日期": quote.get("quote_date", ""),
                    "报价时间": quote.get("quote_time", ""),
                    "价格来源": quote.get("source", ""),
                    "价格状态": quote_status or "数据不可用",
                    "浮动盈亏率": None,
                    "交易动作": "价格异常，等待确认",
                    "sell_type": "hold",
                    "卖出类型": "hold",
                    "sell_ratio": 0.0,
                    "target_shares": target_share,
                    "目标份额": target_share,
                    "first_sell_price": None,
                    "second_sell_price": None,
                    "third_sell_price": None,
                    "第一卖出价": None,
                    "第二卖出价": None,
                    "第三卖出价": None,
                    "第一止盈价": None,
                    "第二止盈价": None,
                    "第三止盈价": None,
                    "风控触发价": ma60 if not pd.isna(ma60) else None,
                    "risk_trigger_price": ma60 if not pd.isna(ma60) else None,
                    "risk_limit_price": None,
                    "风控挂单价": None,
                    "第一档卖出份额": "行情非今日数据，跳过该档。",
                    "第二档卖出份额": "行情非今日数据，跳过该档。",
                    "第三档卖出份额": "行情非今日数据，跳过该档。",
                    "suggested_sell_shares": 0.0,
                    "建议卖出份额": 0.0,
                    "触发说明": "行情源返回价格异常或不是今日行情，系统已暂停该 ETF 的卖出执行计划，等待人工确认。",
                    "失效条件": "刷新到有效今日行情后重新生成计划。",
                    "卖出说明": "行情源返回价格异常，系统已停用该价格，等待人工确认。",
                    "风险提示": "当前 ETF 不参与盈亏和卖出价计算，避免用错误价格触发交易。",
                    "是否全卖": "否",
                    "priority": 0,
                    "explanation": "行情不可用，未生成卖出决策。",
                    "signal_date": signal_date or "",
                    "execution_date": execution_date or "",
                    "MA60": ma60,
                    "持仓成本": shares * average_buy_price if average_buy_price > 0 else None,
                    "当前市值": None,
                    "浮动盈亏": None,
                }
                )
            )
            continue
        if pd.isna(current_price) or current_price <= 0:
            average_buy_price = float(item.get("average_buy_price") or item.get("cost_price") or 0)
            rows.append(
                validate_sell_prices(
                    {
                        "symbol": symbol,
                        "name": item.get("name") or symbol,
                        "ETF代码": symbol,
                        "ETF名称": item.get("name") or symbol,
                        "current_shares": shares,
                        "持有份额": shares,
                        "target_shares": target_share,
                        "目标份额": target_share,
                        "平均买入价": round(average_buy_price, 3) if average_buy_price > 0 else None,
                        "current_price": None,
                        "当前价格": None,
                        "交易动作": "价格异常，等待确认",
                        "sell_type": "hold",
                        "卖出类型": "hold",
                        "sell_ratio": 0.0,
                        "suggested_sell_shares": 0.0,
                        "建议卖出份额": 0.0,
                        "execution_note": "价格异常，已跳过该 ETF 卖出计划。",
                        "执行说明": "价格异常，已跳过该 ETF 卖出计划。",
                        "risk_note": "价格缺失，不能生成卖出计划。",
                        "风险提示": "价格缺失，不能生成卖出计划。",
                        "signal_date": signal_date or "",
                        "execution_date": execution_date or "",
                        "MA60": ma60,
                    }
                )
            )
            continue
        average_buy_price = float(item.get("average_buy_price") or item.get("cost_price") or current_price or 0)
        take_profit = calculate_take_profit_prices(average_buy_price, frame) if average_buy_price > 0 else {}
        risk = calculate_risk_exit_price(average_buy_price, frame) if average_buy_price > 0 else {}
        risk = validate_risk_trigger_price(
            {
                **risk,
                "symbol": symbol,
                "current_price": round(float(current_price), 3) if current_price > 0 else None,
                "sell_type": "trend_break",
            }
        )
        pnl = calculate_position_pnl(shares, average_buy_price, current_price)
        first_tp = float(take_profit.get("第一止盈价") or average_buy_price)
        raw_ma60 = float(risk.get("MA60", ma60))
        risk_trigger_for_action = _numeric_or_nan(risk.get("risk_trigger_price"))
        trend_line_for_action = risk_trigger_for_action if not pd.isna(risk_trigger_for_action) else float("nan")
        in_target = symbol in target_set
        action = translate_sell_action(current_price, trend_line_for_action, pnl["浮动盈亏率"], first_tp, in_target)
        risk_reason, risk_tip = translate_sell_reason(action, current_price, trend_line_for_action, pnl["浮动盈亏率"], in_target)
        candidates: list[dict[str, Any]] = []
        rebalance_shares = max(shares - target_share, 0.0)
        if rebalance_shares >= lot_size:
            candidates.append(
                {
                    "sell_type": "rebalance_sell",
                    "ratio": min(rebalance_shares / shares, 1.0),
                    "shares": _floor_lot(rebalance_shares, lot_size),
                    "priority": 70 if target_share <= 0 else 55,
                    "reason": "月度调仓目标组合不再需要该 ETF。" if target_share <= 0 else "月度调仓目标份额低于当前持仓，需要减到目标份额。",
                }
            )
        if action == "风控清仓":
            candidates.append({"sell_type": "stop_loss", "ratio": 1.0, "shares": _floor_lot(shares, lot_size), "priority": 95, "reason": risk_reason})
        elif action == "风控减仓":
            candidates.append({"sell_type": "trend_break", "ratio": 0.50, "shares": _candidate_sell_shares(shares, 0.50, lot_size), "priority": 85, "reason": risk_reason})
        elif action == "止盈卖出":
            candidates.append({"sell_type": "take_profit", "ratio": 0.30, "shares": _candidate_sell_shares(shares, 0.30, lot_size), "priority": 45, "reason": risk_reason})

        if candidates:
            primary = max(candidates, key=lambda row: (int(row["priority"]), float(row["shares"])))
            suggested_sell_shares = min(max(float(row["shares"]) for row in candidates), _floor_lot(shares, lot_size))
            sell_type = str(primary["sell_type"])
            sell_ratio = suggested_sell_shares / shares if shares > 0 else 0.0
            merged_reasons = "；".join(dict.fromkeys(str(row["reason"]) for row in candidates if row.get("reason")))
            reason = merged_reasons
            priority = int(primary["priority"])
            action = {
                "rebalance_sell": "调仓卖出",
                "trend_break": "风控减仓",
                "risk_reduce": "风控减仓",
                "stop_loss": "风控清仓",
                "take_profit": "止盈卖出",
            }.get(sell_type, action)
        else:
            suggested_sell_shares = 0.0
            sell_type = "hold"
            sell_ratio = 0.0
            reason = risk_reason
            priority = 0

        if sell_type == "take_profit":
            first_sell_price = take_profit.get("第一止盈价")
            second_sell_price = take_profit.get("第二止盈价")
            third_sell_price = take_profit.get("第三止盈价")
        elif sell_type == "hold":
            first_sell_price = None
            second_sell_price = None
            third_sell_price = None
        else:
            price_ladder = build_risk_sell_price_ladder(current_price)
            first_sell_price = price_ladder.get("first_sell_price")
            second_sell_price = price_ladder.get("second_sell_price")
            third_sell_price = price_ladder.get("third_sell_price")
        first_shares = _candidate_sell_shares(suggested_sell_shares, 0.40, lot_size)
        second_shares = _candidate_sell_shares(suggested_sell_shares - first_shares, 0.60, lot_size) if suggested_sell_shares > first_shares else 0.0
        third_shares = max(_floor_lot(suggested_sell_shares - first_shares - second_shares, lot_size), 0.0)
        is_full_sell = suggested_sell_shares >= _floor_lot(shares, lot_size) and suggested_sell_shares > 0
        explanation = (
            "本次为全卖，因为目标份额为 0 或触发严重趋势/止损条件。"
            if is_full_sell
            else "本次不是全卖，只按最高优先级风险或调仓需求减仓，保留剩余份额继续观察。"
        )
        trigger_source = _text(risk.get("risk_trigger_source")) or "风控触发线"
        trigger_warning = _text(risk.get("risk_trigger_warning"))
        if sell_type in RISK_SELL_TYPES:
            extra = f"{trigger_warning}" if trigger_warning else "当前价已跌破风控触发线。"
            execution_note = f"风控触发价来自 {trigger_source}，{extra}按风控规则卖出 {suggested_sell_shares:.0f} 份；挂单价基于当前参考价生成，不是止盈价。"
        elif sell_type == "take_profit":
            execution_note = f"达到止盈条件，建议按止盈价分批卖出 {suggested_sell_shares:.0f} 份。"
        elif sell_type == "rebalance_sell":
            execution_note = f"轮动调仓需要减仓，建议按当前参考价附近分批卖出 {suggested_sell_shares:.0f} 份。"
        else:
            execution_note = "暂不卖出，仅观察止盈价和风控触发线。"

        rows.append(
            validate_sell_prices(
                {
                "symbol": symbol,
                "name": item.get("name") or symbol,
                "ETF代码": symbol,
                "ETF名称": item.get("name") or symbol,
                "current_shares": shares,
                "持有份额": shares,
                "target_shares": target_share,
                "目标份额": target_share,
                "平均买入价": round(average_buy_price, 3) if average_buy_price > 0 else None,
                "current_price": round(float(current_price), 3) if current_price > 0 else None,
                "当前价格": round(float(current_price), 3) if current_price > 0 else None,
                "报价日期": quote.get("quote_date", ""),
                "报价时间": quote.get("quote_time", ""),
                "价格来源": quote.get("source", "历史行情"),
                "价格状态": quote_status or "最近交易日收盘价",
                "浮动盈亏率": pnl["浮动盈亏率"],
                "交易动作": action,
                "sell_type": sell_type,
                "卖出类型": sell_type,
                "sell_ratio": sell_ratio,
                "建议卖出比例": sell_ratio,
                "first_take_profit_price": take_profit.get("第一止盈价"),
                "second_take_profit_price": take_profit.get("第二止盈价"),
                "third_take_profit_price": take_profit.get("第三止盈价"),
                "第一止盈价": take_profit.get("第一止盈价"),
                "第二止盈价": take_profit.get("第二止盈价"),
                "第三止盈价": take_profit.get("第三止盈价"),
                "first_sell_price": first_sell_price,
                "second_sell_price": second_sell_price,
                "third_sell_price": third_sell_price,
                "第一卖出价": first_sell_price,
                "第二卖出价": second_sell_price,
                "第三卖出价": third_sell_price,
                "risk_trigger_price": risk.get("risk_trigger_price"),
                "风控触发价": risk.get("风控触发价"),
                "risk_trigger_display": risk.get("risk_trigger_display"),
                "risk_trigger_source": risk.get("risk_trigger_source"),
                "risk_trigger_ratio_to_current": risk.get("risk_trigger_ratio_to_current"),
                "risk_trigger_warning": risk.get("risk_trigger_warning"),
                "raw_risk_trigger_price": risk.get("raw_risk_trigger_price"),
                "raw_risk_trigger_source": risk.get("raw_risk_trigger_source"),
                "price_basis_consistent": risk.get("price_basis_consistent"),
                "price_basis_warning": risk.get("price_basis_warning"),
                "latest_close": risk.get("latest_close"),
                "previous_close": risk.get("previous_close"),
                "current_to_latest_close_ratio": risk.get("current_to_latest_close_ratio"),
                "latest_to_previous_close_ratio": risk.get("latest_to_previous_close_ratio"),
                "risk_limit_price": risk.get("risk_limit_price"),
                "风控挂单价": risk.get("风控挂单价"),
                "第一档卖出份额": _sell_share_text(first_shares),
                "第二档卖出份额": _sell_share_text(second_shares),
                "第三档卖出份额": _sell_share_text(third_shares),
                "suggested_sell_shares": suggested_sell_shares,
                "建议卖出份额": suggested_sell_shares,
                "sell_reason": reason,
                "触发说明": reason,
                "失效条件": "若主策略重新转为买入候选，且价格重新站上 60 日均线，原风控减仓计划需要重新评估。",
                "execution_note": execution_note,
                "执行说明": execution_note,
                "卖出说明": reason,
                "risk_note": risk_tip,
                "风险提示": risk_tip,
                "是否全卖": "是" if is_full_sell else "否",
                "priority": priority,
                "explanation": explanation,
                "signal_date": signal_date or "",
                "execution_date": execution_date or "",
                "MA20": risk.get("MA20"),
                "MA60": raw_ma60,
                "MA120": risk.get("MA120"),
                "持仓成本": pnl["持仓成本"],
                "当前市值": pnl["当前市值"],
                "浮动盈亏": pnl["浮动盈亏"],
            }
            )
        )
    return rows


def build_sell_execution_plan(
    positions: dict[str, Any],
    market_frames: dict[str, pd.DataFrame],
    target_symbols: set[str] | list[str] | tuple[str, ...],
    lot_size: int = 100,
    quote_map: dict[str, dict[str, Any]] | None = None,
    target_shares: dict[str, float] | None = None,
    signal_date: str | None = None,
    execution_date: str | None = None,
) -> list[dict[str, Any]]:
    return build_sell_decisions(
        positions=positions,
        market_frames=market_frames,
        target_shares=target_shares,
        target_symbols=target_symbols,
        lot_size=lot_size,
        quote_map=quote_map,
        signal_date=signal_date,
        execution_date=execution_date,
    )


def calculate_ladder_orders(total_amount: float, entry_prices: dict[str, Any], lot_size: int = 100) -> list[dict[str, Any]]:
    weights = [("第一档", "第一买入价", 0.40), ("第二档", "第二买入价", 0.35), ("第三档", "第三买入价", 0.25)]
    trigger_text = {
        "第一档": str(entry_prices.get("第一档触发说明") or "价格回落到第一档附近，说明没有明显追高，可买入试探仓。"),
        "第二档": str(entry_prices.get("第二档触发说明") or "价格回落到第二档附近，属于正常回调，可继续买入计划仓。"),
        "第三档": str(entry_prices.get("第三档触发说明") or "价格回落到第三档附近，属于较深回调，只在趋势未破坏时执行。"),
    }
    rows: list[dict[str, Any]] = []
    for label, price_key, weight in weights:
        price = float(entry_prices.get(price_key) or 0)
        amount = max(float(total_amount), 0.0) * weight
        shares = math.floor(amount / price / lot_size) * lot_size if price > 0 else 0
        rows.append(
            {
                "档位": label,
                "买入价": price,
                "建议买入金额": amount,
                "建议买入份额": float(shares),
                "触发说明": trigger_text[label],
                "失效条件": entry_prices.get("失效条件", ""),
                "执行状态": "金额不足 100 份，跳过该档" if shares < lot_size else "等待价格触发",
            }
        )
    return rows


def build_intraday_execution_plan(buy_plan: list[dict[str, Any]], market_data: dict[str, pd.DataFrame] | None = None) -> list[dict[str, Any]]:
    market_data = market_data or {}
    rows: list[dict[str, Any]] = []
    for item in buy_plan:
        symbol = _text(item.get("ETF代码"))
        quality = _text(item.get("数据质量"))
        if quality in {QUALITY_SEVERE, QUALITY_UNAVAILABLE}:
            rows.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": item.get("ETF名称", symbol),
                    "档位": "不生成",
                    "触发说明": "数据质量不足，今日不建议新增买入。",
                    "失效条件": "数据质量不足，今日买入计划取消。",
                }
            )
            continue
        entry_prices = {
            "第一买入价": item.get("第一买入价"),
            "第二买入价": item.get("第二买入价"),
            "第三买入价": item.get("第三买入价"),
            "失效条件": item.get("失效条件", ""),
        }
        ladders = item.get("每档计划")
        if not isinstance(ladders, list):
            total_amount = float(item.get("仍需买入金额") or item.get("今日建议买入金额") or item.get("预计买入金额") or 0)
            ladders = calculate_ladder_orders(total_amount, entry_prices)
        for ladder in ladders:
            rows.append(
                {
                    "ETF代码": symbol,
                    "ETF名称": item.get("ETF名称", symbol),
                    **ladder,
                }
            )
    return rows


def contains_code_language(text: str) -> bool:
    return bool(re.search(r"\b(ranks|momentum|moving average|score|filter|passed|failed)\b", _text(text), flags=re.I))
