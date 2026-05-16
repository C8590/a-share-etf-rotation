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
    elif any(key in lower for key in ["timeout", "timed out", "connection", "network", "rate", "限流", "too many"]):
        error_type = "网络或接口限流"
        frontend = "行情接口暂时访问不稳定，已暂缓该 ETF，不参与今日买入"
        action = "暂缓观察，不参与买入"
    elif any(key in lower for key in ["all data sources failed", "akshare", "fund_etf_hist", "source returned empty"]):
        error_type = "数据源不可用"
        frontend = "该 ETF 暂时无法获取历史行情，已自动排除，不参与今日信号计算"
        action = "已排除，不参与买入"
    elif any(key in lower for key in ["invalid", "not found", "代码", "symbol"]):
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
    prices = {
        "第一买入价": round(reference_price * (1 - 0.25 * vol_band), 3),
        "第二买入价": round(reference_price * (1 - 0.50 * vol_band), 3),
        "第三买入价": round(reference_price * (1 - 0.85 * vol_band), 3),
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


def calculate_ladder_orders(total_amount: float, entry_prices: dict[str, Any], lot_size: int = 100) -> list[dict[str, Any]]:
    weights = [("第一档", "第一买入价", 0.40), ("第二档", "第二买入价", 0.35), ("第三档", "第三买入价", 0.25)]
    trigger_text = {
        "第一档": "价格回落到第一档附近，说明没有明显追高，可买入试探仓。",
        "第二档": "价格回落到第二档附近，属于正常回调，可继续买入计划仓。",
        "第三档": "价格回落到第三档附近，属于较深回调，只在趋势未破坏时执行。",
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
            total_amount = float(item.get("今日建议买入金额") or item.get("预计买入金额") or 0)
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
