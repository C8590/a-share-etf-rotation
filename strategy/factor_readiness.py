from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


FACTOR_008B_READINESS_COLUMNS = [
    "readiness_item",
    "current_status",
    "passed",
    "blocking",
    "severity",
    "threshold",
    "actual_value",
    "blocker_type",
    "dependency",
    "remediation_action",
    "prerequisite_task",
    "estimated_path",
    "can_be_resolved_by_waiting",
    "can_be_resolved_by_manual_review",
    "can_be_resolved_by_index_cache",
    "can_be_resolved_by_metadata",
    "can_be_resolved_by_nav_iopv",
    "can_be_resolved_by_factor_config",
    "notes",
]

FACTOR_008B_READINESS_SUMMARY_COLUMNS = [
    "summary_item",
    "count",
    "severity",
    "finding",
    "suggested_action",
    "examples",
    "notes",
]

BLOCKER_TYPES = {
    "candidate_gate",
    "factor_gate",
    "coverage",
    "short_history",
    "no_used_factors",
    "benchmark_dependency",
    "nav_iopv_dependency",
    "metadata_dependency",
    "manual_review",
    "unknown",
}


def _read_csv(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    return pd.read_csv(file_path, dtype=str, encoding="utf-8-sig").fillna("")


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


def _float(value: Any, default: float = 0.0) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return default
    return float(parsed)


def _gate_by_item(gate: pd.DataFrame, item: str) -> dict[str, Any]:
    if gate.empty or "gate_item" not in gate.columns:
        return {}
    rows = gate[gate["gate_item"].astype(str).eq(item)]
    if rows.empty:
        return {}
    return rows.iloc[0].to_dict()


def _audit_rows(audit: pd.DataFrame, item: str) -> pd.DataFrame:
    if audit.empty or "audit_item" not in audit.columns:
        return pd.DataFrame()
    return audit[audit["audit_item"].astype(str).eq(item)]


def _audit_row(audit: pd.DataFrame, item: str) -> dict[str, Any]:
    rows = _audit_rows(audit, item)
    if rows.empty:
        return {}
    return rows.iloc[0].to_dict()


def _factor_coverage_row(audit: pd.DataFrame, factor_name: str) -> dict[str, Any]:
    rows = _audit_rows(audit, "factor_coverage_by_name")
    if rows.empty:
        return {}
    mask = rows["finding"].astype(str).str.contains(f"{factor_name} is used", regex=False)
    if not bool(mask.any()) and "notes" in rows.columns:
        mask = rows["notes"].astype(str).str.contains(factor_name, regex=False)
    if not bool(mask.any()):
        return {}
    return rows[mask].iloc[0].to_dict()


def _metadata_coverage(coverage: pd.DataFrame, field_name: str) -> float:
    if coverage.empty or "field_name" not in coverage.columns or "coverage_ratio" not in coverage.columns:
        return 0.0
    rows = coverage[coverage["field_name"].astype(str).eq(field_name)]
    if rows.empty:
        return 0.0
    return _float(rows.iloc[0].get("coverage_ratio"), 0.0)


def _examples_from_symbols(symbol_text: Any, limit: int = 5) -> str:
    parts = [part.strip() for part in str(symbol_text).split(",") if part.strip()]
    return ";".join(parts[:limit])


def classify_008b_blocker(readiness_item: str, *, current_status: str = "", dependency: str = "") -> dict[str, Any]:
    item = readiness_item.lower()
    dep = dependency.lower()
    if "candidate" in item:
        return {"blocker_type": "candidate_gate", "prerequisite_task": "rerun candidate governance after row-level blockers clear"}
    if "factor_gate" in item:
        return {"blocker_type": "factor_gate", "prerequisite_task": "rerun compute-factor-score and factor gate after dependencies clear"}
    if "computable" in item or "coverage" in item:
        return {"blocker_type": "coverage", "prerequisite_task": "restore broad factor coverage without filling missing values"}
    if "short_history" in item:
        return {"blocker_type": "short_history", "prerequisite_task": "wait for history and rerun diagnose-data-quality / candidate gate / factor score"}
    if "no_used_factors" in item or "unable_to_score" in item:
        return {"blocker_type": "no_used_factors", "prerequisite_task": "make at least one enabled factor genuinely usable"}
    if "tracking_error" in item or "relative_return" in item or "benchmark" in item or "benchmark" in dep:
        return {"blocker_type": "benchmark_dependency", "prerequisite_task": "diagnose-index-source and update-index-data in a controlled environment"}
    if "discount_premium" in item or "nav" in dep or "iopv" in dep:
        return {"blocker_type": "nav_iopv_dependency", "prerequisite_task": "add trusted NAV/IOPV source before enabling discount/premium"}
    if "fund_size" in item or "management_fee" in item or "metadata" in dep:
        return {"blocker_type": "metadata_dependency", "prerequisite_task": "enrich metadata coverage and then review factor config"}
    if "manual_review" in item:
        return {"blocker_type": "manual_review", "prerequisite_task": "complete manual review; do not auto-unblock"}
    return {"blocker_type": "unknown", "prerequisite_task": "review factor readiness evidence"}


def _row(
    *,
    readiness_item: str,
    current_status: str,
    passed: bool,
    blocking: bool,
    severity: str,
    threshold: str,
    actual_value: str,
    blocker_type: str,
    dependency: str,
    remediation_action: str,
    prerequisite_task: str,
    estimated_path: str,
    can_be_resolved_by_waiting: bool = False,
    can_be_resolved_by_manual_review: bool = False,
    can_be_resolved_by_index_cache: bool = False,
    can_be_resolved_by_metadata: bool = False,
    can_be_resolved_by_nav_iopv: bool = False,
    can_be_resolved_by_factor_config: bool = False,
    notes: str = "",
) -> dict[str, Any]:
    if blocker_type not in BLOCKER_TYPES:
        blocker_type = "unknown"
    return {
        "readiness_item": readiness_item,
        "current_status": current_status,
        "passed": bool(passed),
        "blocking": bool(blocking),
        "severity": severity,
        "threshold": threshold,
        "actual_value": actual_value,
        "blocker_type": blocker_type,
        "dependency": dependency,
        "remediation_action": remediation_action,
        "prerequisite_task": prerequisite_task,
        "estimated_path": estimated_path,
        "can_be_resolved_by_waiting": bool(can_be_resolved_by_waiting),
        "can_be_resolved_by_manual_review": bool(can_be_resolved_by_manual_review),
        "can_be_resolved_by_index_cache": bool(can_be_resolved_by_index_cache),
        "can_be_resolved_by_metadata": bool(can_be_resolved_by_metadata),
        "can_be_resolved_by_nav_iopv": bool(can_be_resolved_by_nav_iopv),
        "can_be_resolved_by_factor_config": bool(can_be_resolved_by_factor_config),
        "notes": notes,
    }


def _row_from_gate(
    gate: pd.DataFrame,
    gate_item: str,
    *,
    readiness_item: str,
    blocker_type: str,
    dependency: str,
    remediation_action: str,
    prerequisite_task: str,
    estimated_path: str,
    can_be_resolved_by_waiting: bool = False,
    can_be_resolved_by_index_cache: bool = False,
    can_be_resolved_by_nav_iopv: bool = False,
    can_be_resolved_by_metadata: bool = False,
    can_be_resolved_by_factor_config: bool = False,
    notes: str = "",
) -> dict[str, Any]:
    evidence = _gate_by_item(gate, gate_item)
    passed = _bool(evidence.get("passed", False))
    blocking = (not passed) and _bool(evidence.get("blocking", False))
    return _row(
        readiness_item=readiness_item,
        current_status=_text(evidence.get("status") or ("passed" if passed else "blocked")),
        passed=passed,
        blocking=blocking,
        severity=_text(evidence.get("severity") or ("high" if blocking else "info")),
        threshold=_text(evidence.get("threshold")),
        actual_value=_text(evidence.get("actual_value")),
        blocker_type=blocker_type,
        dependency=dependency,
        remediation_action=_text(evidence.get("suggested_action") or remediation_action),
        prerequisite_task=prerequisite_task,
        estimated_path=estimated_path,
        can_be_resolved_by_waiting=can_be_resolved_by_waiting,
        can_be_resolved_by_index_cache=can_be_resolved_by_index_cache,
        can_be_resolved_by_nav_iopv=can_be_resolved_by_nav_iopv,
        can_be_resolved_by_metadata=can_be_resolved_by_metadata,
        can_be_resolved_by_factor_config=can_be_resolved_by_factor_config,
        notes=_text(evidence.get("notes") or notes),
    )


def build_008b_readiness_check(
    *,
    output_dir: str | Path = "output",
    factor_gate: pd.DataFrame | None = None,
    factor_audit: pd.DataFrame | None = None,
    factor_report: pd.DataFrame | None = None,
    factor_detail: pd.DataFrame | None = None,
    candidate_gate: pd.DataFrame | None = None,
    candidate_unblock: pd.DataFrame | None = None,
    manual_review: pd.DataFrame | None = None,
    etf_metrics_coverage: pd.DataFrame | None = None,
    index_coverage: pd.DataFrame | None = None,
    etf_metadata_coverage: pd.DataFrame | None = None,
    data_governance_status: dict[str, Any] | None = None,
    qa_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    gate = factor_gate if factor_gate is not None else _read_csv(output_path / "factor_score_gate.csv")
    audit = factor_audit if factor_audit is not None else _read_csv(output_path / "factor_score_audit.csv")
    report = factor_report if factor_report is not None else _read_csv(output_path / "factor_score_report.csv")
    detail = factor_detail if factor_detail is not None else _read_csv(output_path / "factor_score_detail.csv")
    candidates = candidate_gate if candidate_gate is not None else _read_csv(output_path / "candidate_gate.csv")
    unblock = candidate_unblock if candidate_unblock is not None else _read_csv(output_path / "candidate_unblock_plan.csv")
    manual = manual_review if manual_review is not None else _read_csv(output_path / "manual_review_list.csv")
    metrics_coverage = etf_metrics_coverage if etf_metrics_coverage is not None else _read_csv(output_path / "etf_metrics_coverage.csv")
    index_cov = index_coverage if index_coverage is not None else _read_csv(output_path / "index_data_coverage.csv")
    metadata_cov = etf_metadata_coverage if etf_metadata_coverage is not None else _read_csv(output_path / "etf_metadata_coverage.csv")
    governance = data_governance_status if data_governance_status is not None else _read_json(output_path / "data_governance_status.json")
    qa = qa_report if qa_report is not None else _read_json(output_path / "qa_report.json")

    strategy_layer = qa.get("strategy_layer", {}) if isinstance(qa.get("strategy_layer"), dict) else {}
    factor_summary = strategy_layer.get("factor_score", {}) if isinstance(strategy_layer.get("factor_score"), dict) else {}
    candidate_eligible = _int(governance.get("candidate_eligible_count"), -1)
    if candidate_eligible < 0 and not candidates.empty and "candidate_status" in candidates.columns:
        candidate_eligible = int(candidates["candidate_status"].astype(str).eq("eligible").sum())
    candidate_total = _int(governance.get("candidate_total"), len(candidates))
    manual_count = _int(governance.get("manual_review_count"), len(manual))
    gate_status = _text(governance.get("factor_gate_status") or factor_summary.get("gate_status"))
    if not gate_status:
        blocked = gate.get("blocking", pd.Series(dtype=object)).astype(str).str.lower().isin(["true", "1", "yes"]) & gate.get("passed", pd.Series(dtype=object)).astype(str).str.lower().isin(["false", "0", "no"])
        gate_status = "blocked_for_strategy_use" if bool(blocked.any()) else "passed_for_candidate_research"
    score_ok = int(report["score_status"].astype(str).eq("ok").sum()) if not report.empty and "score_status" in report.columns else _int(factor_summary.get("score_computable_count"), 0)
    score_total = len(report) if not report.empty else _int(factor_summary.get("total_symbols"), 0)
    no_used = int(report["score_status"].astype(str).eq("no_used_factors").sum()) if not report.empty and "score_status" in report.columns else 0
    unable = score_total - score_ok if score_total else _int(factor_summary.get("unable_to_score_count"), 0)
    computable_ratio = round(score_ok / score_total, 4) if score_total else _float(factor_summary.get("computable_ratio"), 0.0)
    unable_ratio = round(unable / score_total, 4) if score_total else 0.0
    usable_benchmark = 0
    data_layer = qa.get("data_layer", {}) if isinstance(qa.get("data_layer"), dict) else {}
    index_summary = data_layer.get("index_data", {}) if isinstance(data_layer.get("index_data"), dict) else {}
    if index_summary:
        usable_benchmark = _int(index_summary.get("usable_benchmark_count"), 0)
    elif not index_cov.empty and "usable_as_benchmark" in index_cov.columns:
        usable_benchmark = int(index_cov["usable_as_benchmark"].astype(str).str.lower().isin(["true", "1", "yes"]).sum())

    rows: list[dict[str, Any]] = []
    rows.append(
        _row(
            readiness_item="candidate_eligible_count",
            current_status="passed" if candidate_eligible > 0 else "blocked",
            passed=candidate_eligible > 0,
            blocking=candidate_eligible <= 0,
            severity="high" if candidate_eligible <= 0 else "info",
            threshold="> 0",
            actual_value=f"{candidate_eligible}/{candidate_total}",
            blocker_type="candidate_gate",
            dependency="candidate_gate + candidate_unblock_plan",
            remediation_action="do not form candidates until candidate gate has eligible rows",
            prerequisite_task="clear short-history/manual-review/no-used-factor blockers, then rerun build-candidate-gate",
            estimated_path="candidate gate must be rebuilt from clean governance reports",
            notes="eligible=0 means no ETF can enter 008B candidate construction",
        )
    )
    rows.append(
        _row(
            readiness_item="factor_gate_status",
            current_status=gate_status,
            passed=gate_status == "passed_for_candidate_research",
            blocking=gate_status != "passed_for_candidate_research",
            severity="high" if gate_status != "passed_for_candidate_research" else "info",
            threshold="passed_for_candidate_research",
            actual_value=gate_status,
            blocker_type="factor_gate",
            dependency="factor_score_gate.csv",
            remediation_action="keep factor score observation-only until all blocking gate rows pass",
            prerequisite_task="fix source coverage, short-history bias, no-used-factors, and factor coverage, then rerun compute-factor-score",
            estimated_path="all factor_score_gate blocking rows must pass",
            notes="does not change qa-check exit code or current strategy",
        )
    )
    rows.extend(
        [
            _row_from_gate(
                gate,
                "min_computable_ratio",
                readiness_item="min_computable_ratio",
                blocker_type="coverage",
                dependency="factor_score_report.csv",
                remediation_action="raise score-computable coverage without imputing missing factor values",
                prerequisite_task="improve factor source coverage and rerun compute-factor-score",
                estimated_path="computable_ratio >= 0.80",
            ),
            _row_from_gate(
                gate,
                "max_unable_to_score_ratio",
                readiness_item="max_unable_to_score_ratio",
                blocker_type="no_used_factors",
                dependency="factor_score_report.csv",
                remediation_action="make no_used_factors rows genuinely scoreable; never treat them as low score",
                prerequisite_task="restore at least one enabled factor per affected ETF",
                estimated_path="unable_to_score_ratio <= 0.20",
            ),
            _row_from_gate(
                gate,
                "no_short_history_bias",
                readiness_item="short_history_bias",
                blocker_type="short_history",
                dependency="data_quality_diagnosis + candidate_gate",
                remediation_action="wait for enough history; do not rank short-history rows as candidates",
                prerequisite_task="rerun diagnose-data-quality / candidate gate / factor score after row counts mature",
                estimated_path="scoreable sample no longer dominated by short_history or insufficient_rows",
                can_be_resolved_by_waiting=True,
            ),
        ]
    )
    rows.append(
        _row(
            readiness_item="no_used_factors",
            current_status="blocked" if no_used > 0 else "passed",
            passed=no_used == 0,
            blocking=no_used > 0,
            severity="high" if no_used > 0 else "info",
            threshold="0 no_used_factors rows or within gate tolerance",
            actual_value=f"{no_used}/{score_total}; unable_to_score_ratio={unable_ratio:.4f}",
            blocker_type="no_used_factors",
            dependency="factor_score_report.csv + factor_score_detail.csv",
            remediation_action="restore genuine factor evidence; never fill missing indicators with zero",
            prerequisite_task="fix source dependencies or config only after source evidence is real",
            estimated_path="each candidate has at least one enabled factor with factor_status=used",
            notes="no_used_factors is unscoreable evidence, not bearish evidence",
        )
    )

    for factor_name, item_name in [
        ("tracking_error", "tracking_error_dependency"),
        ("relative_return_60d", "relative_return_dependency"),
    ]:
        coverage = _factor_coverage_row(audit, factor_name)
        used_count = _int(coverage.get("count"), 0)
        ratio = _float(coverage.get("ratio"), 0.0)
        rows.append(
            _row(
                readiness_item=item_name,
                current_status="blocked" if used_count == 0 else "warning",
                passed=used_count > 0 and usable_benchmark > 0,
                blocking=used_count == 0 or usable_benchmark <= 0,
                severity="high" if used_count == 0 or usable_benchmark <= 0 else "warning",
                threshold="factor used count > 0 and usable_benchmark_count > 0",
                actual_value=f"{factor_name}_used={used_count}; coverage={ratio:.4f}; usable_benchmark_count={usable_benchmark}",
                blocker_type="benchmark_dependency",
                dependency="etf_metrics + index_data_coverage + index_cache",
                remediation_action="build schema-valid benchmark/index cache before enabling benchmark-relative factors",
                prerequisite_task="diagnose-index-source -> update-index-data -> compute-etf-metrics -> compute-factor-score",
                estimated_path="usable_benchmark_count > 0 and metric coverage passes factor gate",
                can_be_resolved_by_index_cache=True,
                notes=_text(coverage.get("notes") or "benchmark-dependent factor is unavailable"),
            )
        )

    discount_cov = _factor_coverage_row(audit, "discount_premium")
    discount_used = _int(discount_cov.get("count"), 0)
    rows.append(
        _row(
            readiness_item="discount_premium_dependency",
            current_status="blocked" if discount_used == 0 else "warning",
            passed=discount_used > 0,
            blocking=discount_used == 0,
            severity="high" if discount_used == 0 else "warning",
            threshold="trusted NAV/IOPV data produces discount_premium",
            actual_value=f"discount_premium_used={discount_used}; coverage={_float(discount_cov.get('ratio'), 0.0):.4f}",
            blocker_type="nav_iopv_dependency",
            dependency="NAV/IOPV source + etf_metrics",
            remediation_action="add trusted NAV or IOPV source; exchange prices alone cannot create discount/premium",
            prerequisite_task="source NAV/IOPV, recompute ETF metrics, then rerun factor score",
            estimated_path="discount_premium factor_status becomes used for enough ETFs",
            can_be_resolved_by_nav_iopv=True,
            notes=_text(discount_cov.get("notes") or "do not fill discount/premium with zero"),
        )
    )

    for field_name, item_name in [("fund_size", "fund_size_dependency"), ("management_fee", "management_fee_dependency")]:
        coverage = _metadata_coverage(metadata_cov, field_name)
        factor_cov = _factor_coverage_row(audit, field_name)
        disabled = _text(factor_cov.get("status")).lower() == "disabled"
        rows.append(
            _row(
                readiness_item=item_name,
                current_status="warning" if disabled or coverage < 0.80 else "passed",
                passed=coverage >= 0.80 and not disabled,
                blocking=False,
                severity="warning" if disabled or coverage < 0.80 else "info",
                threshold="metadata coverage >= 0.80 and factor enabled only after coverage is confirmed",
                actual_value=f"metadata_coverage={coverage:.4f}; factor_status={_text(factor_cov.get('status') or 'unknown')}",
                blocker_type="metadata_dependency",
                dependency="etf_metadata_coverage + factor config",
                remediation_action="keep metadata factors disabled until coverage is independently confirmed",
                prerequisite_task="update ETF metadata, verify coverage, then review config/factor_score.yaml",
                estimated_path="metadata coverage sufficient before enabling size or fee factors",
                can_be_resolved_by_metadata=True,
                can_be_resolved_by_factor_config=True,
                notes="warning only while disabled; do not impute metadata values",
            )
        )

    rows.append(
        _row_from_gate(
            gate,
            "factor_coverage_minimum",
            readiness_item="factor_coverage_minimum",
            blocker_type="coverage",
            dependency="factor_score_audit + factor_score_gate",
            remediation_action="raise minimum enabled-factor coverage and sample size",
            prerequisite_task="fix source dependencies, wait for history, then rerun factor score gate",
            estimated_path="score_computable_count >= 30 and min enabled factor coverage >= 0.80",
            can_be_resolved_by_waiting=True,
            can_be_resolved_by_index_cache=True,
            can_be_resolved_by_nav_iopv=True,
            can_be_resolved_by_metadata=True,
        )
    )
    rows.append(
        _row(
            readiness_item="manual_review_required",
            current_status="blocked" if manual_count > 0 else "passed",
            passed=manual_count == 0,
            blocking=manual_count > 0,
            severity="high" if manual_count > 0 else "info",
            threshold="0 manual-review blockers",
            actual_value=str(manual_count),
            blocker_type="manual_review",
            dependency="manual_review_list.csv",
            remediation_action="complete manual review; do not auto-unblock suspicious rows",
            prerequisite_task="human review of abnormal return / low liquidity / very short history evidence",
            estimated_path="manual_review_list is clear and candidate gate is rerun",
            can_be_resolved_by_manual_review=True,
            notes="manual review cannot be cleared by factor scoring",
        )
    )
    rows.append(
        _row(
            readiness_item="benchmark_dependency",
            current_status="blocked" if usable_benchmark <= 0 else "passed",
            passed=usable_benchmark > 0,
            blocking=usable_benchmark <= 0,
            severity="high" if usable_benchmark <= 0 else "info",
            threshold="usable_benchmark_count > 0",
            actual_value=str(usable_benchmark),
            blocker_type="benchmark_dependency",
            dependency="index_data_coverage + index_cache",
            remediation_action="rerun diagnose-index-source and update-index-data in network-enabled controlled environment",
            prerequisite_task="schema-valid index cache, then compute-etf-metrics and factor score",
            estimated_path="usable benchmark cache exists before 007B/008B benchmark factors",
            can_be_resolved_by_index_cache=True,
            notes="also blocks ETF-GAP-007B",
        )
    )
    metadata_low = _audit_row(audit, "metadata_low_coverage_dependency")
    metadata_count = _int(metadata_low.get("count"), 0)
    rows.append(
        _row(
            readiness_item="metadata_dependency",
            current_status="warning" if metadata_count > 0 else "passed",
            passed=metadata_count == 0,
            blocking=False,
            severity="warning" if metadata_count > 0 else "info",
            threshold="metadata-dependent factors confirmed before enablement",
            actual_value=f"{metadata_count} affected",
            blocker_type="metadata_dependency",
            dependency="etf_metadata_coverage",
            remediation_action="enrich metadata before enabling fund_size or management_fee",
            prerequisite_task="update-etf-metadata and verify coverage; config review required before enabling",
            estimated_path="metadata factors remain disabled until coverage is trustworthy",
            can_be_resolved_by_metadata=True,
            can_be_resolved_by_factor_config=True,
            notes=_text(metadata_low.get("notes") or "metadata warning does not justify filling missing values"),
        )
    )
    return rows


def build_008b_remediation_plan(rows: list[dict[str, Any]] | pd.DataFrame) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    blocking = frame[frame["blocking"].astype(str).str.lower().isin(["true", "1", "yes"])]
    warning = frame[~frame["blocking"].astype(str).str.lower().isin(["true", "1", "yes"]) & ~frame["passed"].astype(str).str.lower().isin(["true", "1", "yes"])]
    plan: list[dict[str, Any]] = []
    for label, subset, severity in [("blocking_items", blocking, "high"), ("warning_items", warning, "warning")]:
        plan.append(
            {
                "summary_item": label,
                "count": int(len(subset)),
                "severity": severity if not subset.empty else "info",
                "finding": f"{len(subset)} readiness item(s) require action before 008B." if label == "blocking_items" else f"{len(subset)} readiness warning item(s) should be resolved or accepted explicitly.",
                "suggested_action": "; ".join(subset["prerequisite_task"].head(3).astype(str).tolist()) if not subset.empty else "no action",
                "examples": ";".join(subset["readiness_item"].head(5).astype(str).tolist()),
                "notes": "008B readiness aggregation",
            }
        )
    for blocker_type, count in blocking["blocker_type"].value_counts().sort_index().items():
        subset = blocking[blocking["blocker_type"].eq(blocker_type)]
        plan.append(
            {
                "summary_item": f"blocker_type:{blocker_type}",
                "count": int(count),
                "severity": "high",
                "finding": f"{count} blocking item(s) have blocker_type={blocker_type}.",
                "suggested_action": "; ".join(subset["remediation_action"].head(3).astype(str).tolist()),
                "examples": ";".join(subset["readiness_item"].head(5).astype(str).tolist()),
                "notes": "grouped blocking readiness items",
            }
        )
    return plan


def write_008b_readiness_report(
    rows: list[dict[str, Any]],
    *,
    report_path: str | Path = "output/factor_008b_readiness.csv",
    summary_path: str | Path = "output/factor_008b_readiness_summary.csv",
) -> tuple[Path, Path]:
    report = Path(report_path)
    summary = Path(summary_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=FACTOR_008B_READINESS_COLUMNS).to_csv(report, index=False, encoding="utf-8-sig")
    pd.DataFrame(build_008b_remediation_plan(rows), columns=FACTOR_008B_READINESS_SUMMARY_COLUMNS).to_csv(summary, index=False, encoding="utf-8-sig")
    return report, summary


def summarize_008b_readiness(rows: list[dict[str, Any]] | pd.DataFrame | None = None, *, report_path: str | Path | None = None) -> dict[str, Any]:
    frame = pd.DataFrame(rows) if rows is not None else _read_csv(report_path or "output/factor_008b_readiness.csv")
    if frame.empty:
        return {
            "factor_008b_readiness_report": "output/factor_008b_readiness.csv",
            "factor_008b_readiness_summary_report": "output/factor_008b_readiness_summary.csv",
            "readiness_status": "not_run",
            "allowed_to_enter_008b": False,
            "blocking_items": [],
            "warning_items": [],
            "top_blockers": [],
            "next_recommended_action": "run check-factor-008b-readiness after factor score reports exist",
        }
    bool_col = lambda name: frame[name].astype(str).str.lower().isin(["true", "1", "yes"])
    blocking = frame[bool_col("blocking")]
    warnings = frame[~bool_col("blocking") & ~bool_col("passed")]
    top_fields = ["readiness_item", "blocker_type", "actual_value", "remediation_action", "prerequisite_task"]
    allowed = blocking.empty and warnings.empty and bool_col("passed").all()
    if not blocking.empty:
        next_action = str(blocking.iloc[0].get("prerequisite_task") or blocking.iloc[0].get("remediation_action"))
    elif not warnings.empty:
        next_action = str(warnings.iloc[0].get("prerequisite_task") or warnings.iloc[0].get("remediation_action"))
    else:
        next_action = "008B readiness clean; review before generating factor_score_candidates.csv"
    return {
        "factor_008b_readiness_report": "output/factor_008b_readiness.csv",
        "factor_008b_readiness_summary_report": "output/factor_008b_readiness_summary.csv",
        "readiness_status": "passed" if allowed else "blocked",
        "allowed_to_enter_008b": bool(allowed),
        "blocking_items": blocking["readiness_item"].astype(str).tolist(),
        "warning_items": warnings["readiness_item"].astype(str).tolist(),
        "top_blockers": blocking[top_fields].head(10).to_dict("records"),
        "next_recommended_action": next_action,
    }


def merge_008b_readiness_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    summary: dict[str, Any] | None = None,
) -> bool:
    path = Path(qa_report_path)
    if not path.exists():
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    output_dir = path.parent
    readiness_summary = summary or summarize_008b_readiness(report_path=output_dir / "factor_008b_readiness.csv")
    strategy_layer = report.setdefault("strategy_layer", {})
    factor_score = strategy_layer.setdefault("factor_score", {})
    if not isinstance(factor_score, dict):
        factor_score = {}
        strategy_layer["factor_score"] = factor_score
    factor_score.update(readiness_summary)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
