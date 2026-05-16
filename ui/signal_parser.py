from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from data.universe import build_universe_stage_counts
from signal.trade_policy import (
    QUALITY_LIGHT,
    QUALITY_NORMAL,
    QUALITY_SEVERE,
    QUALITY_UNAVAILABLE,
    normalize_error_message,
    translate_strategy_status,
)


MAIN_STRATEGY = "momentum_rotation_monthly"
STRATEGY_ORDER = [
    "momentum_rotation_monthly",
    "reduced_equal_weight_monthly",
    "equal_weight_monthly",
    "balanced",
    "conservative",
]
MARKET_CLOSE_TIME = time(15, 0)
MARKET_TZ = ZoneInfo("Asia/Shanghai")
MIN_FILTERED_ETF_COUNT = 30
MIN_RANKED_ETF_COUNT = 30
EXCELLENT_FILTERED_ETF_COUNT = 100
EXCELLENT_RANKED_ETF_COUNT = 100
MAX_NORMAL_QA_WARNING_COUNT = 10
MAX_EXCELLENT_QA_WARNING_COUNT = 0
MAX_NORMAL_DOWNLOAD_FAILURE_COUNT = 5
NORMAL_OBSERVATION_CASH_MIN = 30000.0

STRATEGY_LABELS = {
    "momentum_rotation_monthly": "动态量化轮动策略",
    "reduced_equal_weight_monthly": "固定篮子基准策略 / 精选等权配置策略",
    "equal_weight_monthly": "全池等权配置策略",
    "balanced": "研究策略：均衡轮动",
    "conservative": "防守参考策略",
    "original": "原始策略",
}

STATUS_LABELS = {
    "recommended_for_observation": "暂不买入，只观察",
    "research_observation_candidate": "可买入候选，但等待价格确认",
    "research_only": "不参与实盘，只作研究对照",
    "defensive_only": "防守模式参考，不作为主动买入信号",
    "rejected": "今日不买入",
}

EMPTY_TEXTS = {"", "无", "空仓", "未填写", "N/A", "nan", "None"}


@dataclass(frozen=True)
class DashboardData:
    overview: dict[str, Any]
    signals: pd.DataFrame
    rankings: pd.DataFrame
    coverage: pd.DataFrame
    universe_raw: pd.DataFrame
    universe_snapshot: pd.DataFrame
    qa_report: dict[str, Any]
    strategy_review: pd.DataFrame
    etf_names: dict[str, str]
    output_mtimes: dict[str, str]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def _clean_text(value: Any, default: str = "") -> str:
    if value in ("", None):
        return default
    try:
        if pd.isna(value):
            return default
    except TypeError:
        pass
    return str(value)


def _format_mtime(path: Path) -> str:
    if not path.exists():
        return "未生成"
    return datetime.fromtimestamp(path.stat().st_mtime, MARKET_TZ).strftime("%Y-%m-%d %H:%M:%S")


def strategy_label(strategy_name: str) -> str:
    return STRATEGY_LABELS.get(strategy_name, strategy_name)


def status_label(status: str) -> str:
    return translate_strategy_status(status)


def source_label(source: str) -> str:
    return "手动选择" if source == "manual" else "自动使用最新可用数据"


def rebalance_rule_label(rule: Any) -> str:
    text = _clean_text(rule)
    if not text or text == "N/A":
        return "未提供"
    if "monthly" in text:
        return "月度调仓观察"
    if "biweekly" in text:
        return "双周调仓观察"
    if "weekly" in text:
        return "周度调仓观察"
    return "按策略配置调仓"


def load_etf_names(project_root: Path) -> dict[str, str]:
    for candidate in [
        project_root / "output" / "etf_universe_raw.csv",
        project_root / "output" / "etf_universe_snapshot.csv",
    ]:
        if candidate.exists():
            frame = pd.read_csv(candidate, dtype={"symbol": str}).fillna("")
            if {"symbol", "name"}.issubset(frame.columns):
                return {str(row["symbol"]).zfill(6): str(row["name"]) for _, row in frame.iterrows()}
    path = project_root / "config" / "etf_universe.yaml"
    if not path.exists():
        path = project_root / "config" / "etf_pool.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    items = raw.get("etfs", raw.get("etf_pool", raw if isinstance(raw, list) else []))
    return {str(item.get("symbol", "")).zfill(6): str(item.get("name", "")) for item in items if item.get("symbol")}


