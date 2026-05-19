from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


KEY_LABELS = {
    "overall_project_status": "项目状态",
    "allowed_to_enter_007b": "007B 准入",
    "allowed_to_enter_007b_scope": "007B 范围",
    "allowed_to_enter_008b": "008B 准入",
    "candidate_eligible_count": "候选可用",
    "candidate_blocked_count": "候选阻断",
    "data_quality_failed_count": "数据质量未通过",
    "end_date_coverage_gap_days": "行情覆盖滞后",
    "manual_review_count": "人工复核",
    "factor_gate_status": "因子评分",
}

FIELD_LABELS = {
    "qa_item": "QA 项目",
    "raw_status": "原始状态",
    "normalized_status": "状态",
    "severity": "严重程度",
    "blocking": "是否阻断",
    "actionability": "处理方式",
    "affected_count": "影响数量",
    "affected_ratio": "影响比例",
    "root_cause": "根本原因",
    "governed_by": "治理来源",
    "recommended_action": "建议操作",
    "blocks_candidate_pool": "阻断候选池",
    "blocks_007b": "阻断 007B",
    "blocks_008b": "阻断 008B",
    "symbol": "代码",
    "name": "名称",
    "candidate_status": "候选状态",
    "eligibility_status": "准入状态",
    "block_reason": "阻断原因",
    "observation_reason": "观察原因",
    "requires_manual_review": "需要人工复核",
    "factor_gate_status": "因子门禁",
    "tracking_index_code": "指数代码",
    "tracking_index_name": "指数名称",
    "tracking_error": "跟踪误差",
    "relative_return_20d": "20日相对收益",
    "relative_return_60d": "60日相对收益",
    "relative_return_120d": "120日相对收益",
    "validation_status": "验证状态",
    "failure_reason": "不可用原因",
    "readiness_item": "检查项",
    "current_status": "当前状态",
    "actual_value": "当前值",
    "remediation_action": "处理建议",
    "notes": "说明",
    "observation_status": "观察状态",
    "observation_priority": "观察优先级",
    "low_liquidity_flag": "低流动性观察",
    "estimated_trading_days_until_eligible": "预计还需交易日",
    "manual_review_reason": "复核原因",
    "recommended_checks": "建议检查",
    "review_status": "复核状态",
    "review_priority": "复核优先级",
    "gate_item": "门禁项",
    "status": "状态",
    "finding": "发现",
    "suggested_action": "建议操作",
    "audit_item": "审计项",
    "count": "数量",
    "row_count": "已有交易日",
    "rows_needed": "还需交易日",
}

