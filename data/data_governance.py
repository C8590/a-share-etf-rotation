from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from data.candidate_unblock import summarize_candidate_unblock_plan
from data.index_readiness import summarize_007b_readiness
from data.qa_status import summarize_qa_status
from strategy.factor_readiness import summarize_008b_readiness


DATA_GOVERNANCE_STATUS_REQUIRED_FIELDS = [
    "generated_at",
    "qa_exit_status",
    "data_quality_failed_count",
    "end_date_coverage_gap_days",
    "candidate_total",
    "candidate_eligible_count",
    "candidate_blocked_count",
    "blocked_short_history_count",
    "blocked_manual_review_count",
    "blocked_no_used_factors_count",
    "observation_pool_count",
    "very_short_history_count",
    "estimated_eligible_within_20d_count",
    "estimated_eligible_within_60d_count",
    "manual_review_count",
    "factor_gate_status",
    "allowed_to_enter_008b",
    "allowed_to_enter_007b",
    "next_recommended_action",
    "blocking_reasons",
    "report_paths",
]

RUNBOOK_PATH = Path("docs") / "research" / "data_governance_runbook.md"
STATUS_PATH = Path("output") / "data_governance_status.json"


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


def _bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "yes", "y"])


def _summary_count(summary: pd.DataFrame, item_column: str, item: str) -> int:
    if summary.empty or item_column not in summary.columns or "count" not in summary.columns:
        return 0
    rows = summary.loc[summary[item_column].astype(str).eq(item), "count"]
    if rows.empty:
        return 0
    parsed = pd.to_numeric(rows.iloc[0], errors="coerce")
    return 0 if pd.isna(parsed) else int(float(parsed))


def _parse_gap_days(reasons: list[Any]) -> int:
    for reason in reasons:
        match = re.search(r"coverage gap is\s+(\d+)\s+days", str(reason))
        if match:
            return int(match.group(1))
    return 0


def _factor_gate_status(factor_gate: pd.DataFrame, qa_report: dict[str, Any]) -> str:
    factor_score = qa_report.get("strategy_layer", {}).get("factor_score", {})
    if isinstance(factor_score, dict) and factor_score.get("gate_status"):
        return str(factor_score["gate_status"])
    if factor_gate.empty:
        return "not_run"
    blocking = factor_gate.get("blocking", pd.Series(dtype=object)).astype(str).str.lower().isin(["true", "1", "yes"])
    blocked = factor_gate.get("status", pd.Series(dtype=object)).astype(str).eq("blocked")
    warning = factor_gate.get("status", pd.Series(dtype=object)).astype(str).eq("warning")
    if bool((blocking & blocked).any()):
        return "blocked_for_strategy_use"
    if bool(warning.any()):
        return "warning_observation_only"
    return "passed_for_candidate_research"


def _report_paths(output_dir: Path) -> dict[str, str]:
    return {
        "data_quality_diagnosis": str(output_dir / "data_quality_diagnosis.csv"),
        "data_quality_diagnosis_summary": str(output_dir / "data_quality_diagnosis_summary.csv"),
        "candidate_gate": str(output_dir / "candidate_gate.csv"),
        "candidate_gate_summary": str(output_dir / "candidate_gate_summary.csv"),
        "short_history_observation_pool": str(output_dir / "short_history_observation_pool.csv"),
        "short_history_observation_summary": str(output_dir / "short_history_observation_summary.csv"),
        "manual_review_list": str(output_dir / "manual_review_list.csv"),
        "manual_review_summary": str(output_dir / "manual_review_summary.csv"),
        "factor_score_gate": str(output_dir / "factor_score_gate.csv"),
        "qa_status_breakdown": str(output_dir / "qa_status_breakdown.csv"),
        "qa_status_summary": str(output_dir / "qa_status_summary.csv"),
        "candidate_unblock_plan": str(output_dir / "candidate_unblock_plan.csv"),
        "candidate_unblock_summary": str(output_dir / "candidate_unblock_summary.csv"),
        "factor_008b_readiness": str(output_dir / "factor_008b_readiness.csv"),
        "factor_008b_readiness_summary": str(output_dir / "factor_008b_readiness_summary.csv"),
        "index_007b_readiness": str(output_dir / "index_007b_readiness.csv"),
        "index_007b_unlock_plan": str(output_dir / "index_007b_unlock_plan.csv"),
        "index_007b_readiness_summary": str(output_dir / "index_007b_readiness_summary.csv"),
        "qa_report": str(output_dir / "qa_report.json"),
        "data_governance_status": str(output_dir / "data_governance_status.json"),
        "data_governance_runbook": str(RUNBOOK_PATH),
    }


