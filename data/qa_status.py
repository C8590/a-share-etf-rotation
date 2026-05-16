from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


QA_STATUS_BREAKDOWN_COLUMNS = [
    "qa_item",
    "raw_status",
    "normalized_status",
    "severity",
    "blocking",
    "actionability",
    "affected_count",
    "affected_ratio",
    "root_cause",
    "governed_by",
    "recommended_action",
    "can_be_fixed_by_refresh",
    "can_be_fixed_by_waiting",
    "requires_manual_review",
    "blocks_candidate_pool",
    "blocks_007b",
    "blocks_008b",
    "notes",
]

QA_STATUS_SUMMARY_COLUMNS = [
    "summary_item",
    "count",
    "severity",
    "finding",
    "suggested_action",
    "examples",
    "notes",
]

ACTIONABILITY_VALUES = {
    "refresh_needed",
    "wait_for_history",
    "manual_review",
    "source_unavailable",
    "governance_blocked",
    "already_governed",
    "unknown",
}


def _read_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        return pd.DataFrame()


def _read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _bool_count(frame: pd.DataFrame, column: str) -> int:
    if frame.empty or column not in frame.columns:
        return 0
    values = frame[column].astype(str).str.lower().str.strip()
    return int(values.isin(["true", "1", "yes", "y"]).sum())


def _count_value(frame: pd.DataFrame, column: str, value: str) -> int:
    if frame.empty or column not in frame.columns:
        return 0
    return int(frame[column].astype(str).str.lower().eq(value.lower()).sum())