STATUS_LABELS = {
    "007b_small_scope_only": "007B 小范围可用",
    "008b_available": "008B 可用",
    "blocked_for_strategy_use": "暂不可用于策略",
    "ready_small_scope": "小范围就绪",
    "small_scope": "小范围研究",
    "blocked": "阻断",
    "governed_blocked": "已治理，未放行",
    "passed": "已通过",
    "ok": "可用",
    "warning": "观察",
    "failed": "失败",
    "failed_governed": "已治理，未放行",
    "failed_actionable": "需处理的失败",
    "research_only": "仅研究",
    "computed_valid": "指标可计算",
    "unable_to_compute": "暂无法计算",
    "no_index_cache": "缺少指数缓存",
    "missing_benchmark": "缺少基准指数",
    "blocked_short_history": "历史不足阻断",
    "blocked_manual_review": "人工复核阻断",
    "blocked_no_used_factors": "无可用因子阻断",
    "factor_gate_blocked": "因子门禁阻断",
    "P0_manual_review": "P0 人工复核",
    "P1_wait_for_history": "P1 等待历史数据补足",
    "P1_short_history_observe": "P1 等待历史数据补足",
    "P2_low_liquidity_watch": "P2 低流动性观察",
    "blocked_until_review": "复核前阻断",
    "wait_for_history": "等待历史数据补足",
    "manual_review": "人工复核",
    "manual_review_required": "需要人工复核",
    "refresh_needed": "需要受控刷新",
    "source_diagnosis": "需要数据源诊断",
    "source_unavailable": "数据源不可用",
    "governance_blocked": "治理门禁阻断",
    "already_governed": "已纳入治理",
    "stale_or_source_lag": "行情覆盖滞后或数据源滞后",
    "source_lag": "数据源滞后",
    "provider_stale": "数据源停更或滞后",
    "source_lag_blocker": "数据源滞后阻断",
    "short_history": "历史数据不足",
    "very_short_history": "极短历史",
    "new_etf_short_history": "新基金历史数据不足",
    "no_used_factors": "无可用因子",
    "insufficient_rows": "历史数据不足",
    "low_liquidity": "低流动性",
    "zero_or_low_liquidity": "零成交或低流动性",
    "abnormal_return": "异常收益",
    "requires_review": "需要复核",
    "unknown_quality_finding": "未知质量发现",
    "exclude_from_candidate_pool": "排除出候选池",
    "data_quality_failed": "数据质量失败",
    "end_date_coverage_gap": "行情日期覆盖缺口",
    "candidate_gate": "候选池门禁",
    "governance_coverage": "治理覆盖",
    "benchmark_dependency": "基准指数依赖",
    "metadata_dependency": "元数据依赖",
    "fund_size_dependency": "基金规模依赖",
    "management_fee_dependency": "管理费依赖",
    "low_liquidity_watch": "低流动性观察",
    "observation_only": "仅观察",
    "waiting": "等待历史数据补足",
    "waiting_for_history": "等待历史数据补足",
    "eligible": "可进入候选池",
    "candidate_pool_blocked": "候选池阻断",
    "covered_not_cleared": "已覆盖但未放行",
    "candidate_eligible_count": "候选池无可用 ETF",
    "factor_gate_status": "因子门禁阻断",
    "min_computable_ratio": "可评分比例不足",
    "max_unable_to_score_ratio": "不可评分比例过高",
    "short_history_bias": "历史数据不足偏差",
    "discount_premium_dependency": "折溢价数据不可用",
    "metadata_dependency": "元数据覆盖不足",
    "tracking_error_dependency": "跟踪误差依赖未满足",
    "relative_return_dependency": "相对收益依赖未满足",
    "factor_coverage_minimum": "因子覆盖不足",
    "manual_review_required": "需要人工复核",
    "buy_candidate": "买入候选",
    "watch_candidate": "观察候选",
    "avoid": "暂不考虑",
    "data_blocked": "数据不足，不能判断",
    "True": "是",
    "False": "否",
    "true": "是",
    "false": "否",
}

ACTION_LABELS = {
    "keep excluded and observe until sufficient history": "继续排除，等待历史数据补足",
    "run update-data only in controlled environment or diagnose source lag": "仅在受控环境刷新数据，或先诊断数据源滞后",
    "diagnose source lag for 560000; keep blocked": "诊断 560000 数据源滞后，继续阻断",
    "diagnose source lag for 560000; keep blocked; do not run full-market refresh for this alone": "诊断 560000 数据源滞后，继续阻断；不要为该单标的执行全市场刷新",
    "keep blocked; diagnose provider/source lag; do not run full-market refresh for this alone": "继续阻断，诊断数据源滞后；不要为该单标的执行全市场刷新",
    "complete manual review, do not auto unblock": "完成人工复核前不得自动解除阻断",
    "keep blocked rows out of production candidate pool": "继续阻断，不进入正式候选池",
    "do not enter 008B": "不进入 008B",
    "diagnose-index-source": "诊断指数数据源",
    "update-index-data": "更新指数数据",
    "compute-etf-metrics": "计算 ETF 指标",
    "clear short-history/manual-review/no-used-factor blockers, then rerun build-candidate-gate": "清理历史不足、人工复核和无可用因子阻断后，再重新生成候选池门禁",
    "complete manual review first, then rerun candidate gate without auto-clearing blocks": "先完成人工复核，再重新运行候选池门禁；不得自动解除阻断",
    "manual price-quality review before any strategy use or refresh acceptance": "进入策略使用或接受刷新结果前，先完成人工价格质量复核",
    "manual review required before candidate gate reconsideration": "重新评估候选池前必须完成人工复核",
    "Treat no_used_factors as unscoreable, not as a low score.": "将无可用因子视为暂不可评分，而不是低分。",
    "Keep factor score as an observation report until broad enough coverage exists.": "在覆盖率足够前，多因子评分仅作为观察报告。",
    "Require enough price history before producing an independent candidate strategy.": "形成独立候选策略前，必须先满足价格历史长度要求。",
    "discount_premium_dependency": "折溢价数据不可用",
    "tracking_error_dependency": "跟踪误差依赖未满足",
    "relative_return_dependency": "相对收益依赖未满足",
    "discount_premium": "折溢价",
    "tracking_error": "跟踪误差",
    "relative_return": "相对收益",
    "no_used_factors": "无可用因子",
    "factor_score_gate": "因子评分门禁",
    "candidate_gate": "候选池门禁",
    "index_cache": "指数缓存",
    "source_unavailable": "数据源不可用",
    "provider_stale": "数据源停更或滞后",
    "source_lag_blocker": "数据源滞后阻断",
}