def build_data_governance_status(
    *,
    output_dir: str | Path = "output",
    diagnosis: pd.DataFrame | None = None,
    diagnosis_summary: pd.DataFrame | None = None,
    candidate_gate: pd.DataFrame | None = None,
    candidate_gate_summary: pd.DataFrame | None = None,
    observation_pool: pd.DataFrame | None = None,
    observation_summary: pd.DataFrame | None = None,
    manual_review: pd.DataFrame | None = None,
    manual_review_summary: pd.DataFrame | None = None,
    factor_gate: pd.DataFrame | None = None,
    qa_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    diagnosis_frame = diagnosis if diagnosis is not None else _read_csv(output_path / "data_quality_diagnosis.csv")
    diagnosis_summary_frame = diagnosis_summary if diagnosis_summary is not None else _read_csv(output_path / "data_quality_diagnosis_summary.csv")
    candidate_frame = candidate_gate if candidate_gate is not None else _read_csv(output_path / "candidate_gate.csv")
    candidate_summary_frame = candidate_gate_summary if candidate_gate_summary is not None else _read_csv(output_path / "candidate_gate_summary.csv")
    observation_frame = observation_pool if observation_pool is not None else _read_csv(output_path / "short_history_observation_pool.csv")
    observation_summary_frame = observation_summary if observation_summary is not None else _read_csv(output_path / "short_history_observation_summary.csv")
    manual_frame = manual_review if manual_review is not None else _read_csv(output_path / "manual_review_list.csv")
    manual_summary_frame = manual_review_summary if manual_review_summary is not None else _read_csv(output_path / "manual_review_summary.csv")
    factor_gate_frame = factor_gate if factor_gate is not None else _read_csv(output_path / "factor_score_gate.csv")
    qa = qa_report if qa_report is not None else _read_json(output_path / "qa_report.json")

    data_layer = qa.get("data_layer", {}) if isinstance(qa.get("data_layer"), dict) else {}
    strategy_layer = qa.get("strategy_layer", {}) if isinstance(qa.get("strategy_layer"), dict) else {}
    reasons = list(data_layer.get("reasons", []))
    candidate_total = int(len(candidate_frame)) if not candidate_frame.empty else _summary_count(candidate_summary_frame, "gate_item", "total")
    candidate_eligible = (
        int(candidate_frame["candidate_status"].eq("eligible").sum())
        if not candidate_frame.empty and "candidate_status" in candidate_frame.columns
        else _summary_count(candidate_summary_frame, "gate_item", "eligible")
    )
    candidate_blocked = (
        int(_bool_series(candidate_frame["blocked"]).sum())
        if not candidate_frame.empty and "blocked" in candidate_frame.columns
        else _summary_count(candidate_summary_frame, "gate_item", "blocked")
    )
    blocked_short_history = _summary_count(candidate_summary_frame, "gate_item", "blocked_short_history")
    if not blocked_short_history and not candidate_frame.empty and "candidate_status" in candidate_frame.columns:
        blocked_short_history = int(candidate_frame["candidate_status"].eq("blocked_short_history").sum())
    blocked_manual_review = _summary_count(candidate_summary_frame, "gate_item", "blocked_manual_review")
    if not blocked_manual_review and not candidate_frame.empty and "candidate_status" in candidate_frame.columns:
        blocked_manual_review = int(candidate_frame["candidate_status"].eq("blocked_manual_review").sum())
    blocked_no_used_factors = _summary_count(candidate_summary_frame, "gate_item", "blocked_no_used_factors")
    if not blocked_no_used_factors and not candidate_frame.empty and "candidate_status" in candidate_frame.columns:
        blocked_no_used_factors = int(candidate_frame["candidate_status"].eq("blocked_no_used_factors").sum())

    observation_count = int(len(observation_frame)) if not observation_frame.empty else _summary_count(observation_summary_frame, "summary_item", "total_observation_count")
    very_short_history = _summary_count(observation_summary_frame, "summary_item", "very_short_history")
    if not very_short_history and not observation_frame.empty and "history_status" in observation_frame.columns:
        very_short_history = int(observation_frame["history_status"].eq("very_short_history").sum())
    estimated_20 = _summary_count(observation_summary_frame, "summary_item", "estimated_eligible_within_20d")
    estimated_60 = _summary_count(observation_summary_frame, "summary_item", "estimated_eligible_within_60d")
    manual_count = int(len(manual_frame)) if not manual_frame.empty else _summary_count(manual_summary_frame, "review_item", "manual_review_count")
    data_quality_failed = int(len(diagnosis_frame)) if not diagnosis_frame.empty else _summary_count(diagnosis_summary_frame, "diagnosis_item", "short_history")
    factor_status = _factor_gate_status(factor_gate_frame, qa)
    usable_benchmark_count = int(data_layer.get("index_data", {}).get("usable_benchmark_count", 0)) if isinstance(data_layer.get("index_data"), dict) else 0
    qa_passed = bool(data_layer.get("passed", False)) and bool(strategy_layer.get("passed", False)) and bool(qa.get("output_layer", {}).get("passed", False))

    blocking_reasons: list[str] = []
    if data_quality_failed > 0:
        blocking_reasons.append("data_quality_failed")
    if _parse_gap_days(reasons) > 0:
        blocking_reasons.append("end_date_coverage_gap")
    if blocked_short_history > 0:
        blocking_reasons.append("short_history")
    if manual_count > 0 or blocked_manual_review > 0:
        blocking_reasons.append("manual_review_required")
    if blocked_no_used_factors > 0:
        blocking_reasons.append("no_used_factors")
    if candidate_eligible <= 0:
        blocking_reasons.append("no_candidate_eligible")
    if candidate_blocked > 0:
        blocking_reasons.append("candidate_gate_blocked")
    if factor_status != "passed_for_candidate_research":
        blocking_reasons.append("factor_score_gate_blocked")
    if usable_benchmark_count <= 0:
        blocking_reasons.append("no_usable_benchmark")
    blocking_reasons = list(dict.fromkeys(blocking_reasons + [str(item) for item in reasons]))

    allowed_008b = (
        qa_passed
        and data_quality_failed == 0
        and blocked_short_history == 0
        and manual_count == 0
        and candidate_eligible > 0
        and candidate_blocked == 0
        and blocked_no_used_factors == 0
        and factor_status == "passed_for_candidate_research"
    )
    allowed_007b = qa_passed and usable_benchmark_count > 0

    if manual_count > 0:
        next_action = "complete P0 manual review list and keep affected ETFs blocked"
    elif blocked_short_history > 0:
        next_action = "wait for history accumulation; rerun observation pool and candidate gate after row counts reach minimum"
    elif blocked_no_used_factors > 0 or factor_status != "passed_for_candidate_research":
        next_action = "fix factor/source coverage and rerun factor score gate"
    elif usable_benchmark_count <= 0:
        next_action = "confirm benchmark mappings and schema-valid index cache before benchmark-dependent work"
    elif candidate_eligible <= 0:
        next_action = "rerun candidate gate after data and factor gates clear"
    else:
        next_action = "review governance status and consider controlled next-stage research"

    qa_status_summary = summarize_qa_status(_read_csv(output_path / "qa_status_breakdown.csv"))
    actionable_failures = qa_status_summary["refresh_action_count"] + qa_status_summary["manual_review_action_count"]
    candidate_unblock_summary = summarize_candidate_unblock_plan(report_path=output_path / "candidate_unblock_plan.csv")
    factor_008b_summary = summarize_008b_readiness(report_path=output_path / "factor_008b_readiness.csv")
    index_007b_summary = summarize_007b_readiness(report_path=output_path / "index_007b_readiness.csv")
    if index_007b_summary["readiness_status"] != "not_run":
        allowed_007b = allowed_007b and bool(index_007b_summary["allowed_to_enter_007b"])

    return {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "qa_exit_status": "passed" if qa.get("allow_small_observation") else "failed",
        "data_quality_failed_count": data_quality_failed,
        "end_date_coverage_gap_days": _parse_gap_days(reasons),
        "candidate_total": candidate_total,
        "candidate_eligible_count": candidate_eligible,
        "candidate_blocked_count": candidate_blocked,
        "blocked_short_history_count": blocked_short_history,
        "blocked_manual_review_count": blocked_manual_review,
        "blocked_no_used_factors_count": blocked_no_used_factors,
        "observation_pool_count": observation_count,
        "very_short_history_count": very_short_history,
        "estimated_eligible_within_20d_count": estimated_20,
        "estimated_eligible_within_60d_count": estimated_60,
        "manual_review_count": manual_count,
        "factor_gate_status": factor_status,
        "allowed_to_enter_008b": bool(allowed_008b),
        "allowed_to_enter_007b": bool(allowed_007b),
        "next_recommended_action": next_action,
        "blocking_reasons": blocking_reasons,
        "qa_status": qa_status_summary,
        "governed_failures": qa_status_summary["governed_failure_count"],
        "actionable_failures": actionable_failures,
        "next_refresh_action": "run update-data only in controlled environment or diagnose source lag" if qa_status_summary["refresh_action_count"] else "no refresh action from qa_status",
        "next_manual_review_action": "complete P0 manual review list; do not auto-unblock" if qa_status_summary["manual_review_action_count"] else "no manual review action from qa_status",
        "candidate_unblock_status": candidate_unblock_summary,
        "immediate_eligible_count": candidate_unblock_summary["immediate_eligible_count"],
        "estimated_unblockable_by_waiting_count": candidate_unblock_summary["estimated_unblockable_by_waiting_count"],
        "candidate_next_action": candidate_unblock_summary["next_recommended_action"],
        "factor_008b_readiness_status": factor_008b_summary["readiness_status"],
        "factor_008b_blockers": factor_008b_summary["blocking_items"],
        "factor_008b_next_action": factor_008b_summary["next_recommended_action"],
        "index_007b_readiness_status": index_007b_summary["readiness_status"],
        "index_007b_blockers": index_007b_summary["blocking_items"],
        "index_007b_next_action": index_007b_summary["next_recommended_action"],
        "report_paths": _report_paths(output_path),
    }


def write_data_governance_status(status: dict[str, Any], *, path: str | Path = STATUS_PATH) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _yes_no(value: Any) -> str:
    return "YES" if bool(value) else "NO"


def build_data_governance_runbook_text(status: dict[str, Any]) -> str:
    paths = status.get("report_paths", {})
    blocking = status.get("blocking_reasons", [])
    return f"""# ETF Data Governance Runbook

Generated from existing reports only. This runbook does not refresh cache, change strategy output, replace `compare_signal`, alter backtest returns, modify UI behavior, clear blockers, or relax QA.

## Current Data Governance Status

- QA exit status: `{status.get("qa_exit_status")}`
- Data quality failed ETF count: `{status.get("data_quality_failed_count")}`
- ETF end-date coverage gap days: `{status.get("end_date_coverage_gap_days")}`
- Candidate gate: `{status.get("candidate_eligible_count")}` eligible / `{status.get("candidate_blocked_count")}` blocked / `{status.get("candidate_total")}` total
- Blocked short history: `{status.get("blocked_short_history_count")}`
- P0 manual review: `{status.get("manual_review_count")}`
- Blocked no-used-factors: `{status.get("blocked_no_used_factors_count")}`
- Observation pool count: `{status.get("observation_pool_count")}`
- Very short history count: `{status.get("very_short_history_count")}`
- Estimated eligible within 20 trading days: `{status.get("estimated_eligible_within_20d_count")}`
- Estimated eligible within 60 trading days: `{status.get("estimated_eligible_within_60d_count")}`
- Factor score gate status: `{status.get("factor_gate_status")}`
- Allowed to enter ETF-GAP-008B: `{_yes_no(status.get("allowed_to_enter_008b"))}`
- Allowed to enter ETF-GAP-007B: `{_yes_no(status.get("allowed_to_enter_007b"))}`
- QA status hard failure rows: `{status.get("qa_status", {}).get("hard_failure_count", 0)}`
- QA status governed failure rows: `{status.get("governed_failures", 0)}`
- QA status refresh-action count: `{status.get("qa_status", {}).get("refresh_action_count", 0)}`
- QA status wait-for-history count: `{status.get("qa_status", {}).get("wait_for_history_count", 0)}`
- QA status manual-review action count: `{status.get("qa_status", {}).get("manual_review_action_count", 0)}`
- Candidate unblock immediate eligible count: `{status.get("immediate_eligible_count", 0)}`
- Candidate unblock wait-for-history count: `{status.get("candidate_unblock_status", {}).get("wait_for_history_count", 0)}`
- Candidate unblock manual-review count: `{status.get("candidate_unblock_status", {}).get("manual_review_required_count", 0)}`
- Candidate unblock no-used-factors count: `{status.get("candidate_unblock_status", {}).get("no_used_factors_count", 0)}`
- Factor 008B readiness status: `{status.get("factor_008b_readiness_status", "not_run")}`
- Factor 008B blocker count: `{len(status.get("factor_008b_blockers", []))}`
- Factor 008B next action: {status.get("factor_008b_next_action", "not_run")}
- Index 007B readiness status: `{status.get("index_007b_readiness_status", "not_run")}`
- Index 007B blocker count: `{len(status.get("index_007b_blockers", []))}`
- Index 007B next action: {status.get("index_007b_next_action", "not_run")}

Current blocking reasons:

{chr(10).join(f"- `{item}`" for item in blocking) if blocking else "- None"}

Next recommended action: {status.get("next_recommended_action")}

## Output File Roles

- `output/data_quality_diagnosis.csv`: root-cause diagnosis for failed ETF data-quality rows.
- `output/data_quality_diagnosis_summary.csv`: aggregate counts for the diagnosis report.
- `output/candidate_gate.csv`: pre-candidate eligibility gate. It blocks short history, manual review, no-used-factors, and factor-gate failures from candidate construction.
- `output/candidate_gate_summary.csv`: aggregate candidate-gate findings.
- `output/short_history_observation_pool.csv`: watchlist for short-history ETFs. It estimates rows needed and possible eligible dates without promoting candidates.
- `output/short_history_observation_summary.csv`: aggregate observation-pool status.
- `output/manual_review_list.csv`: P0 manual-review checklist for ETFs that require human confirmation.
- `output/manual_review_summary.csv`: aggregate manual-review counts.
- `output/factor_score_gate.csv`: factor-score gate findings. Blocking rows mean factor scores cannot drive candidate construction.
- `output/qa_status_breakdown.csv`: QA failure actionability split. It explains what can be fixed by waiting, manual review, controlled refresh/source diagnosis, or source repair.
- `output/qa_status_summary.csv`: aggregate QA-status findings and next actions.
- `output/candidate_unblock_plan.csv`: per-symbol unblock path plan. It does not mark any ETF eligible.
- `output/candidate_unblock_summary.csv`: aggregate candidate-unblock counts and next actions.
- `output/factor_008b_readiness.csv`: ETF-GAP-008B readiness check. It does not generate candidates or change factor scores.
- `output/factor_008b_readiness_summary.csv`: aggregate 008B readiness blockers and warning items.
- `output/index_007b_readiness.csv`: ETF-GAP-007B benchmark/index readiness precheck. It does not enter 007B or calculate benchmark-relative metrics.
- `output/index_007b_unlock_plan.csv`: ETF-level path to unlock real benchmark metrics after confirmed mapping and schema-valid index cache exist.
- `output/index_007b_readiness_summary.csv`: aggregate 007B readiness blockers, warnings, and unlock priorities.
- `output/qa_report.json`: top-level QA and report summary.
- `output/data_governance_status.json`: machine-readable governance status generated by `summarize-data-governance`.

## How To Read `qa_status_breakdown.csv`

Use `qa_item`, `actionability`, `root_cause`, `governed_by`, and the `blocks_*` columns.

- `wait_for_history` means the ETF is not a refresh target; it needs enough trading rows and a rerun of the gates.
- `refresh_needed` means a controlled update or source-lag diagnosis may be appropriate, but this runbook does not refresh data.
- `manual_review` means a human must verify the listed evidence and no automatic unblocking is allowed.
- `source_unavailable` means benchmark or source dependencies are absent, blocking ETF-GAP-007B.
- `governance_blocked` means the issue is already represented by a gate and still blocks candidate use.
- `already_governed` means the failure is explained by reports but remains a hard QA failure.

## How To Read `data_quality_diagnosis.csv`

Use `primary_failure_type`, `history_status`, `cache_status`, `liquidity_status`, `price_quality_status`, `strategy_eligibility`, and `remediation_priority`.

- `new_etf_short_history` means wait for history; it is not a refresh instruction.
- `blocked_short_history` means do not score or promote into candidates.
- `P0_manual_review` means suspicious evidence must be reviewed before any candidate use.
- `requires_refresh=False` confirms the current short-history set is not a cache-refresh queue.
- `low_liquidity` is a tradability risk, not a low score.

## How To Read `candidate_gate.csv`

Use `candidate_status`, `gate_passed`, `blocked`, `block_reason`, and `observation_reason`.

- `eligible` is the only status that can be considered for future candidate research, and only if factor gate and QA also pass.
- `blocked_short_history` waits for minimum history.
- `blocked_manual_review` waits for a human review conclusion.
- `blocked_no_used_factors` is unscoreable evidence, not bearish evidence.
- `blocked_factor_gate` means the global factor-score gate blocks candidate research.

## How To Read `candidate_unblock_plan.csv`

Use `unblock_path`, `unblock_status`, `required_conditions`, `still_blocked_after_primary_fix`, and `next_action`.

- `wait_for_history` means the primary path is row-count accumulation; it is not a refresh instruction.
- `manual_review_required` means a human review must be completed before rerunning gates.
- `benchmark_dependency_missing` means benchmark/index cache work is still required before benchmark-dependent factors can help.
- `no_used_factors` means the row is unscoreable, never a low-score candidate.
- `still_blocked_after_primary_fix=True` means another blocker, usually global factor gate or manual review, remains after the primary row-level condition.

## How To Read `factor_008b_readiness.csv`

Use `readiness_item`, `blocking`, `blocker_type`, `dependency`, `remediation_action`, and `prerequisite_task`.

- `candidate_eligible_count` and `factor_gate_status` are hard 008B entry gates.
- `short_history_bias` must be fixed by waiting for history and rerunning governance reports, not by scoring it low.
- `no_used_factors` means no usable enabled evidence; never fill missing values with zero.
- `tracking_error_dependency` and `relative_return_dependency` require schema-valid benchmark/index cache.
- `discount_premium_dependency` requires NAV/IOPV data; exchange prices are not a substitute.
- `fund_size_dependency` and `management_fee_dependency` remain metadata/config warnings until coverage is trustworthy.

## How To Read `index_007b_readiness.csv`

Use `readiness_item`, `blocking`, `blocker_type`, `dependency`, `remediation_action`, and `prerequisite_task`.

- `usable_benchmark_count`, `index_cache_exists`, and `index_cache_schema_valid` are hard 007B entry gates.
- `tracking_error_computable_count` and `relative_return_computable_count` must be greater than zero before entering 007B.
- `index_source_network_available` and `eastmoney_proxy_failure` identify work that must be done in a network/proxy-enabled environment.
- `benchmark_mapping_confidence` allows only `config_manual` or `metadata_exact` hard mappings; `name_inferred` and `unable_to_confirm` remain review-only.
- `no_fake_benchmark_guard` must always pass. ETF own prices must never be used as benchmark substitutes.

## How To Read `index_007b_unlock_plan.csv`

Use `unlock_priority`, `required_action`, `eligible_for_007b_after_unlock`, and the cache/source status columns.

- `P0_get_index_cache` means a confirmed mapping exists, but real schema-valid index cache is missing.
- `P1_validate_mapping` and `P3_manual_review` mean the ETF cannot become a hard benchmark row until mapping evidence improves.
- `P1_fix_index_schema` means a cache file exists but cannot pass the required index-cache schema.
- `P2_wait_for_network` means source checks must be rerun where network/proxy access works.
- `eligible_for_007b_after_unlock=True` means the ETF can be considered for a small-scope 007B run only after cache and metric gates are real.

## How To Read `short_history_observation_pool.csv`

Use `rows_needed`, `estimated_trading_days_until_eligible`, `estimated_calendar_date_until_eligible`, `observation_status`, and `observation_priority`.

- `rows_needed = max(min_required_rows - row_count, 0)`.
- `estimated_calendar_date_until_eligible=unknown` means the calendar snapshot does not reach far enough.
- `waiting_for_history` rows may be revisited after enough rows accumulate.
- `waiting_but_low_liquidity` rows need both history and tradability review.
- `P0_manual_review` rows cannot be promoted by waiting alone.

## How To Read `manual_review_list.csv`

Use `manual_review_reason`, `evidence_fields`, `recommended_checks`, `possible_outcomes`, and `review_status`.

- `blocked_until_review` means the ETF remains blocked until a human conclusion is recorded.
- `abnormal_return_flag=True` requires return-outlier and source/adjustment checks.
- `low_liquidity_flag=True` requires tradability checks.
- `history_status=very_short_history` means even a clean review still needs history accumulation.
- `possible_outcomes` are handling directions, not automatic decisions.

## How To Read `factor_score_gate.csv`

Use `status`, `blocking`, `gate_item`, `actual_value`, and `suggested_action`.

- Any row with `blocking=True` and `status=blocked` keeps factor scores in observation mode.
- `min_computable_ratio`, `max_unable_to_score_ratio`, and `factor_coverage_minimum` describe coverage readiness.
- `no_short_history_bias` prevents short-history rows from becoming apparent factor winners.
- Benchmark, NAV/IOPV, and source-unavailable gates must clear before factor scores can support ETF-GAP-008B.

## Unblocking Rules

### `blocked_short_history`

Can be reconsidered only after:

- `row_count >= min_required_rows`
- data-quality checks no longer report short-history failure
- no manual-review blocker remains
- candidate gate is rerun from current reports
- QA hard gates remain unchanged and still enforce failures

### `manual_review_required`

Can be reconsidered only after:

- a human review conclusion is recorded in a separate audit trail
- abnormal-return, unknown-quality, or source/adjustment evidence is explained
- any targeted refresh or repair, if chosen later, is separately approved and rechecked
- diagnosis, observation pool, manual-review list, and candidate gate are rerun

This runbook does not clear `manual_review_required`.

### `no_used_factors`

Treat as unscoreable, not as a low score. Improve source/factor coverage and rerun factor scoring. Do not rank or penalize these ETFs as candidates.

### `low_liquidity`

Keep as an observation/tradability risk. It is not a permanent exclusion by itself and is not a score penalty. Recheck volume/amount evidence before any strategy use.

## When To Rerun Commands

- `diagnose-data-quality`: rerun after new ETF cache data exists, after targeted repair/refresh tasks, or after quality-report inputs change.
- `build-candidate-gate`: rerun after diagnosis, factor-score reports, or factor-score gate changes.
- `build-observation-pool`: rerun after diagnosis changes or after enough new trading days accumulate.
- `build-manual-review-list`: rerun after diagnosis, observation pool, candidate gate, or human-review evidence changes.
- `summarize-data-governance`: rerun after any upstream governance report changes.

## ETF-GAP-008B Entry Rule

Still forbidden when any of the following hold:

- data quality failed count is nonzero
- end-date coverage gap remains a QA blocker
- `candidate_eligible_count == 0`
- candidate gate has blocked rows
- `blocked_short_history_count > 0`
- `manual_review_count > 0`
- `blocked_no_used_factors_count > 0`
- `factor_gate_status != passed_for_candidate_research`

ETF-GAP-008B can only be considered after QA, candidate gate, manual review, short-history, factor coverage, and source dependency gates are all clean.

## ETF-GAP-007B Entry Rule

ETF-GAP-007B can only be considered when benchmark/index evidence is usable. At minimum, `usable_benchmark_count > 0` and the relevant index cache/schema checks must be clean. If `usable_benchmark_count == 0`, ETF-GAP-007B remains blocked.

## Report Paths

{chr(10).join(f"- `{key}`: `{value}`" for key, value in sorted(paths.items()))}
"""


def write_data_governance_runbook(status: dict[str, Any], *, path: str | Path = RUNBOOK_PATH) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_data_governance_runbook_text(status), encoding="utf-8")
    return out


def merge_data_governance_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    status: dict[str, Any] | None = None,
) -> bool:
    path = Path(qa_report_path)
    if not path.exists():
        return False
    current_status = status if status is not None else _read_json(path.parent / "data_governance_status.json")
    if not current_status:
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    data_layer = report.setdefault("data_layer", {})
    summary = {
        "data_governance_runbook": str(RUNBOOK_PATH),
        "data_governance_status_report": str(path.parent / "data_governance_status.json"),
        "allowed_to_enter_008b": bool(current_status.get("allowed_to_enter_008b", False)),
        "allowed_to_enter_007b": bool(current_status.get("allowed_to_enter_007b", False)),
        "next_recommended_action": str(current_status.get("next_recommended_action", "")),
        "blocking_reasons": list(current_status.get("blocking_reasons", [])),
    }
    data_layer["data_governance"] = summary
    data_layer.update(summary)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
