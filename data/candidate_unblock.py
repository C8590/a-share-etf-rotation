from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


CANDIDATE_UNBLOCK_PLAN_COLUMNS = [
    "symbol",
    "name",
    "current_candidate_status",
    "current_block_reason",
    "unblock_path",
    "unblock_status",
    "unblock_priority",
    "required_conditions",
    "waiting_condition",
    "manual_review_condition",
    "benchmark_condition",
    "factor_gate_condition",
    "metadata_condition",
    "liquidity_condition",
    "estimated_earliest_review_date",
    "can_be_unblocked_by_waiting",
    "can_be_unblocked_by_manual_review",
    "can_be_unblocked_by_refresh",
    "can_be_unblocked_by_benchmark_update",
    "still_blocked_after_primary_fix",
    "next_action",
    "notes",
]

CANDIDATE_UNBLOCK_SUMMARY_COLUMNS = [
    "unblock_item",
    "count",
    "ratio",
    "severity",
    "finding",
    "suggested_action",
    "examples",
    "notes",
]

UNBLOCK_PATH_VALUES = {
    "wait_for_history",
    "manual_review_required",
    "source_lag_blocker",
    "factor_gate_blocked",
    "no_used_factors",
    "benchmark_dependency_missing",
    "metadata_dependency_missing",
    "liquidity_watch",
    "unknown",
}

UNBLOCK_STATUS_VALUES = {
    "not_ready",
    "waiting",
    "requires_manual_review",
    "requires_data_dependency",
    "requires_factor_gate_pass",
    "eligible_after_conditions",
    "unknown",
}