TOKEN_SEPARATORS = (";", "|", ",")

QA_ACTIONABILITY_OPTIONS = [
    ("全部", None),
    ("已纳入治理", "already_governed"),
    ("治理门禁阻断", "governance_blocked"),
    ("需要人工复核", "manual_review"),
    ("需要受控刷新", "refresh_needed"),
    ("等待历史数据补足", "wait_for_history"),
    ("数据源不可用", "source_unavailable"),
]

QA_BLOCK_SCOPE_OPTIONS = [
    ("全部", "all"),
    ("阻断 007B", "007b"),
    ("阻断 008B", "008b"),
    ("同时阻断 007B 和 008B", "both"),
    ("不阻断", "none"),
]

CANDIDATE_STATUS_OPTIONS = [
    ("全部", None),
    ("可进入候选池", "eligible"),
    ("历史不足阻断", "blocked_short_history"),
    ("人工复核阻断", "blocked_manual_review"),
    ("无可用因子阻断", "blocked_no_used_factors"),
    ("因子门禁阻断", "factor_gate_blocked"),
    ("观察中", "observation_only"),
]

OBSERVATION_STATUS_OPTIONS = [
    ("全部", None),
    ("等待历史数据补足", "waiting_for_history"),
    ("极短历史", "very_short_history"),
    ("低流动性观察", "low_liquidity_watch"),
    ("人工复核", "manual_review_required"),
]

DOWNLOAD_LABELS = {
    "data_governance_status.json": "数据治理状态 JSON",
    "qa_report.json": "QA 总览 JSON",
    "qa_status_breakdown.csv": "QA 分层报告",
    "data_quality_diagnosis.csv": "数据质量诊断报告",
    "short_history_observation_pool.csv": "短历史观察池",
    "manual_review_list.csv": "人工复核清单",
    "candidate_gate.csv": "候选池门禁报告",
    "candidate_unblock_plan.csv": "候选池解除路径",
    "etf_007b_metrics_report.csv": "007B 小范围指标报告",
    "etf_007b_metrics_summary.csv": "007B 指标摘要",
    "index_007b_readiness.csv": "007B 指数准备度",
    "factor_008b_readiness.csv": "008B 准入检查报告",
    "factor_score_gate.csv": "因子评分门禁报告",
    "factor_score_audit.csv": "因子评分审计报告",
}

REPORT_GROUPS = {
    "总览报告": ["data_governance_status.json", "qa_report.json"],
    "QA / 数据治理": [
        "qa_status_breakdown.csv",
        "data_quality_diagnosis.csv",
        "short_history_observation_pool.csv",
        "manual_review_list.csv",
    ],
    "候选池": ["candidate_gate.csv", "candidate_unblock_plan.csv"],
    "007B": ["etf_007b_metrics_report.csv", "etf_007b_metrics_summary.csv", "index_007b_readiness.csv"],
    "008B": ["factor_008b_readiness.csv", "factor_score_gate.csv", "factor_score_audit.csv"],
}


def _project_path(project_root: Path | str, relative: str) -> Path:
    return Path(project_root) / relative


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


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


def _safe_float(value: Any) -> float | None:
    try:
        if value in ("", None) or pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _value_counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    if frame.empty or column not in frame.columns:
        return {}
    return {str(key): int(value) for key, value in frame[column].astype(str).value_counts(dropna=False).items()}