def ordered_signals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "strategy_name" not in df.columns:
        return df
    order_map = {name: idx for idx, name in enumerate(STRATEGY_ORDER)}
    result = df.copy()
    if "signal_date_source" not in result.columns:
        if "requested_signal_date" in result.columns:
            requested = result["requested_signal_date"].fillna("").astype(str).str.strip()
            result["signal_date_source"] = requested.apply(lambda value: "manual" if value else "auto")
        else:
            result["signal_date_source"] = "auto"
    result["_order"] = result["strategy_name"].map(order_map).fillna(999)
    return result.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)


def _after_a_share_close(now: datetime | None = None) -> tuple[bool, str]:
    current = now or datetime.now(MARKET_TZ)
    if current.weekday() >= 5:
        return False, current.date().isoformat()
    return current.time() >= MARKET_CLOSE_TIME, current.date().isoformat()


def _is_stale_after_close(latest_data_date: str, now: datetime | None = None) -> tuple[bool, str, str]:
    after_close, system_date = _after_a_share_close(now)
    if not after_close or not latest_data_date or latest_data_date == "N/A":
        return False, system_date, "是" if after_close else "否"
    try:
        latest = pd.Timestamp(latest_data_date).date().isoformat()
    except Exception:
        return False, system_date, "是" if after_close else "否"
    return latest < system_date, system_date, "是" if after_close else "否"


def _read_quality_report(project_root: Path) -> pd.DataFrame:
    return _read_csv(project_root / "output" / "data_quality_report.csv")


def _bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[column].astype(str).str.lower().isin(["true", "1", "yes"])


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None) or pd.isna(value):
            return default
    except TypeError:
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None) or pd.isna(value):
            return default
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _qa_warning_list(quality_frame: pd.DataFrame, limit: int = 12) -> list[str]:
    if quality_frame.empty or "warnings" not in quality_frame.columns:
        return []
    warnings: list[str] = []
    for _, row in quality_frame.iterrows():
        text = _clean_text(row.get("warnings"))
        if not text:
            continue
        symbol = _clean_text(row.get("symbol"), "UNKNOWN")
        name = _clean_text(row.get("name"), symbol)
        warnings.append(f"{symbol} {name}: {text}")
        if len(warnings) >= limit:
            break
    return warnings


def _layer_passed(qa_report: dict[str, Any], name: str) -> bool:
    layer = qa_report.get(name)
    return bool(layer.get("passed")) if isinstance(layer, dict) else False


def _execution_window_expired(execution_status: str) -> bool:
    return any(key in execution_status for key in ["已过", "过期", "错过"])


def _execution_window_ok(execution_status: str) -> bool:
    if not execution_status or execution_status == "N/A":
        return False
    return not _execution_window_expired(execution_status)


def _load_usage_mode(project_root: Path) -> dict[str, bool]:
    path = project_root / "config" / "live_observation.yaml"
    if not path.exists():
        path = project_root / "config" / "live_observation.example.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    mode = str(raw.get("usage_level") or raw.get("mode") or raw.get("trade_mode") or "").strip().lower()
    normal_enabled = bool(raw.get("normal_observation_enabled")) or mode in {
        "normal",
        "normal_observation",
        "formal",
        "formal_trading",
    }
    formal_enabled = bool(raw.get("formal_trading_enabled")) or mode in {"formal", "formal_trading"}
    return {
        "normal_observation_enabled": normal_enabled,
        "formal_trading_enabled": formal_enabled,
    }


def _main_strategy_status(qa_report: dict[str, Any]) -> str:
    strategy_layer = qa_report.get("strategy_layer")
    checks = strategy_layer.get("checks", []) if isinstance(strategy_layer, dict) else []
    for item in checks:
        if item.get("strategy_name") == MAIN_STRATEGY:
            return str(item.get("strategy_status") or "")
    return ""