def _read_csv(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    return pd.read_csv(file_path, dtype={"symbol": str}, encoding="utf-8-sig").fillna("")


def _read_json(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int(value: Any, default: int = 0) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return int(float(parsed))


def _ratio(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total, 6)


def _examples(frame: pd.DataFrame, mask: pd.Series, limit: int = 5) -> str:
    if frame.empty or "symbol" not in frame.columns:
        return ""
    parts: list[str] = []
    for row in frame.loc[mask].head(limit).to_dict("records"):
        parts.append(f"{str(row.get('symbol', '')).zfill(6)} {_text(row.get('name'))}".strip())
    return ";".join(parts)


def _by_symbol(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    return {
        str(row.get("symbol", "")).zfill(6): row
        for row in frame.to_dict("records")
        if _text(row.get("symbol"))
    }


def _factor_gate_blocked(factor_gate: pd.DataFrame, governance: dict[str, Any], qa_report: dict[str, Any]) -> bool:
    status = _text(governance.get("factor_gate_status"))
    if not status:
        factor_summary = qa_report.get("strategy_layer", {}).get("factor_score", {})
        if isinstance(factor_summary, dict):
            status = _text(factor_summary.get("gate_status"))
    if status == "blocked_for_strategy_use":
        return True
    if factor_gate.empty:
        return False
    blocking = factor_gate.get("blocking", pd.Series(dtype=object)).astype(str).str.lower().isin(["true", "1", "yes"])
    blocked = factor_gate.get("status", pd.Series(dtype=object)).astype(str).eq("blocked")
    return bool((blocking & blocked).any())


def _benchmark_missing(index_coverage: pd.DataFrame, governance: dict[str, Any], qa_report: dict[str, Any]) -> bool:
    data_layer = qa_report.get("data_layer", {}) if isinstance(qa_report.get("data_layer"), dict) else {}
    index_summary = data_layer.get("index_data", {}) if isinstance(data_layer.get("index_data"), dict) else {}
    usable = _int(index_summary.get("usable_benchmark_count"), -1)
    if usable >= 0:
        return usable <= 0
    if "allowed_to_enter_007b" in governance:
        return not bool(governance.get("allowed_to_enter_007b"))
    if index_coverage.empty or "usable_as_benchmark" not in index_coverage.columns:
        return False
    return not bool(index_coverage["usable_as_benchmark"].astype(str).str.lower().isin(["true", "1", "yes"]).any())


def estimate_unblock_conditions(
    *,
    candidate_row: dict[str, Any],
    diagnosis_row: dict[str, Any] | None = None,
    observation_row: dict[str, Any] | None = None,
    manual_review_row: dict[str, Any] | None = None,
    factor_row: dict[str, Any] | None = None,
    benchmark_missing: bool = False,
    global_factor_gate_blocked: bool = False,
) -> dict[str, Any]:
    diagnosis = diagnosis_row or {}
    observation = observation_row or {}
    manual = manual_review_row or {}
    factor = factor_row or {}
    status = _text(candidate_row.get("candidate_status") or candidate_row.get("current_candidate_status"))
    block_reason = _text(candidate_row.get("block_reason") or candidate_row.get("current_block_reason"))
    row_count = _int(diagnosis.get("row_count") or observation.get("row_count"), 0)
    min_rows = _int(diagnosis.get("min_required_rows") or observation.get("min_required_rows"), 250)
    rows_needed = _int(observation.get("rows_needed") or max(min_rows - row_count, 0), 0)
    estimated_date = _text(observation.get("estimated_calendar_date_until_eligible"))
    low_liquidity = (
        _bool(observation.get("low_liquidity_flag"))
        or _bool(manual.get("low_liquidity_flag"))
        or _text(candidate_row.get("observation_reason")).find("low_liquidity") >= 0
        or _text(candidate_row.get("liquidity_status")) == "low_liquidity"
    )
    manual_required = _bool(candidate_row.get("requires_manual_review")) or bool(manual) or status == "blocked_manual_review"
    no_used_factors = status == "blocked_no_used_factors" or _text(factor.get("score_status")) == "no_used_factors"

    required: list[str] = []
    waiting_condition = ""
    manual_condition = ""
    benchmark_condition = ""
    factor_gate_condition = ""
    metadata_condition = ""
    liquidity_condition = ""

    if status in {"blocked_short_history", "blocked_manual_review"} or "short_history" in block_reason:
        waiting_condition = f"row_count >= min_required_rows ({row_count}/{min_rows}; rows_needed={rows_needed})"
        required.append(waiting_condition)
    if manual_required:
        manual_reason = _text(manual.get("manual_review_reason") or diagnosis.get("secondary_failure_type") or "abnormal_return / low_liquidity / very_short_history evidence")
        manual_condition = f"manual review confirms or rejects: {manual_reason}"
        required.append(manual_condition)
    if no_used_factors:
        required.append("at least one enabled factor produces usable evidence")
        if benchmark_missing:
            benchmark_condition = "schema-valid benchmark/index cache is available; run update-index-data only in controlled environment"
            required.append(benchmark_condition)
        if "metadata" in _text(factor.get("notes")).lower() or _text(factor.get("source")).lower() == "metadata":
            metadata_condition = "metadata enrichment confirms enabled metadata factor coverage"
            required.append(metadata_condition)
    if global_factor_gate_blocked:
        factor_gate_condition = "factor_gate_status != blocked_for_strategy_use"
        required.append(factor_gate_condition)
    if benchmark_missing and not benchmark_condition:
        benchmark_condition = "usable_benchmark_count > 0 before 007B or benchmark-dependent factor promotion"
    if low_liquidity:
        liquidity_condition = "keep liquidity watch until tradability threshold is met"
        required.append(liquidity_condition)

    return {
        "required_conditions": "; ".join(dict.fromkeys([item for item in required if item])),
        "waiting_condition": waiting_condition,
        "manual_review_condition": manual_condition,
        "benchmark_condition": benchmark_condition,
        "factor_gate_condition": factor_gate_condition,
        "metadata_condition": metadata_condition,
        "liquidity_condition": liquidity_condition,
        "estimated_earliest_review_date": estimated_date,
        "can_be_unblocked_by_waiting": bool(waiting_condition and not manual_required),
        "can_be_unblocked_by_manual_review": bool(manual_required),
        "can_be_unblocked_by_refresh": False,
        "can_be_unblocked_by_benchmark_update": bool(benchmark_condition and no_used_factors),
    }


def classify_unblock_path(
    *,
    candidate_row: dict[str, Any],
    factor_row: dict[str, Any] | None = None,
    benchmark_missing: bool = False,
) -> tuple[str, str, str, str]:
    status = _text(candidate_row.get("candidate_status") or candidate_row.get("current_candidate_status"))
    factor_status = _text((factor_row or {}).get("score_status"))
    if status == "blocked_manual_review" or _bool(candidate_row.get("requires_manual_review")):
        return (
            "manual_review_required",
            "requires_manual_review",
            "P0_manual_review",
            "complete manual review, do not auto unblock",
        )
    if status == "blocked_short_history":
        return (
            "wait_for_history",
            "waiting",
            "P1_wait_for_history",
            "keep excluded and rerun candidate gate after minimum history is reached",
        )
    if "source_lag" in _text(candidate_row.get("block_reason") or candidate_row.get("current_block_reason")):
        return (
            "source_lag_blocker",
            "requires_data_dependency",
            "P0_source_lag",
            "keep blocked; diagnose provider/source lag; do not run full-market refresh for this alone",
        )
    if status == "blocked_no_used_factors" or factor_status == "no_used_factors":
        if benchmark_missing:
            return (
                "benchmark_dependency_missing",
                "requires_data_dependency",
                "P1_benchmark_dependency",
                "fix benchmark/index cache dependency before treating no_used_factors as resolvable",
            )
        return (
            "no_used_factors",
            "requires_data_dependency",
            "P1_factor_coverage",
            "restore at least one enabled usable factor; do not treat as low score",
        )
    if status == "blocked_factor_gate":
        return (
            "factor_gate_blocked",
            "requires_factor_gate_pass",
            "P0_factor_gate",
            "keep out of 008B until factor gate passes",
        )
    if _text(candidate_row.get("observation_reason")).find("low_liquidity") >= 0:
        return ("liquidity_watch", "not_ready", "P2_liquidity_watch", "observe tradability before candidate use")
    if status == "eligible":
        return (
            "factor_gate_blocked",
            "requires_factor_gate_pass",
            "P0_factor_gate",
            "eligible only after global factor gate and QA are clean",
        )
    return ("unknown", "unknown", "P2_review", "review inputs before any candidate use")


def build_candidate_unblock_plan(
    *,
    output_dir: str | Path = "output",
    candidate_gate: pd.DataFrame | None = None,
    diagnosis: pd.DataFrame | None = None,
    observation_pool: pd.DataFrame | None = None,
    manual_review: pd.DataFrame | None = None,
    factor_score: pd.DataFrame | None = None,
    factor_gate: pd.DataFrame | None = None,
    etf_metrics_coverage: pd.DataFrame | None = None,
    index_coverage: pd.DataFrame | None = None,
    data_governance_status: dict[str, Any] | None = None,
    qa_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    candidate_frame = candidate_gate if candidate_gate is not None else _read_csv(output_path / "candidate_gate.csv")
    diagnosis_frame = diagnosis if diagnosis is not None else _read_csv(output_path / "data_quality_diagnosis.csv")
    observation_frame = observation_pool if observation_pool is not None else _read_csv(output_path / "short_history_observation_pool.csv")
    manual_frame = manual_review if manual_review is not None else _read_csv(output_path / "manual_review_list.csv")
    factor_frame = factor_score if factor_score is not None else _read_csv(output_path / "factor_score_report.csv")
    factor_gate_frame = factor_gate if factor_gate is not None else _read_csv(output_path / "factor_score_gate.csv")
    metrics_coverage_frame = etf_metrics_coverage if etf_metrics_coverage is not None else _read_csv(output_path / "etf_metrics_coverage.csv")
    index_frame = index_coverage if index_coverage is not None else _read_csv(output_path / "index_data_coverage.csv")
    governance = data_governance_status if data_governance_status is not None else _read_json(output_path / "data_governance_status.json")
    qa = qa_report if qa_report is not None else _read_json(output_path / "qa_report.json")

    diagnosis_by_symbol = _by_symbol(diagnosis_frame)
    observation_by_symbol = _by_symbol(observation_frame)
    manual_by_symbol = _by_symbol(manual_frame)
    factor_by_symbol = _by_symbol(factor_frame)
    global_factor_blocked = _factor_gate_blocked(factor_gate_frame, governance, qa)
    benchmark_missing = _benchmark_missing(index_frame, governance, qa)
    benchmark_metrics_missing = False
    if not metrics_coverage_frame.empty and "main_failure_reason" in metrics_coverage_frame.columns:
        benchmark_metrics_missing = bool(metrics_coverage_frame["main_failure_reason"].astype(str).eq("missing_benchmark").any())
    benchmark_dependency_missing = benchmark_missing or benchmark_metrics_missing

    rows: list[dict[str, Any]] = []
    for candidate in candidate_frame.to_dict("records"):
        symbol = str(candidate.get("symbol", "")).zfill(6)
        factor = factor_by_symbol.get(symbol, {})
        path, status, priority, next_action = classify_unblock_path(
            candidate_row=candidate,
            factor_row=factor,
            benchmark_missing=benchmark_dependency_missing,
        )
        conditions = estimate_unblock_conditions(
            candidate_row=candidate,
            diagnosis_row=diagnosis_by_symbol.get(symbol),
            observation_row=observation_by_symbol.get(symbol),
            manual_review_row=manual_by_symbol.get(symbol),
            factor_row=factor,
            benchmark_missing=benchmark_dependency_missing,
            global_factor_gate_blocked=global_factor_blocked,
        )
        still_blocked = bool(global_factor_blocked or conditions["manual_review_condition"] or conditions["factor_gate_condition"])
        if path == "wait_for_history":
            still_blocked = bool(global_factor_blocked or conditions["factor_gate_condition"] or conditions["liquidity_condition"])
        notes = [
            "plan only; does not clear candidate gate",
            "no_used_factors is unscoreable evidence, not a low score" if path in {"no_used_factors", "benchmark_dependency_missing"} else "",
            "source lag blocker; keep blocked; not fixable by ordinary refresh" if path == "source_lag_blocker" else "",
            "benchmark dependency also keeps 007B blocked" if benchmark_dependency_missing else "",
        ]
        row = {
            "symbol": symbol,
            "name": _text(candidate.get("name")),
            "current_candidate_status": _text(candidate.get("candidate_status")),
            "current_block_reason": _text(candidate.get("block_reason")),
            "unblock_path": path if path in UNBLOCK_PATH_VALUES else "unknown",
            "unblock_status": status if status in UNBLOCK_STATUS_VALUES else "unknown",
            "unblock_priority": priority,
            **conditions,
            "still_blocked_after_primary_fix": bool(still_blocked),
            "next_action": next_action,
            "notes": "; ".join([item for item in notes if item]),
        }
        rows.append({column: row.get(column, "") for column in CANDIDATE_UNBLOCK_PLAN_COLUMNS})
    return rows


def _summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    total = max(1, len(frame))
    bool_col = lambda name: frame[name].astype(str).str.lower().isin(["true", "1", "yes"])
    specs = [
        ("total", pd.Series([True] * len(frame), index=frame.index), "info", "Candidate unblock plan covers all candidate gate rows.", "use as a plan only; do not mark eligible from this report"),
        ("immediate_eligible", frame["unblock_status"].eq("eligible_after_conditions"), "info", "Rows that are immediately eligible after current conditions.", "none while QA and factor gate remain blocked"),
        ("wait_for_history", frame["unblock_path"].eq("wait_for_history"), "high", "Rows can only move by accumulating sufficient history.", "keep excluded and rerun candidate gate after minimum history"),
        ("manual_review_required", frame["unblock_path"].eq("manual_review_required"), "high", "Rows require manual price/source/liquidity review.", "complete manual review; do not auto unblock"),
        ("source_lag_blocker", frame["unblock_path"].eq("source_lag_blocker"), "high", "Rows are blocked by single-symbol source lag or provider staleness.", "diagnose source lag; do not run full-market refresh for this alone"),
        ("no_used_factors", frame["current_candidate_status"].eq("blocked_no_used_factors"), "high", "Rows are unscoreable because no enabled factor is usable.", "fix factor dependencies; never treat as low score"),
        ("benchmark_dependency_missing", bool_col("can_be_unblocked_by_benchmark_update"), "high", "Benchmark/index dependency is missing for relevant factor paths.", "run diagnose-index-source and update-index-data in controlled environment"),
        ("factor_gate_blocked", bool_col("still_blocked_after_primary_fix"), "high", "Rows remain blocked after primary row-level fix while global gates are blocked.", "do not enter 008B until factor gate passes"),
        ("liquidity_watch", frame["liquidity_condition"].astype(str).ne(""), "medium", "Low-liquidity rows need tradability observation.", "keep liquidity watch visible before candidate use"),
    ]
    out: list[dict[str, Any]] = []
    for item, mask, severity, finding, action in specs:
        count = int(mask.sum())
        out.append(
            {
                "unblock_item": item,
                "count": count,
                "ratio": _ratio(count, total),
                "severity": severity if count else "info",
                "finding": finding,
                "suggested_action": action if count else "no action",
                "examples": _examples(frame, mask),
                "notes": "candidate unblock plan aggregation",
            }
        )
    for value, count in frame["unblock_path"].value_counts().sort_index().items():
        mask = frame["unblock_path"].eq(value)
        out.append(
            {
                "unblock_item": f"unblock_path:{value}",
                "count": int(count),
                "ratio": _ratio(int(count), total),
                "severity": "high" if value not in {"liquidity_watch"} else "medium",
                "finding": f"{count} row(s) have unblock_path={value}.",
                "suggested_action": "see per-symbol next_action",
                "examples": _examples(frame, mask),
                "notes": "grouped by unblock_path",
            }
        )
    return out


def write_candidate_unblock_plan(
    rows: list[dict[str, Any]],
    *,
    report_path: str | Path = "output/candidate_unblock_plan.csv",
    summary_path: str | Path = "output/candidate_unblock_summary.csv",
) -> tuple[Path, Path]:
    report = Path(report_path)
    summary = Path(summary_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=CANDIDATE_UNBLOCK_PLAN_COLUMNS).to_csv(report, index=False, encoding="utf-8-sig")
    pd.DataFrame(_summary_rows(rows), columns=CANDIDATE_UNBLOCK_SUMMARY_COLUMNS).to_csv(summary, index=False, encoding="utf-8-sig")
    return report, summary


def summarize_candidate_unblock_plan(rows: list[dict[str, Any]] | pd.DataFrame | None = None, *, report_path: str | Path | None = None) -> dict[str, Any]:
    frame = pd.DataFrame(rows) if rows is not None else _read_csv(report_path or "output/candidate_unblock_plan.csv")
    if frame.empty:
        return {
            "candidate_unblock_plan_report": "output/candidate_unblock_plan.csv",
            "candidate_unblock_summary_report": "output/candidate_unblock_summary.csv",
            "total_symbols": 0,
            "wait_for_history_count": 0,
            "manual_review_required_count": 0,
            "source_lag_blocker_count": 0,
            "no_used_factors_count": 0,
            "factor_gate_blocked_count": 0,
            "benchmark_dependency_missing_count": 0,
            "estimated_unblockable_by_waiting_count": 0,
            "immediate_eligible_count": 0,
            "top_examples": [],
            "next_recommended_action": "build candidate unblock plan after candidate gate exists",
        }
    bool_col = lambda name: frame[name].astype(str).str.lower().isin(["true", "1", "yes"])
    top_examples = frame.head(10)[
        ["symbol", "name", "current_candidate_status", "unblock_path", "unblock_status", "next_action"]
    ].to_dict("records")
    wait_count = int(frame["unblock_path"].eq("wait_for_history").sum())
    manual_count = int(frame["unblock_path"].eq("manual_review_required").sum())
    source_lag_count = int(frame["unblock_path"].eq("source_lag_blocker").sum())
    no_used_count = int(frame["current_candidate_status"].eq("blocked_no_used_factors").sum())
    factor_blocked_count = int(bool_col("still_blocked_after_primary_fix").sum())
    benchmark_count = int(bool_col("can_be_unblocked_by_benchmark_update").sum())
    immediate_count = int(frame["unblock_status"].eq("eligible_after_conditions").sum())
    if source_lag_count:
        next_action = "diagnose source lag and keep affected symbols blocked; do not run full-market refresh for this alone"
    elif manual_count:
        next_action = "complete manual review first, then rerun candidate gate without auto-clearing blocks"
    elif wait_count:
        next_action = "keep short-history ETFs excluded until minimum rows are reached, then rerun candidate gate"
    elif benchmark_count:
        next_action = "fix benchmark/index cache dependencies in a controlled environment"
    elif factor_blocked_count:
        next_action = "wait for factor gate to pass before entering 008B"
    else:
        next_action = "review unblock plan before candidate research"
    return {
        "candidate_unblock_plan_report": "output/candidate_unblock_plan.csv",
        "candidate_unblock_summary_report": "output/candidate_unblock_summary.csv",
        "total_symbols": int(len(frame)),
        "wait_for_history_count": wait_count,
        "manual_review_required_count": manual_count,
        "source_lag_blocker_count": source_lag_count,
        "no_used_factors_count": no_used_count,
        "factor_gate_blocked_count": factor_blocked_count,
        "benchmark_dependency_missing_count": benchmark_count,
        "estimated_unblockable_by_waiting_count": int(bool_col("can_be_unblocked_by_waiting").sum()),
        "immediate_eligible_count": immediate_count,
        "top_examples": top_examples,
        "next_recommended_action": next_action,
    }


def merge_candidate_unblock_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    summary: dict[str, Any] | None = None,
) -> bool:
    path = Path(qa_report_path)
    if not path.exists():
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    output_dir = path.parent
    candidate_summary = summary or summarize_candidate_unblock_plan(report_path=output_dir / "candidate_unblock_plan.csv")
    strategy_layer = report.setdefault("strategy_layer", {})
    strategy_layer["candidate_unblock"] = candidate_summary
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