def _missing_columns(frame: pd.DataFrame, required_columns: list[str] | tuple[str, ...]) -> list[str]:
    return [column for column in required_columns if column not in frame.columns]


def _read_rows(frame: pd.DataFrame, status_column: str, status_value: str, limit: int = 200) -> pd.DataFrame:
    if frame.empty or status_column not in frame.columns:
        return pd.DataFrame()
    return frame[frame[status_column].astype(str).eq(status_value)].head(limit).copy()


def safe_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    return data.get(key, default) if isinstance(data, dict) else default


def load_json_report(path: Path | str) -> dict[str, Any]:
    report_path = Path(path)
    if not report_path.exists():
        return {"exists": False, "path": str(report_path), "data": {}, "warnings": [f"missing report: {report_path}"]}
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "path": str(report_path), "data": {}, "warnings": [f"failed to read JSON report {report_path}: {exc}"]}
    return {"exists": True, "path": str(report_path), "data": data if isinstance(data, dict) else {}, "warnings": []}


def load_csv_report(path: Path | str, required_columns: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    report_path = Path(path)
    if not report_path.exists():
        return {"exists": False, "path": str(report_path), "frame": pd.DataFrame(), "warnings": [f"missing report: {report_path}"]}
    try:
        frame = pd.read_csv(report_path, dtype=str, encoding="utf-8-sig").fillna("")
    except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        return {"exists": True, "path": str(report_path), "frame": pd.DataFrame(), "warnings": [f"failed to read CSV report {report_path}: {exc}"]}
    warnings: list[str] = []
    missing = _missing_columns(frame, required_columns or [])
    if missing:
        warnings.append(f"{report_path.name} missing columns: {', '.join(missing)}")
    return {"exists": True, "path": str(report_path), "frame": frame, "warnings": warnings}


def format_bool_status(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    text = str(value).strip()
    if text.lower() in {"true", "1", "yes"}:
        return "是"
    if text.lower() in {"false", "0", "no"}:
        return "否"
    return format_status(text)


def format_status(value: Any) -> str:
    if isinstance(value, bool):
        return format_bool_status(value)
    text = "" if value is None else str(value).strip()
    if not text:
        return "未知"
    return STATUS_LABELS.get(text, text)


def format_field_label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name)


def format_action(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return "—"
    if text in ACTION_LABELS:
        return ACTION_LABELS[text]
    result = text
    for raw, label in sorted(ACTION_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(raw, label)
    for raw, label in sorted(STATUS_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(raw, label)
    result = result.replace(" -> ", " → ").replace(" / ", " / ")
    return result


def format_display_value(value: Any) -> str:
    if isinstance(value, bool):
        return format_bool_status(value)
    text = "" if value is None else str(value).strip()
    if not text:
        return "—"
    if text in ACTION_LABELS:
        return format_action(text)
    if text in STATUS_LABELS:
        return format_status(text)
    if any(sep in text for sep in TOKEN_SEPARATORS):
        result = text
        for sep in TOKEN_SEPARATORS:
            if sep in result:
                parts = [format_display_value(part.strip()) for part in result.split(sep) if part.strip()]
                return "；".join(parts) if parts else "—"
    return format_action(text)


def format_percent(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "—"
    return f"{number * 100:.{digits}f}%"


def format_decimal(value: Any, digits: int = 4) -> str:
    number = _safe_float(value)
    if number is None:
        return "—"
    return f"{number:.{digits}f}"


def localize_dataframe_values(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    for column in out.columns:
        if column in {"blocking", "blocks_007b", "blocks_008b", "requires_manual_review", "low_liquidity_flag", "full_scope_available"}:
            out[column] = out[column].map(format_bool_status)
        elif column in {
            "recommended_action",
            "remediation_action",
            "suggested_action",
            "recommended_checks",
            "notes",
            "failure_reason",
            "finding",
            "actual_value",
            "threshold",
            "dependency",
            "prerequisite_task",
            "estimated_path",
        }:
            out[column] = out[column].map(format_action)
        elif column.endswith("status") or column in {
            "qa_item",
            "candidate_status",
            "block_reason",
            "observation_reason",
            "factor_gate_status",
            "review_priority",
            "manual_review_reason",
            "actionability",
            "root_cause",
            "readiness_item",
            "gate_item",
            "audit_item",
            "blocker_type",
        }:
            out[column] = out[column].map(format_display_value)
    return out


def summarize_governance_status(project_root: Path | str) -> dict[str, Any]:
    status_report = load_json_report(_project_path(project_root, "output/data_governance_status.json"))
    qa_report = load_json_report(_project_path(project_root, "output/qa_report.json"))
    status = status_report["data"]
    qa = qa_report["data"]
    warnings = [*status_report["warnings"], *qa_report["warnings"]]
    data_layer = safe_get(qa, "data_layer", {})
    strategy_layer = safe_get(qa, "strategy_layer", {})
    output_layer = safe_get(qa, "output_layer", {})
    required = [
        "allowed_to_enter_007b",
        "allowed_to_enter_007b_scope",
        "allowed_to_enter_008b",
        "candidate_eligible_count",
        "candidate_blocked_count",
        "data_quality_failed_count",
        "end_date_coverage_gap_days",
        "manual_review_count",
        "factor_gate_status",
    ]
    missing = [key for key in required if key not in status]
    if missing:
        warnings.append("data_governance_status.json missing fields: " + ", ".join(missing))
    overall_project_status = "governed_blocked"
    if status.get("allowed_to_enter_008b") is True:
        overall_project_status = "008b_available"
    return {
        "warnings": warnings,
        "raw": status,
        "qa": qa,
        "overall_project_status": overall_project_status,
        "allowed_to_enter_007b": bool(status.get("allowed_to_enter_007b", False)),
        "allowed_to_enter_007b_scope": str(status.get("allowed_to_enter_007b_scope", "blocked")),
        "allowed_to_enter_008b": bool(status.get("allowed_to_enter_008b", False)),
        "candidate_eligible_count": _safe_int(status.get("candidate_eligible_count")),
        "candidate_blocked_count": _safe_int(status.get("candidate_blocked_count")),
        "data_quality_failed_count": _safe_int(status.get("data_quality_failed_count")),
        "end_date_coverage_gap_days": _safe_int(status.get("end_date_coverage_gap_days")),
        "manual_review_count": _safe_int(status.get("manual_review_count")),
        "factor_gate_status": str(status.get("factor_gate_status", "unknown")),
        "next_recommended_action": str(status.get("next_recommended_action", "")),
        "data_layer_passed": bool(safe_get(data_layer, "passed", False)),
        "strategy_layer_passed": bool(safe_get(strategy_layer, "passed", False)),
        "output_layer_passed": bool(safe_get(output_layer, "passed", False)),
        "blocking_reasons": list(status.get("blocking_reasons") or qa.get("blocking_reasons") or []),
        "etf_007b_computable_count": _safe_int(status.get("etf_007b_computable_count")),
        "etf_007b_full_scope_available": bool(status.get("etf_007b_full_scope_available", status.get("index_007b_full_scope_available", False))),
        "factor_008b_readiness_status": str(status.get("factor_008b_readiness_status", "unknown")),
        "source_lag_symbols": list(status.get("source_lag_symbols") or []),
        "source_lag_blocker_symbols": list(status.get("source_lag_blocker_symbols") or []),
        "source_lag_blocker_count": _safe_int(status.get("source_lag_blocker_count")),
        "next_source_lag_action": str(status.get("next_source_lag_action", "")),
        "blocked_short_history_count": _safe_int(status.get("blocked_short_history_count")),
        "blocked_manual_review_count": _safe_int(status.get("blocked_manual_review_count")),
        "blocked_no_used_factors_count": _safe_int(status.get("blocked_no_used_factors_count")),
        "very_short_history_count": _safe_int(status.get("very_short_history_count")),
        "estimated_eligible_within_20d_count": _safe_int(status.get("estimated_eligible_within_20d_count")),
        "estimated_eligible_within_60d_count": _safe_int(status.get("estimated_eligible_within_60d_count")),
    }


def get_governance_status(project_root: Path | str) -> dict[str, Any]:
    return summarize_governance_status(project_root)


def get_qa_status(project_root: Path | str) -> dict[str, Any]:
    breakdown_report = load_csv_report(
        _project_path(project_root, "output/qa_status_breakdown.csv"),
        ["qa_item", "actionability", "root_cause", "blocking", "blocks_007b", "blocks_008b", "recommended_action"],
    )
    summary_report = load_csv_report(_project_path(project_root, "output/qa_status_summary.csv"))
    breakdown = breakdown_report["frame"]
    return {
        "warnings": [*breakdown_report["warnings"], *summary_report["warnings"]],
        "breakdown": breakdown,
        "summary": summary_report["frame"],
        "blocking_count": int(breakdown["blocking"].map(_is_true).sum()) if "blocking" in breakdown.columns else 0,
        "actionability_counts": _value_counts(breakdown, "actionability"),
        "root_cause_counts": _value_counts(breakdown, "root_cause"),
    }


def summarize_candidate_gate(project_root: Path | str) -> dict[str, Any]:
    gate_report = load_csv_report(
        _project_path(project_root, "output/candidate_gate.csv"),
        ["symbol", "name", "candidate_status", "eligibility_status", "block_reason", "factor_gate_status"],
    )
    unblock_report = load_csv_report(
        _project_path(project_root, "output/candidate_unblock_plan.csv"),
        ["symbol", "name", "unblock_status", "next_action", "can_be_unblocked_by_benchmark_update"],
    )
    gate = gate_report["frame"]
    status_counts = _value_counts(gate, "candidate_status")
    eligible = int((gate["eligibility_status"].astype(str) == "eligible").sum()) if "eligibility_status" in gate.columns else 0
    blocked = int((gate["eligibility_status"].astype(str) == "blocked").sum()) if "eligibility_status" in gate.columns else 0
    observation_only = int(gate["candidate_status"].astype(str).str.contains("observation", case=False, na=False).sum()) if "candidate_status" in gate.columns else 0
    return {
        "warnings": [*gate_report["warnings"], *unblock_report["warnings"]],
        "gate": gate,
        "unblock_plan": unblock_report["frame"],
        "eligible_count": eligible,
        "blocked_count": blocked,
        "observation_only_count": observation_only,
        "blocked_short_history_count": status_counts.get("blocked_short_history", 0),
        "blocked_manual_review_count": status_counts.get("blocked_manual_review", 0),
        "blocked_no_used_factors_count": status_counts.get("blocked_no_used_factors", 0),
        "factor_gate_blocked_count": int((gate["factor_gate_status"].astype(str) == "blocked_for_strategy_use").sum()) if "factor_gate_status" in gate.columns else 0,
        "status_counts": status_counts,
    }


def get_candidate_gate_summary(project_root: Path | str) -> dict[str, Any]:
    return summarize_candidate_gate(project_root)


def summarize_007b(project_root: Path | str) -> dict[str, Any]:
    report = load_csv_report(
        _project_path(project_root, "output/etf_007b_metrics_report.csv"),
        ["symbol", "name", "tracking_index_code", "tracking_index_name", "tracking_error", "relative_return_20d", "relative_return_60d", "relative_return_120d", "validation_status"],
    )
    summary_report = load_csv_report(_project_path(project_root, "output/etf_007b_metrics_summary.csv"), ["summary_item", "count"])
    readiness_report = load_csv_report(_project_path(project_root, "output/index_007b_readiness.csv"), ["readiness_item", "current_status", "blocking", "actual_value"])
    governance = get_governance_status(project_root)
    frame = report["frame"]
    computed = _read_rows(frame, "validation_status", "computed_valid")
    no_index_cache = _read_rows(frame, "validation_status", "no_index_cache")
    missing_benchmark = _read_rows(frame, "validation_status", "missing_benchmark")
    return {
        "warnings": [*report["warnings"], *summary_report["warnings"], *readiness_report["warnings"], *governance["warnings"]],
        "report": frame,
        "summary": summary_report["frame"],
        "readiness": readiness_report["frame"],
        "computed_valid": computed,
        "no_index_cache": no_index_cache,
        "missing_benchmark": missing_benchmark,
        "computed_valid_count": len(computed),
        "no_index_cache_count": len(no_index_cache),
        "missing_benchmark_count": len(missing_benchmark),
        "allowed_to_enter_007b_scope": governance["allowed_to_enter_007b_scope"],
        "full_scope_available": governance["etf_007b_full_scope_available"],
    }


def get_007b_summary(project_root: Path | str) -> dict[str, Any]:
    return summarize_007b(project_root)


def summarize_008b(project_root: Path | str) -> dict[str, Any]:
    readiness_report = load_csv_report(
        _project_path(project_root, "output/factor_008b_readiness.csv"),
        ["readiness_item", "current_status", "blocking", "actual_value", "remediation_action"],
    )
    gate_report = load_csv_report(_project_path(project_root, "output/factor_score_gate.csv"), ["gate_item", "status", "blocking", "actual_value"])
    audit_report = load_csv_report(_project_path(project_root, "output/factor_score_audit.csv"), ["audit_item", "status", "count", "finding"])
    governance = get_governance_status(project_root)
    readiness = readiness_report["frame"]
    blockers = readiness[readiness["blocking"].map(_is_true)].copy() if "blocking" in readiness.columns else pd.DataFrame()
    return {
        "warnings": [*readiness_report["warnings"], *gate_report["warnings"], *audit_report["warnings"], *governance["warnings"]],
        "readiness": readiness,
        "factor_gate": gate_report["frame"],
        "factor_audit": audit_report["frame"],
        "blockers": blockers,
        "readiness_status": governance["factor_008b_readiness_status"],
        "allowed_to_enter_008b": governance["allowed_to_enter_008b"],
        "candidate_eligible_count": governance["candidate_eligible_count"],
        "factor_gate_status": governance["factor_gate_status"],
    }


def get_008b_summary(project_root: Path | str) -> dict[str, Any]:
    return summarize_008b(project_root)


def summarize_manual_review(project_root: Path | str) -> dict[str, Any]:
    observation_report = load_csv_report(
        _project_path(project_root, "output/short_history_observation_pool.csv"),
        ["symbol", "name", "observation_status", "estimated_trading_days_until_eligible", "requires_manual_review", "low_liquidity_flag"],
    )
    manual_report = load_csv_report(
        _project_path(project_root, "output/manual_review_list.csv"),
        ["symbol", "name", "manual_review_reason", "recommended_checks", "review_status"],
    )
    observation = observation_report["frame"]
    manual = manual_report["frame"]
    estimates = pd.to_numeric(observation.get("estimated_trading_days_until_eligible", pd.Series(dtype=str)), errors="coerce")
    return {
        "warnings": [*observation_report["warnings"], *manual_report["warnings"]],
        "observation_pool": observation,
        "manual_review": manual,
        "observation_total": len(observation),
        "very_short_history_count": int((observation["history_status"].astype(str) == "very_short_history").sum()) if "history_status" in observation.columns else 0,
        "low_liquidity_watch_count": int(observation["low_liquidity_flag"].map(_is_true).sum()) if "low_liquidity_flag" in observation.columns else 0,
        "estimated_eligible_within_20d": int(estimates.le(20).sum()) if not observation.empty else 0,
        "estimated_eligible_within_60d": int(estimates.le(60).sum()) if not observation.empty else 0,
        "unknown_estimate_count": int(estimates.isna().sum()) if not observation.empty else 0,
        "manual_review_count": len(manual),
    }


def get_manual_review_summary(project_root: Path | str) -> dict[str, Any]:
    return summarize_manual_review(project_root)


def get_report_downloads(project_root: Path | str) -> dict[str, list[dict[str, Any]]]:
    root = Path(project_root)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for group, filenames in REPORT_GROUPS.items():
        rows: list[dict[str, Any]] = []
        for filename in filenames:
            path = root / "output" / filename
            rows.append(
                {
                    "filename": filename,
                    "display_name": DOWNLOAD_LABELS.get(filename, filename),
                    "relative_path": f"output/{filename}",
                    "path": str(path),
                    "exists": path.exists(),
                    "size_bytes": path.stat().st_size if path.exists() else 0,
                }
            )
        grouped[group] = rows
    return grouped
