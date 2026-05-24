from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


COLUMN_NAME_MAP = {
    "code": "代码",
    "symbol": "ETF代码",
    "ETF代码": "ETF代码",
    "name": "ETF名称",
    "ETF名称": "ETF名称",
    "date": "日期",
    "signal_date": "信号日",
    "requested_signal_date": "选择信号日",
    "effective_signal_date": "信号日",
    "execution_date": "预计执行日",
    "execute_date": "预计执行日",
    "latest_data_date": "最新数据日期",
    "exchange": "交易所",
    "asset_class": "资产类别",
    "category": "细分类别",
    "theme": "主题",
    "sector": "行业/板块",
    "sector_l1": "一级行业",
    "sector_l2": "二级行业",
    "risk_group": "风险分组",
    "aliases": "方向别名",
    "tracking_index": "跟踪指数",
    "listing_date": "上市日期",
    "latest_date": "最新数据日期",
    "spot_date": "行情日期",
    "price": "价格",
    "spot_amount": "当日成交额",
    "amount": "成交额",
    "volume": "成交量",
    "avg_amount_20": "近20日平均成交额",
    "data_rows": "数据行数",
    "is_active": "是否活跃",
    "is_trade_day": "是否交易日",
    "success": "下载成功",
    "source": "数据源",
    "start_date": "起始日期",
    "end_date": "结束日期",
    "rows": "数据行数",
    "missing_count": "缺失行数",
    "duplicate_count": "重复日期数",
    "status": "状态",
    "quality_status": "质量状态",
    "failure_reason": "失败原因",
    "errors": "错误",
    "warnings": "提示",
    "momentum": "动量",
    "momentum_20": "20日动量",
    "momentum_60": "60日动量",
    "momentum_120": "120日动量",
    "volatility_20": "20日波动率",
    "max_drawdown_60": "60日最大回撤",
    "score": "综合得分",
    "rank": "排名",
    "final_signal": "最终信号",
    "weight": "目标仓位",
    "target_weight": "目标权重",
    "current_weight": "当前权重",
    "action": "操作",
    "trade_action": "调仓动作",
    "trade_amount": "交易金额",
    "shares": "建议份额",
    "filter_reason": "过滤原因",
    "eligible": "是否进入策略观察池",
    "selected": "是否进入候选池",
    "close": "收盘价",
    "return": "收益率",
    "volatility": "波动率",
    "ma": "均线",
    "ma20": "20日均线",
    "ma60": "60日均线",
    "above_ma": "是否高于均线",
    "reason": "基础过滤说明",
    "selection_reason": "候选/过滤原因",
    "missing_days": "缺失天数",
    "local_latest_date": "本地最新日期",
    "target_update_date": "目标更新日期",
    "cached": "使用缓存",
}


VALUE_MAP = {
    "A股股票": "A股股票",
    "bond": "债券",
    "commodity": "商品",
    "cross_border": "跨境",
    "cash": "货币",
    "equity": "A股股票",
    "selected": "进入候选池",
    "eligible_not_selected": "通过过滤但未进候选池",
    "filtered_out": "未通过过滤",
    "watch": "观察，不买入",
    "WATCH": "观察，不买入",
    "up_to_date": "行情已是最新",
    "outdated": "需要更新",
    "failed": "更新失败",
    "success": "更新成功",
    "cached_success": "使用缓存",
    "cold_start": "首次写入缓存",
    "cold_start_deferred": "暂缓冷启动下载",
    "skipped": "已跳过",
    "ok": "行情已是最新",
    "DRAFT": "订单草稿",
    "SIMULATION": "模拟执行",
    "MANUAL_CONFIRM": "人工确认",
    "DRAFT_BUY": "买入订单草稿",
    "BLOCKED_BUY": "买入已阻断",
    "DRAFT_EXIT": "卖出订单草稿",
    "NO_ORDER": "无订单意图",
    "BUY": "买入方向",
    "SELL": "卖出方向",
    "V2_MODULAR": "V2.1 模块化总控信号",
    "V1_LEGACY": "V1 传统信号（仅用于对照）",
    "HOLD": "继续持有",
    "FORBID_BUY": "禁止买入",
    "STANDARD_BUY": "标准买入",
    "PROBE_BUY": "试探买入",
    "RISK_EXIT": "风险退出",
    "TREND_DECAY_EXIT": "趋势衰减退出",
    "REPLACEMENT_EXIT": "调仓替换退出",
    True: "是",
    False: "否",
}


def _display_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "nat", "<na>"}:
        return ""
    return VALUE_MAP.get(text, text)


def metric_card(label: str, value: Any) -> None:
    st.metric(label, value if value not in ("", None) else "N/A")


def localize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == bool:
            out[col] = out[col].map(VALUE_MAP)
        elif col in {"asset_class", "final_signal", "success", "is_active", "eligible", "selected", "status"}:
            out[col] = out[col].map(lambda value: VALUE_MAP.get(value, value))
        elif out[col].dtype == object:
            out[col] = out[col].map(_display_text)
    return out.rename(columns={col: COLUMN_NAME_MAP.get(col, col) for col in out.columns})


def localize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    return localize_columns(df)


def show_dataframe_or_empty(df: pd.DataFrame, empty_text: str = "无", key: str | None = None, height: int = 400) -> None:
    if df.empty:
        st.caption(empty_text)
    else:
        st.dataframe(localize_columns(df), hide_index=True, width="stretch", height=height)


def status_badge(status: str) -> None:
    if status == "recommended_for_observation":
        st.info("暂不买入，只观察")
    elif status == "research_observation_candidate":
        st.info("可买入候选，但等待价格确认")
    elif status == "research_only":
        st.warning("不参与实盘，只作研究对照")
    elif status == "defensive_only":
        st.info("防守模式参考，不作为主动买入信号")
    elif status == "rejected":
        st.error("今日不买入")
    else:
        st.info(status or "未知")