def _numeric(value: Any, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return float(parsed)


def _int(value: Any, default: int = 0) -> int:
    return int(_numeric(value, float(default)))


def _ratio(count: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(count) / float(denominator), 6)


def _examples(frame: pd.DataFrame, limit: int = 5) -> str:
    if frame.empty or "symbol" not in frame.columns:
        return ""
    parts: list[str] = []
    for row in frame.head(limit).to_dict("records"):
        symbol = str(row.get("symbol", "")).zfill(6)
        name = str(row.get("name", "")).strip()
        parts.append(f"{symbol} {name}".strip())
    return ";".join(parts)


def _parse_gap_days(reasons: list[Any]) -> int:
    for reason in reasons:
        match = re.search(r"coverage gap is (\d+) day", str(reason))
        if match:
            return int(match.group(1))
    return 0


def classify_qa_failure_actionability(
    qa_item: str,
    *,
    root_cause: str = "",
    requires_manual_review: bool = False,
    can_be_fixed_by_refresh: str | bool = "false",
    can_be_fixed_by_waiting: bool = False,
    blocks_007b: bool = False,
    blocks_008b: bool = False,
) -> str:
    item = f"{qa_item} {root_cause}".lower()
    refresh_text = str(can_be_fixed_by_refresh).lower()
    if requires_manual_review or "manual_review" in item:
        return "manual_review"
    if "short_history" in item or can_be_fixed_by_waiting:
        return "wait_for_history"
    if refresh_text in {"true", "maybe"} or "coverage_gap" in item or "stale" in item:
        return "refresh_needed"
    if blocks_007b or "benchmark" in item or "index cache" in item:
        return "source_unavailable"
    if blocks_008b or "candidate" in item or "factor_gate" in item:
        return "governance_blocked"
    if "governed" in item:
        return "already_governed"
    return "unknown"


def _row(
    *,
    qa_item: str,
    raw_status: str,
    normalized_status: str,
    severity: str,
    blocking: bool,
    actionability: str,
    affected_count: int,
    affected_ratio: float,
    root_cause: str,
    governed_by: str,
    recommended_action: str,
    can_be_fixed_by_refresh: str | bool,
    can_be_fixed_by_waiting: bool,
    requires_manual_review: bool,
    blocks_candidate_pool: bool,
    blocks_007b: bool,
    blocks_008b: bool,
    notes: str,
) -> dict[str, Any]:
    if actionability not in ACTIONABILITY_VALUES:
        actionability = "unknown"
    return {
        "qa_item": qa_item,
        "raw_status": raw_status,
        "normalized_status": normalized_status,
        "severity": severity,
        "blocking": bool(blocking),
        "actionability": actionability,
        "affected_count": int(affected_count),
        "affected_ratio": round(float(affected_ratio), 6),
        "root_cause": root_cause,
        "governed_by": governed_by,
        "recommended_action": recommended_action,
        "can_be_fixed_by_refresh": str(can_be_fixed_by_refresh).lower(),
        "can_be_fixed_by_waiting": bool(can_be_fixed_by_waiting),
        "requires_manual_review": bool(requires_manual_review),
        "blocks_candidate_pool": bool(blocks_candidate_pool),
        "blocks_007b": bool(blocks_007b),
        "blocks_008b": bool(blocks_008b),
        "notes": notes,
    }


def build_qa_status_breakdown(
    *,
    output_dir: str | Path = "output",
    qa_report: dict[str, Any] | None = None,
    data_governance_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    qa = qa_report if qa_report is not None else _read_json(output_path / "qa_report.json")
    governance = data_governance_status if data_governance_status is not None else _read_json(output_path / "data_governance_status.json")
    data_layer = qa.get("data_layer", {}) if isinstance(qa.get("data_layer"), dict) else {}
    strategy_layer = qa.get("strategy_layer", {}) if isinstance(qa.get("strategy_layer"), dict) else {}
    reasons = list(data_layer.get("reasons", [])) or list(qa.get("blocking_reasons", []))

    diagnosis = _read_csv(output_path / "data_quality_diagnosis.csv")
    diagnosis_summary = _read_csv(output_path / "data_quality_diagnosis_summary.csv")
    candidate_gate = _read_csv(output_path / "candidate_gate.csv")
    observation_pool = _read_csv(output_path / "short_history_observation_pool.csv")
    manual_review = _read_csv(output_path / "manual_review_list.csv")
    factor_gate = _read_csv(output_path / "factor_score_gate.csv")
    index_coverage = _read_csv(output_path / "index_data_coverage.csv")
    coverage = _read_csv(output_path / "data_coverage_report.csv")
    failure_summary = _read_csv(output_path / "data_failure_summary.csv")

    total_coverage = len(coverage) if not coverage.empty else _int(data_layer.get("effective_etf_count"), 0)
    candidate_total = len(candidate_gate)
    diagnosis_count = len(diagnosis)
    short_history_count = _count_value(diagnosis, "history_status", "short_history") + _count_value(diagnosis, "history_status", "very_short_history")
    if not short_history_count:
        short_history_count = _int(data_layer.get("short_history_count", governance.get("data_quality_failed_count", diagnosis_count)))
    manual_count = len(manual_review) if not manual_review.empty else _int(data_layer.get("manual_review_count", governance.get("manual_review_count", 0)))
    candidate_blocked = _bool_count(candidate_gate, "blocked") or _int(governance.get("candidate_blocked_count", 0))
    candidate_eligible = _count_value(candidate_gate, "candidate_status", "eligible") or _int(governance.get("candidate_eligible_count", 0))
    factor_blocking = _bool_count(factor_gate, "blocking")
    factor_status = str(governance.get("factor_gate_status") or data_layer.get("factor_score", {}).get("gate_status") or strategy_layer.get("factor_score", {}).get("gate_status") or "")
    usable_benchmark_count = _bool_count(index_coverage, "usable_as_benchmark")
    if isinstance(data_layer.get("index_data"), dict):
        usable_benchmark_count = _int(data_layer["index_data"].get("usable_benchmark_count"), usable_benchmark_count)
    index_total = len(index_coverage)
    gap_days = _int(governance.get("end_date_coverage_gap_days"), 0) or _parse_gap_days(reasons)
    stale_rows = failure_summary[failure_summary.get("failure_type", pd.Series(dtype=str)).astype(str).eq("stale_end_date")] if not failure_summary.empty and "failure_type" in failure_summary.columns else pd.DataFrame()
    stale_count = int(len(stale_rows)) if not stale_rows.empty else (1 if gap_days > 0 else 0)

    rows: list[dict[str, Any]] = []
    if diagnosis_count or short_history_count:
        rows.append(
            _row(
                qa_item="data_quality_failed",
                raw_status=f"data quality failed for {diagnosis_count or short_history_count} ETF(s)",
                normalized_status="failed_governed",
                severity="high",
                blocking=True,
                actionability=classify_qa_failure_actionability("data_quality_failed", root_cause="short_history", can_be_fixed_by_waiting=True),
                affected_count=diagnosis_count or short_history_count,
                affected_ratio=_ratio(diagnosis_count or short_history_count, total_coverage),
                root_cause="short_history",
                governed_by="data_quality_diagnosis + candidate_gate + observation_pool",
                recommended_action="keep excluded and observe until sufficient history",
                can_be_fixed_by_refresh=False,
                can_be_fixed_by_waiting=True,
                requires_manual_review=False,
                blocks_candidate_pool=True,
                blocks_007b=False,
                blocks_008b=True,
                notes="short_history is structural readiness, not a full-market refresh queue",
            )
        )
    if gap_days > 0:
        rows.append(
            _row(
                qa_item="end_date_coverage_gap",
                raw_status=f"ETF end-date coverage gap is {gap_days} days",
                normalized_status="failed_actionable",
                severity="high",
                blocking=True,
                actionability=classify_qa_failure_actionability("end_date_coverage_gap", root_cause="stale_or_source_lag", can_be_fixed_by_refresh="maybe"),
                affected_count=stale_count,
                affected_ratio=_ratio(stale_count, total_coverage),
                root_cause="stale_or_source_lag",
                governed_by="data_failure_summary + data_coverage_report + qa_report",
                recommended_action="run update-data only in controlled environment or diagnose source lag",
                can_be_fixed_by_refresh="maybe",
                can_be_fixed_by_waiting=False,
                requires_manual_review=False,
                blocks_candidate_pool=True,
                blocks_007b=False,
                blocks_008b=True,
                notes="do not refresh full market in this QA-status step",
            )
        )
    if manual_count > 0:
        rows.append(
            _row(
                qa_item="manual_review_required",
                raw_status=f"{manual_count} ETF(s) require manual review",
                normalized_status="blocked_until_review",
                severity="high",
                blocking=True,
                actionability="manual_review",
                affected_count=manual_count,
                affected_ratio=_ratio(manual_count, total_coverage),
                root_cause="manual_review_required",
                governed_by="manual_review_list",
                recommended_action="complete manual review, do not auto unblock",
                can_be_fixed_by_refresh=False,
                can_be_fixed_by_waiting=False,
                requires_manual_review=True,
                blocks_candidate_pool=True,
                blocks_007b=False,
                blocks_008b=True,
                notes=_examples(manual_review),
            )
        )
    if candidate_blocked > 0 or candidate_eligible <= 0:
        rows.append(
            _row(
                qa_item="candidate_gate",
                raw_status=f"{candidate_eligible} eligible / {candidate_blocked} blocked",
                normalized_status="candidate_pool_blocked",
                severity="high",
                blocking=True,
                actionability="governance_blocked",
                affected_count=candidate_blocked,
                affected_ratio=_ratio(candidate_blocked, candidate_total),
                root_cause="no_candidate_eligible",
                governed_by="candidate_gate",
                recommended_action="keep blocked rows out of production candidate pool",
                can_be_fixed_by_refresh=False,
                can_be_fixed_by_waiting=short_history_count > 0,
                requires_manual_review=manual_count > 0,
                blocks_candidate_pool=True,
                blocks_007b=False,
                blocks_008b=True,
                notes="candidate gate is the production-pool stop sign; it does not alter strategy output",
            )
        )
    if factor_blocking > 0 or factor_status == "blocked_for_strategy_use":
        rows.append(
            _row(
                qa_item="factor_gate_status",
                raw_status=factor_status or "blocked_for_strategy_use",
                normalized_status="blocked_for_strategy_use",
                severity="high",
                blocking=True,
                actionability="governance_blocked",
                affected_count=factor_blocking,
                affected_ratio=_ratio(factor_blocking, len(factor_gate)),
                root_cause="factor_score_gate_blocked",
                governed_by="factor_score_gate",
                recommended_action="do not enter 008B",
                can_be_fixed_by_refresh=False,
                can_be_fixed_by_waiting=False,
                requires_manual_review=False,
                blocks_candidate_pool=True,
                blocks_007b=False,
                blocks_008b=True,
                notes="factor score remains observation-only until gate findings clear",
            )
        )
    if index_total > 0 and usable_benchmark_count <= 0:
        rows.append(
            _row(
                qa_item="usable_benchmark_count",
                raw_status="usable_benchmark_count=0",
                normalized_status="blocked_for_benchmark_research",
                severity="high",
                blocking=True,
                actionability="source_unavailable",
                affected_count=index_total,
                affected_ratio=1.0,
                root_cause="no usable index cache",
                governed_by="index_data_coverage + index_source_diagnostics",
                recommended_action="rerun diagnose-index-source and update-index-data in network-enabled environment",
                can_be_fixed_by_refresh="maybe",
                can_be_fixed_by_waiting=False,
                requires_manual_review=False,
                blocks_candidate_pool=False,
                blocks_007b=True,
                blocks_008b=False,
                notes="007B remains blocked until at least one schema-valid benchmark cache is usable",
            )
        )
    if diagnosis_count and len(observation_pool) == diagnosis_count and len(manual_review) <= diagnosis_count:
        rows.append(
            _row(
                qa_item="governance_coverage",
                raw_status="diagnosis, observation pool, manual review, and candidate gate reports exist",
                normalized_status="covered_not_cleared",
                severity="info",
                blocking=False,
                actionability="already_governed",
                affected_count=diagnosis_count,
                affected_ratio=_ratio(diagnosis_count, total_coverage),
                root_cause="governance_coverage",
                governed_by="data_quality_diagnosis + observation_pool + manual_review_list + candidate_gate",
                recommended_action="treat QA failure as explained but still hard-gated",
                can_be_fixed_by_refresh=False,
                can_be_fixed_by_waiting=True,
                requires_manual_review=manual_count > 0,
                blocks_candidate_pool=False,
                blocks_007b=False,
                blocks_008b=False,
                notes="governed does not mean passed",
            )
        )
    return rows


def summarize_qa_status(rows: list[dict[str, Any]] | pd.DataFrame) -> dict[str, Any]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {
            "qa_status_breakdown_report": "output/qa_status_breakdown.csv",
            "qa_status_summary_report": "output/qa_status_summary.csv",
            "hard_failure_count": 0,
            "governed_failure_count": 0,
            "refresh_action_count": 0,
            "wait_for_history_count": 0,
            "manual_review_action_count": 0,
            "blocks_007b": False,
            "blocks_008b": False,
            "next_recommended_action": "no QA status breakdown available",
            "actionability_counts": {},
            "top_examples": [],
        }
    blocking = frame["blocking"].astype(str).str.lower().isin(["true", "1", "yes"])
    actionability_counts = frame.groupby("actionability")["affected_count"].apply(lambda s: int(pd.to_numeric(s, errors="coerce").fillna(0).sum())).to_dict()
    blocks_007b = frame["blocks_007b"].astype(str).str.lower().isin(["true", "1", "yes"]).any()
    blocks_008b = frame["blocks_008b"].astype(str).str.lower().isin(["true", "1", "yes"]).any()
    manual_count = int(actionability_counts.get("manual_review", 0))
    refresh_count = int(actionability_counts.get("refresh_needed", 0))
    wait_count = int(actionability_counts.get("wait_for_history", 0))
    if manual_count > 0:
        next_action = "complete P0 manual review, keep short-history ETFs excluded, and do not auto-unblock"
    elif refresh_count > 0:
        next_action = "diagnose source lag or run controlled update-data for the stale coverage item"
    elif wait_count > 0:
        next_action = "wait for sufficient ETF history and rerun governance reports"
    elif blocks_007b:
        next_action = "rerun diagnose-index-source and update-index-data in a network-enabled environment"
    elif blocks_008b:
        next_action = "clear factor and candidate gates before entering 008B"
    else:
        next_action = "review QA status and rerun qa-check"
    governed_actions = {"wait_for_history", "manual_review", "governance_blocked", "already_governed", "source_unavailable", "refresh_needed"}
    governed_failure_count = int(frame[frame["actionability"].isin(governed_actions) & blocking]["qa_item"].nunique())
    return {
        "qa_status_breakdown_report": "output/qa_status_breakdown.csv",
        "qa_status_summary_report": "output/qa_status_summary.csv",
        "hard_failure_count": int(blocking.sum()),
        "governed_failure_count": governed_failure_count,
        "refresh_action_count": refresh_count,
        "wait_for_history_count": wait_count,
        "manual_review_action_count": manual_count,
        "blocks_007b": bool(blocks_007b),
        "blocks_008b": bool(blocks_008b),
        "next_recommended_action": next_action,
        "actionability_counts": {str(k): int(v) for k, v in actionability_counts.items()},
        "top_examples": frame[["qa_item", "actionability", "affected_count", "recommended_action"]].head(10).to_dict("records"),
    }


def build_qa_status_summary_rows(rows: list[dict[str, Any]] | pd.DataFrame) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    summary = summarize_qa_status(frame)
    if frame.empty:
        return [
            {
                "summary_item": "qa_status",
                "count": 0,
                "severity": "info",
                "finding": "No QA status rows are available.",
                "suggested_action": "Run summarize-qa-status after upstream QA reports exist.",
                "examples": "",
                "notes": "",
            }
        ]
    rows_out: list[dict[str, Any]] = []
    for actionability, count in summary["actionability_counts"].items():
        examples = frame[frame["actionability"].eq(actionability)]["qa_item"].head(5).astype(str).tolist()
        severity = "high" if actionability in {"refresh_needed", "manual_review", "source_unavailable", "governance_blocked", "wait_for_history"} else "info"
        rows_out.append(
            {
                "summary_item": f"actionability:{actionability}",
                "count": int(count),
                "severity": severity,
                "finding": f"{count} affected item(s) map to {actionability}.",
                "suggested_action": str(frame[frame["actionability"].eq(actionability)]["recommended_action"].head(1).iloc[0]),
                "examples": ";".join(examples),
                "notes": "counts use affected_count, not necessarily unique ETFs across rows",
            }
        )
    rows_out.extend(
        [
            {
                "summary_item": "hard_failure_count",
                "count": summary["hard_failure_count"],
                "severity": "high" if summary["hard_failure_count"] else "info",
                "finding": f"{summary['hard_failure_count']} blocking QA-status row(s) remain.",
                "suggested_action": summary["next_recommended_action"],
                "examples": ";".join(frame[frame["blocking"].astype(str).str.lower().isin(["true", "1", "yes"])]["qa_item"].head(5).astype(str).tolist()),
                "notes": "This does not relax qa-check.",
            },
            {
                "summary_item": "blocks_007b",
                "count": int(summary["blocks_007b"]),
                "severity": "high" if summary["blocks_007b"] else "info",
                "finding": "ETF-GAP-007B is blocked." if summary["blocks_007b"] else "ETF-GAP-007B is not blocked by qa_status.",
                "suggested_action": "Rerun diagnose-index-source and update-index-data in a network-enabled environment." if summary["blocks_007b"] else "Continue normal QA review.",
                "examples": ";".join(frame[frame["blocks_007b"].astype(str).str.lower().isin(["true", "1", "yes"])]["qa_item"].head(5).astype(str).tolist()),
                "notes": "Requires usable_benchmark_count > 0 before 007B.",
            },
            {
                "summary_item": "blocks_008b",
                "count": int(summary["blocks_008b"]),
                "severity": "high" if summary["blocks_008b"] else "info",
                "finding": "ETF-GAP-008B is blocked." if summary["blocks_008b"] else "ETF-GAP-008B is not blocked by qa_status.",
                "suggested_action": "Keep candidate and factor gates closed until QA, history, manual review, and factor coverage clear." if summary["blocks_008b"] else "Continue normal QA review.",
                "examples": ";".join(frame[frame["blocks_008b"].astype(str).str.lower().isin(["true", "1", "yes"])]["qa_item"].head(5).astype(str).tolist()),
                "notes": "QA-status explains blockers but does not clear them.",
            },
        ]
    )
    return rows_out


def write_qa_status_report(
    rows: list[dict[str, Any]],
    *,
    breakdown_path: str | Path = "output/qa_status_breakdown.csv",
    summary_path: str | Path = "output/qa_status_summary.csv",
) -> tuple[Path, Path]:
    breakdown = Path(breakdown_path)
    summary = Path(summary_path)
    breakdown.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=QA_STATUS_BREAKDOWN_COLUMNS).to_csv(breakdown, index=False, encoding="utf-8-sig")
    pd.DataFrame(build_qa_status_summary_rows(rows), columns=QA_STATUS_SUMMARY_COLUMNS).to_csv(summary, index=False, encoding="utf-8-sig")
    return breakdown, summary


def merge_qa_status_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    summary: dict[str, Any] | None = None,
) -> bool:
    path = Path(qa_report_path)
    report = _read_json(path)
    if not report:
        return False
    qa_status = summary if summary is not None else summarize_qa_status(_read_csv(path.parent / "qa_status_breakdown.csv"))
    data_layer = report.setdefault("data_layer", {})
    data_layer["qa_status"] = qa_status
    data_layer.update(
        {
            "qa_status_breakdown_report": qa_status["qa_status_breakdown_report"],
            "qa_status_summary_report": qa_status["qa_status_summary_report"],
            "hard_failure_count": qa_status["hard_failure_count"],
            "governed_failure_count": qa_status["governed_failure_count"],
            "refresh_action_count": qa_status["refresh_action_count"],
            "wait_for_history_count": qa_status["wait_for_history_count"],
            "manual_review_action_count": qa_status["manual_review_action_count"],
            "blocks_007b": qa_status["blocks_007b"],
            "blocks_008b": qa_status["blocks_008b"],
            "next_recommended_action": qa_status["next_recommended_action"],
        }
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