def _next_level_requirements(
    trade_usage_level: str,
    data_quality_status: str,
    execution_status: str,
    quality_warning_count: int,
    download_failed_count: int,
    filtered_count: int,
    ranked_count: int,
    qa_all_passed: bool,
    strategy_status: str,
    normal_observation_enabled: bool,
    formal_trading_enabled: bool,
    observation_cash: float,
) -> tuple[str, list[str]]:
    if trade_usage_level == "可作为正式调仓参考":
        return "已达到最高等级", ["继续保持数据更新、QA 全通过、正式模式开启并在有效执行窗口内查看。"]

    if trade_usage_level == "允许正常观察":
        requirements = [
            "数据质量达到优秀，QA warning 为 0",
            "当前执行状态处于有效执行窗口内",
            "当前跟随策略通过推荐观察评估",
            "用户明确开启正式交易模式",
        ]
        if formal_trading_enabled:
            requirements.remove("用户明确开启正式交易模式")
        return "可作为正式调仓参考", requirements

    if trade_usage_level == "允许小额观察":
        requirements: list[str] = []
        if data_quality_status not in {"正常", "优秀"}:
            requirements.append("数据质量至少达到正常")
        if not qa_all_passed:
            requirements.append("数据层、策略层、输出层 QA 全部通过")
        if filtered_count < MIN_FILTERED_ETF_COUNT:
            requirements.append(f"过滤后可交易 ETF 数量达到 {MIN_FILTERED_ETF_COUNT} 只")
        if ranked_count < MIN_RANKED_ETF_COUNT:
            requirements.append(f"进入排名 ETF 数量达到 {MIN_RANKED_ETF_COUNT} 只")
        if download_failed_count > MAX_NORMAL_DOWNLOAD_FAILURE_COUNT:
            requirements.append(f"下载失败数量不超过 {MAX_NORMAL_DOWNLOAD_FAILURE_COUNT} 只")
        if quality_warning_count > MAX_NORMAL_QA_WARNING_COUNT:
            requirements.append(f"QA warning 数量不超过 {MAX_NORMAL_QA_WARNING_COUNT} 条")
        if _execution_window_expired(execution_status):
            requirements.append("在执行窗口内查看信号，不追过期执行窗口")
        if strategy_status and strategy_status != "recommended_for_observation":
            requirements.append("将当前跟随策略提升为 recommended_for_observation，或切换到已推荐策略")
        if not normal_observation_enabled:
            requirements.append("用户在 live_observation 配置中开启正常观察模式")
        if observation_cash < NORMAL_OBSERVATION_CASH_MIN:
            requirements.append(f"观察资金达到 {NORMAL_OBSERVATION_CASH_MIN:.0f} 元，或明确确认当前资金可用于正常观察")
        return "允许正常观察", requirements or ["当前已满足正常观察的主要条件，重新生成信号后刷新页面确认。"]

    return "允许小额观察", [
        "先确保有有效信号和排名结果",
        "数据层、策略层、输出层 QA 通过",
        f"过滤后 ETF 数量不少于 {MIN_FILTERED_ETF_COUNT} 只，进入排名不少于 {MIN_RANKED_ETF_COUNT} 只",
    ]


def build_quality_report(
    project_root: Path,
    main_row: pd.Series,
    qa_report: dict[str, Any],
    coverage: pd.DataFrame,
    universe_raw: pd.DataFrame,
    universe_snapshot: pd.DataFrame,
    rankings: pd.DataFrame,
    requested_signal_date: str,
    effective_signal_date: str,
    execute_date: str,
    execution_status: str,
    signal_date_source: str,
    latest_data_date: str,
    observation_cash: float,
    current_positions: str,
) -> dict[str, Any]:
    quality_frame = _read_quality_report(project_root)
    raw_for_counts = universe_raw if not universe_raw.empty else universe_snapshot
    counts = (
        build_universe_stage_counts(raw_for_counts, coverage, rankings)
        if not raw_for_counts.empty
        else {
            "raw_total": 0,
            "a_share_equity_total": 0,
            "listed_pass_count": 0,
            "amount_pass_count": 0,
            "completeness_pass_count": 0,
            "ranked_count": int(len(rankings)),
        }
    )
    raw_total = _safe_int(counts.get("raw_total"))
    a_share_total = _safe_int(counts.get("a_share_equity_total"))
    filtered_count = _safe_int(counts.get("completeness_pass_count"))
    ranked_count = _safe_int(counts.get("ranked_count"), int(len(rankings)))
    download_failed_count = int((~_bool_series(coverage, "success")).sum()) if not coverage.empty and "success" in coverage.columns else 0
    if not rankings.empty and "symbol" in rankings.columns and not quality_frame.empty and "symbol" in quality_frame.columns:
        ranked_symbols = set(rankings["symbol"].astype(str).str.zfill(6))
        quality_scope = quality_frame[quality_frame["symbol"].astype(str).str.zfill(6).isin(ranked_symbols)].copy()
    else:
        quality_scope = quality_frame

    data_layer_passed = _layer_passed(qa_report, "data_layer")
    strategy_layer_passed = _layer_passed(qa_report, "strategy_layer")
    output_layer_passed = _layer_passed(qa_report, "output_layer")
    qa_all_passed = data_layer_passed and strategy_layer_passed and output_layer_passed
    qa_warning_list = _qa_warning_list(quality_scope)
    quality_warning_count = int((quality_scope["warnings"].fillna("").astype(str).str.strip() != "").sum()) if "warnings" in quality_scope.columns else 0
    failed_quality_count = int((quality_scope["status"].astype(str) == "failed").sum()) if "status" in quality_scope.columns else 0

    raw_blocking_reasons = list(qa_report.get("blocking_reasons") or [])
    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []
    data_warnings: list[str] = []
    for reason in raw_blocking_reasons:
        text = str(reason)
        if "data quality failed for" in text:
            count = re.search(r"data quality failed for (\d+)", text)
            count_text = count.group(1) if count else "部分"
            data_warnings.append(f"有 {count_text} 只 ETF 数据质量未达标，已排除或仅作为观察，不参与今日买入")
        elif "ETF end-date coverage gap" in text:
            count = re.search(r"gap is (\d+)", text)
            count_text = f"{count.group(1)} 天" if count else "较大"
            data_warnings.append(f"部分 ETF 最新数据日期不一致，覆盖差异为 {count_text}，需等待数据补齐后再恢复正常买入")
        else:
            blocking_reasons.append(text)
    if not effective_signal_date or effective_signal_date == "N/A":
        blocking_reasons.append("无有效信号文件或信号日缺失")
    if ranked_count <= 0:
        blocking_reasons.append("无有效排名结果，无法评估 ETF 信号")
    if filtered_count < MIN_FILTERED_ETF_COUNT:
        data_warnings.append(f"过滤后可交易 ETF 数量偏少：{filtered_count} < {MIN_FILTERED_ETF_COUNT}")
    if ranked_count < MIN_RANKED_ETF_COUNT:
        data_warnings.append(f"进入排名 ETF 数量偏少：{ranked_count} < {MIN_RANKED_ETF_COUNT}")
    if download_failed_count > MAX_NORMAL_DOWNLOAD_FAILURE_COUNT:
        data_warnings.append(f"下载失败数量偏多：{download_failed_count} > {MAX_NORMAL_DOWNLOAD_FAILURE_COUNT}")
    if quality_warning_count > 0:
        data_warnings.append(f"进入排名池中存在 {quality_warning_count} 条数据质量提示，相关 ETF 已按质量等级影响买入动作")
    if failed_quality_count > 0:
        data_warnings.append(f"进入排名池中有 {failed_quality_count} 只 ETF 未达到完整数据 QA 标准，但未低于策略过滤阈值")
    if signal_date_source == "manual":
        data_warnings.append("当前使用手动选择日期生成信号")
    if effective_signal_date and latest_data_date and effective_signal_date != latest_data_date:
        data_warnings.append("信号日不是最新本地数据日期")

    score = 100
    score -= min(40, 15 * len(set(blocking_reasons)))
    score -= min(20, quality_warning_count * 2)
    score -= min(10, failed_quality_count // 4)
    score -= min(15, download_failed_count * 3)
    if filtered_count < MIN_FILTERED_ETF_COUNT:
        score -= 15
    if ranked_count < MIN_RANKED_ETF_COUNT:
        score -= 15
    if signal_date_source == "manual" or (effective_signal_date and latest_data_date and effective_signal_date != latest_data_date):
        score -= 10
    score = max(0, min(100, score))

    if blocking_reasons or ranked_count <= 0:
        data_quality_status = QUALITY_UNAVAILABLE
    elif (
        score < 60
        or not qa_all_passed
        or filtered_count < max(5, MIN_FILTERED_ETF_COUNT // 2)
        or ranked_count < max(5, MIN_RANKED_ETF_COUNT // 2)
    ):
        data_quality_status = QUALITY_SEVERE
    elif (
        quality_warning_count > MAX_NORMAL_QA_WARNING_COUNT
        or failed_quality_count > 0
        or filtered_count < MIN_FILTERED_ETF_COUNT
        or ranked_count < MIN_RANKED_ETF_COUNT
    ):
        data_quality_status = QUALITY_LIGHT
    elif (
        score >= 95
        and quality_warning_count <= MAX_EXCELLENT_QA_WARNING_COUNT
        and download_failed_count == 0
        and filtered_count >= EXCELLENT_FILTERED_ETF_COUNT
        and ranked_count >= EXCELLENT_RANKED_ETF_COUNT
        and effective_signal_date == latest_data_date
    ):
        data_quality_status = QUALITY_NORMAL
    else:
        data_quality_status = QUALITY_NORMAL

    usage_mode = _load_usage_mode(project_root)
    normal_observation_enabled = usage_mode["normal_observation_enabled"]
    formal_trading_enabled = usage_mode["formal_trading_enabled"]
    strategy_status = _main_strategy_status(qa_report) or str(main_row.get("strategy_status") or "")

    if data_quality_status == QUALITY_UNAVAILABLE:
        trade_usage_level = "行情获取失败，已排除"
    elif data_quality_status == QUALITY_SEVERE:
        trade_usage_level = "禁止新增买入，只允许观察或卖出风控"
    elif data_quality_status == QUALITY_LIGHT:
        trade_usage_level = "买入金额 × 50%"
    else:
        trade_usage_level = "允许买入"

    guardrail_reasons: list[str] = []
    if _execution_window_expired(execution_status):
        guardrail_reasons.append("当前执行窗口已过，不建议追单")
    if strategy_status and strategy_status != "recommended_for_observation":
        guardrail_reasons.append("策略仍处于观察模式")
    if not normal_observation_enabled:
        guardrail_reasons.append("尚未启用正常观察模式")
    if observation_cash < NORMAL_OBSERVATION_CASH_MIN:
        guardrail_reasons.append(f"当前资金规模为 {observation_cash:.2f} 元，低于正常观察参考阈值 {NORMAL_OBSERVATION_CASH_MIN:.0f} 元")
    if current_positions in {"空仓", "未填写", "N/A", ""}:
        guardrail_reasons.append(f"当前持仓状态为{current_positions or '未填写'}")

    if data_quality_status == QUALITY_NORMAL and guardrail_reasons:
        trade_usage_level = "允许买入，但需人工确认限制条件"

    warning_reasons.extend(data_warnings)
    warning_reasons.extend(guardrail_reasons)
    next_level, next_level_requirements = _next_level_requirements(
        trade_usage_level,
        data_quality_status,
        execution_status,
        quality_warning_count,
        download_failed_count,
        filtered_count,
        ranked_count,
        qa_all_passed,
        strategy_status,
        normal_observation_enabled,
        formal_trading_enabled,
        observation_cash,
    )

    return {
        "data_quality_status": data_quality_status,
        "trade_usage_level": trade_usage_level,
        "execution_status": execution_status or "N/A",
        "score": score,
        "passed": data_quality_status == QUALITY_NORMAL and qa_all_passed,
        "blocking_reasons": list(dict.fromkeys(blocking_reasons)),
        "warning_reasons": list(dict.fromkeys(warning_reasons)),
        "next_level": next_level,
        "next_level_requirements": list(dict.fromkeys(next_level_requirements)),
        "signal_date": effective_signal_date or "N/A",
        "execute_date": execute_date or "N/A",
        "latest_data_date": latest_data_date or "N/A",
        "raw_etf_count": raw_total,
        "a_share_etf_count": a_share_total,
        "filtered_etf_count": filtered_count,
        "ranked_etf_count": ranked_count,
        "download_failed_count": download_failed_count,
        "qa_passed": qa_all_passed,
        "qa_warning_count": quality_warning_count,
        "qa_warnings": qa_warning_list,
        "strategy_status": strategy_status or "unknown",
        "normal_observation_enabled": normal_observation_enabled,
        "formal_trading_enabled": formal_trading_enabled,
    }


def build_overview(
    project_root: Path,
    signals: pd.DataFrame,
    qa_report: dict[str, Any],
    coverage: pd.DataFrame | None = None,
    universe_raw: pd.DataFrame | None = None,
    universe_snapshot: pd.DataFrame | None = None,
    rankings: pd.DataFrame | None = None,
) -> dict[str, Any]:
    coverage = coverage if coverage is not None else pd.DataFrame()
    universe_raw = universe_raw if universe_raw is not None else pd.DataFrame()
    universe_snapshot = universe_snapshot if universe_snapshot is not None else pd.DataFrame()
    rankings = rankings if rankings is not None else pd.DataFrame()
    main_row = pd.Series(dtype=object)
    if not signals.empty:
        main = signals[signals["strategy_name"] == MAIN_STRATEGY]
        main_row = main.iloc[0] if not main.empty else signals.iloc[0]

    requested_signal_date = _clean_text(main_row.get("requested_signal_date", ""))
    effective_signal_date = _clean_text(main_row.get("effective_signal_date", main_row.get("signal_date", "")))
    execute_date = _clean_text(main_row.get("execute_date", ""))
    execution_status = _clean_text(main_row.get("execution_status", ""))
    signal_date_source = _clean_text(main_row.get("signal_date_source", ""), "manual" if requested_signal_date else "auto")
    latest_data_date = _clean_text(main_row.get("latest_data_date", ""))
    current_positions = _clean_text(main_row.get("current_positions", "未填写"), "未填写")

    observation_cash = main_row.get("observation_cash", main_row.get("current_cash", ""))
    observation_cash_value = _safe_float(observation_cash)
    try:
        observation_cash_text = f"{observation_cash_value:.2f} 元"
    except (TypeError, ValueError):
        observation_cash_text = _clean_text(observation_cash, "N/A")

    current_cash = main_row.get("current_cash", "")
    try:
        current_cash_text = f"{float(current_cash):.2f} 元"
    except (TypeError, ValueError):
        current_cash_text = _clean_text(current_cash, "N/A")

    allow_small_observation = qa_report.get("allow_small_observation")
    allow_text = "YES" if allow_small_observation is True else "NO" if allow_small_observation is False else str(allow_small_observation or "UNKNOWN")

    stale_after_close, system_date, after_close_text = _is_stale_after_close(latest_data_date)
    quality_report = build_quality_report(
        project_root=project_root,
        main_row=main_row,
        qa_report=qa_report,
        coverage=coverage,
        universe_raw=universe_raw,
        universe_snapshot=universe_snapshot,
        rankings=rankings,
        requested_signal_date=requested_signal_date,
        effective_signal_date=effective_signal_date,
        execute_date=execute_date,
        execution_status=execution_status,
        signal_date_source=signal_date_source,
        latest_data_date=latest_data_date,
        observation_cash=observation_cash_value,
        current_positions=current_positions,
    )
    risk_status = quality_report["trade_usage_level"]

    return {
        "requested_signal_date": requested_signal_date or "N/A",
        "effective_signal_date": effective_signal_date or "N/A",
        "execute_date": execute_date or "N/A",
        "execution_status": execution_status or "N/A",
        "signal_date_source": signal_date_source,
        "signal_date_source_label": source_label(signal_date_source),
        "latest_data_date": latest_data_date or "N/A",
        "observation_cash": observation_cash_text,
        "current_cash": current_cash_text,
        "current_positions": current_positions,
        "main_strategy": MAIN_STRATEGY,
        "main_strategy_label": strategy_label(MAIN_STRATEGY),
        "allow_small_observation": allow_text,
        "risk_status": risk_status,
        "quality_report": quality_report,
        "data_quality_status": quality_report["data_quality_status"],
        "trade_usage_level": quality_report["trade_usage_level"],
        "quality_score": quality_report["score"],
        "data_stale_after_close": stale_after_close,
        "system_date": system_date,
        "after_1500": after_close_text,
    }


def load_dashboard_data(project_root: Path) -> DashboardData:
    output = project_root / "output"
    strategy_path = output / "strategy_compare_signal.csv"
    signals = ordered_signals(_read_csv(strategy_path if strategy_path.exists() else output / "compare_signal.csv"))
    rankings = _read_csv(output / "compare_signal_rankings.csv")
    if rankings.empty:
        rankings = _read_csv(output / "compare_signal.csv")
        if "strategy_name" in rankings.columns:
            rankings = pd.DataFrame()
    coverage = _read_csv(output / "data_coverage_report.csv")
    universe_raw = _read_csv(output / "etf_universe_raw.csv")
    universe_snapshot = _read_csv(output / "etf_universe_snapshot.csv")
    qa_report = _read_json(output / "qa_report.json")
    strategy_review = _read_csv(output / "strategy_review.csv")
    etf_names = load_etf_names(project_root)
    overview = build_overview(project_root, signals, qa_report, coverage, universe_raw, universe_snapshot, rankings)
    output_mtimes = {
        "compare_signal.csv": _format_mtime(output / "compare_signal.csv"),
        "strategy_compare_signal.csv": _format_mtime(output / "strategy_compare_signal.csv"),
        "compare_signal.txt": _format_mtime(output / "compare_signal.txt"),
        "qa_report.json": _format_mtime(output / "qa_report.json"),
    }
    return DashboardData(
        overview=overview,
        signals=signals,
        rankings=rankings,
        coverage=coverage,
        universe_raw=universe_raw,
        universe_snapshot=universe_snapshot,
        qa_report=qa_report,
        strategy_review=strategy_review,
        etf_names=etf_names,
        output_mtimes=output_mtimes,
    )


def split_pipe_items(value: Any) -> list[str]:
    text = _clean_text(value)
    if not text or text in EMPTY_TEXTS:
        return []
    return [item.strip() for item in text.split(" | ") if item.strip()]


def _symbols_from_csv(value: Any) -> list[str]:
    text = _clean_text(value)
    if not text or text in EMPTY_TEXTS:
        return []
    return [item.strip() for item in text.split(",") if item.strip() and item.strip() not in EMPTY_TEXTS]


def format_symbol_list(symbols: list[str], etf_names: dict[str, str]) -> str:
    if not symbols:
        return "无"
    return "、".join(f"{symbol} {etf_names.get(symbol, symbol)}" for symbol in symbols)


def target_amount_map(row: pd.Series) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in split_pipe_items(row.get("target_amounts")):
        if ":" not in item:
            continue
        symbol, value = item.split(":", 1)
        try:
            result[symbol.strip()] = float(value)
        except ValueError:
            continue
    return result


def target_symbols(row: pd.Series) -> list[str]:
    return _symbols_from_csv(row.get("target_symbols"))


def current_symbols(row: pd.Series) -> list[str]:
    return _symbols_from_csv(row.get("current_positions"))


def buy_symbols(row: pd.Series) -> list[str]:
    return _symbols_from_csv(row.get("suggested_buy"))


def sell_symbols(row: pd.Series) -> list[str]:
    return _symbols_from_csv(row.get("suggested_sell"))


def hold_symbols(row: pd.Series) -> list[str]:
    target = set(target_symbols(row))
    current = set(current_symbols(row))
    return sorted(target & current)


def portfolio_changed(row: pd.Series) -> bool:
    return bool(set(target_symbols(row)) != set(current_symbols(row)) or buy_symbols(row) or sell_symbols(row))


def parse_target_table(row: pd.Series, etf_names: dict[str, str]) -> pd.DataFrame:
    symbols = target_symbols(row)
    amounts = target_amount_map(row)
    weight = f"{100 / len(symbols):.1f}%" if symbols else ""
    return pd.DataFrame(
        [
            {
                "ETF 代码": symbol,
                "ETF 名称": etf_names.get(symbol, symbol),
                "目标权重": weight,
                "目标金额": f"{amounts.get(symbol, 0.0):.2f} 元" if symbol in amounts else "N/A",
            }
            for symbol in symbols
        ]
    )


def _json_records(value: Any) -> list[dict[str, Any]]:
    text = _clean_text(value)
    if not text or text in EMPTY_TEXTS:
        return []
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _display_plan_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    if "每档计划" in df.columns:
        df = df.drop(columns=["每档计划"])
    if "失效条件" in df.columns and "风险提示" in df.columns:
        df = df.drop(columns=["失效条件"])
    if "reason" in df.columns and "买入原因" in df.columns:
        df = df.drop(columns=["reason"])
    if "实际成交说明" in df.columns and "执行说明" in df.columns:
        df = df.drop(columns=["实际成交说明"])
    df = df.rename(
        columns={
            "ETF代码": "ETF 代码",
            "ETF名称": "ETF 名称",
            "目标权重": "目标权重",
            "目标金额": "目标金额",
            "建议买入份额": "建议买入份额",
            "预计买入金额": "预计买入金额",
            "资金不足时的提示": "提示",
            "实际成交说明": "执行说明",
            "当前持有份额": "当前持有份额",
            "建议卖出份额": "建议卖出份额",
            "卖出原因": "原因",
            "参考估算价格": "参考价格",
            "预计卖出金额": "预计卖出金额",
            "当前份额": "当前份额",
            "当前估算金额": "当前估算金额",
            "是否需要补仓 / 减仓 / 不操作": "操作判断",
            "一手所需资金": "一手所需资金",
            "当前可用现金": "当前可用现金",
            "reason": "原因",
        }
    )
    money_cols = ["目标金额", "今日建议买入金额", "预计买入金额", "预计卖出金额", "当前估算金额", "一手所需资金", "当前可用现金", "建议买入金额"]
    share_cols = ["建议买入份额", "当前持有份额", "建议卖出份额", "当前份额"]
    for col in money_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda value: "N/A" if value in ("", None) or pd.isna(value) else f"{float(value):.2f} 元")
    for col in share_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda value: "N/A" if value in ("", None) or pd.isna(value) else f"{float(value):.0f} 份")
    for col in ["参考价格", "第一买入价", "第二买入价", "第三买入价", "买入价"]:
        if col in df.columns:
            df[col] = df[col].apply(lambda value: "N/A" if value in ("", None) or pd.isna(value) else f"{float(value):.3f}")
    preferred = [
        "ETF代码",
        "ETF名称",
        "交易动作",
        "目标权重",
        "目标金额",
        "今日建议买入金额",
        "第一买入价",
        "第二买入价",
        "第三买入价",
        "建议买入份额",
        "数据质量",
        "执行说明",
        "买入原因",
        "风险提示",
    ]
    if any(col in df.columns for col in preferred):
        return df[[col for col in preferred if col in df.columns] + [col for col in df.columns if col not in preferred]]
    return df


def parse_buy_table(row: pd.Series) -> pd.DataFrame:
    return _display_plan_frame(_json_records(row.get("buy_plan")))


def parse_intraday_execution_table(row: pd.Series) -> pd.DataFrame:
    return _display_plan_frame(_json_records(row.get("intraday_execution_plan")))


def parse_skip_table(row: pd.Series) -> pd.DataFrame:
    return _display_plan_frame(_json_records(row.get("skipped_buy_plan")))


def parse_sell_table(row: pd.Series) -> pd.DataFrame:
    return _display_plan_frame(_json_records(row.get("sell_plan")))


def parse_hold_table(row: pd.Series) -> pd.DataFrame:
    return _display_plan_frame(_json_records(row.get("hold_plan")))


def parse_rank_table(row: pd.Series) -> pd.DataFrame:
    records = _json_records(row.get("rank_table"))
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df = df.rename(
        columns={
            "symbol": "ETF 代码",
            "name": "ETF 名称",
            "exchange": "交易所",
            "asset_class": "资产类别",
            "category": "细分类别",
            "tracking_index": "跟踪指数",
            "theme": "主题",
            "sector": "行业/板块",
            "latest_date": "最新日期",
            "close": "收盘价",
            "momentum": "动量",
            "momentum_20": "20日动量",
            "momentum_60": "60日动量",
            "momentum_120": "120日动量",
            "volatility_20": "20日波动率",
            "max_drawdown_60": "60日最大回撤",
            "score": "综合得分",
            "ma": "均线",
            "above_ma": "是否高于均线",
            "rank": "排名",
            "selected": "是否入选",
            "final_signal": "最终信号",
            "selection_reason": "入选 / 未入选原因",
        }
    )
    for col in ["收盘价", "均线"]:
        if col in df.columns:
            df[col] = df[col].apply(lambda value: "N/A" if value in ("", None) or pd.isna(value) else f"{float(value):.4f}")
    for col in ["动量", "20日动量", "60日动量", "120日动量", "20日波动率", "60日最大回撤"]:
        if col in df.columns:
            df[col] = df[col].apply(lambda value: "N/A" if value in ("", None) or pd.isna(value) else f"{float(value) * 100:.2f}%")
    if "综合得分" in df.columns:
        df["综合得分"] = df["综合得分"].apply(lambda value: "N/A" if value in ("", None) or pd.isna(value) else f"{float(value):.6f}")
    for col in ["是否高于均线", "是否入选"]:
        if col in df.columns:
            df[col] = df[col].apply(lambda value: "是" if bool(value) else "否")
    columns = [
        "ETF 代码",
        "ETF 名称",
        "交易所",
        "资产类别",
        "细分类别",
        "跟踪指数",
        "最新日期",
        "20日动量",
        "60日动量",
        "120日动量",
        "20日波动率",
        "60日最大回撤",
        "综合得分",
        "排名",
        "最终信号",
        "是否入选",
        "入选 / 未入选原因",
    ]
    return df[[col for col in columns if col in df.columns]]


def strategy_row(signals: pd.DataFrame, strategy_name: str) -> pd.Series:
    if signals.empty:
        return pd.Series(dtype=object)
    matched = signals[signals["strategy_name"] == strategy_name]
    if matched.empty:
        return pd.Series(dtype=object)
    return matched.iloc[0]
