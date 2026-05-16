from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


MANUAL_REVIEW_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "review_priority",
    "review_status",
    "manual_review_reason",
    "primary_failure_type",
    "secondary_failure_type",
    "history_status",
    "row_count",
    "min_required_rows",
    "rows_needed",
    "first_date",
    "last_date",
    "latest_expected_date",
    "end_date_gap_days",
    "liquidity_status",
    "abnormal_return_flag",
    "low_liquidity_flag",
    "missing_cache_flag",
    "cache_status",
    "candidate_status",
    "observation_status",
    "evidence_fields",
    "recommended_checks",
    "possible_outcomes",
    "recommended_action",
    "notes",
]

MANUAL_REVIEW_SUMMARY_COLUMNS = [
    "review_item",
    "count",
    "ratio",
    "severity",
    "examples",
    "suggested_action",
    "notes",
]

REVIEW_PRIORITIES = {"P0_manual_review", "P1_data_watch", "P2_metadata_check"}
REVIEW_STATUSES = {
    "pending_manual_review",
    "evidence_incomplete",
    "ready_for_review",
    "blocked_until_review",
    "unknown",
}


def _read_csv(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    return pd.read_csv(file_path, dtype={"symbol": str}, encoding="utf-8-sig").fillna("")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _int_value(value: Any, default: int = 0) -> int:
    parsed = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(parsed) else int(float(parsed))


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else str(parsed.date())


def _symbol_map(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    return {
        str(row.get("symbol", "")).zfill(6): row
        for row in frame.to_dict("records")
        if _text(row.get("symbol"))
    }


def _failure_map(frame: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    if frame.empty or "symbol" not in frame.columns:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for row in frame.to_dict("records"):
        symbol = str(row.get("symbol", "")).zfill(6)
        if symbol:
            out.setdefault(symbol, []).append(row)
    return out


def _split_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for raw in _text(value).replace("|", ";").split(";"):
            item = raw.strip()
            if item:
                tokens.add(item)
    return tokens


def validate_manual_review_inputs(
    *,
    diagnosis: pd.DataFrame,
    observation_pool: pd.DataFrame | None = None,
    candidate_gate: pd.DataFrame | None = None,
    metadata: pd.DataFrame | None = None,
) -> dict[str, Any]:
    required = {
        "diagnosis": {"symbol", "requires_manual_review", "failure_type", "history_status", "cache_status"},
    }
    optional = {
        "observation_pool": {"symbol", "observation_status", "rows_needed", "low_liquidity_flag", "abnormal_return_flag"},
        "candidate_gate": {"symbol", "candidate_status", "block_reason"},
        "metadata": {"symbol", "name"},
    }
    frames = {
        "diagnosis": diagnosis,
        "observation_pool": observation_pool if observation_pool is not None else pd.DataFrame(),
        "candidate_gate": candidate_gate if candidate_gate is not None else pd.DataFrame(),
        "metadata": metadata if metadata is not None else pd.DataFrame(),
    }
    missing_required = {
        name: sorted(columns - set(frames[name].columns))
        for name, columns in required.items()
        if columns - set(frames[name].columns)
    }
    missing_optional = {
        name: sorted(columns - set(frames[name].columns))
        for name, columns in optional.items()
        if not frames[name].empty and columns - set(frames[name].columns)
    }
    return {
        "valid": not missing_required,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
    }


def classify_manual_review_reason(
    *,
    diagnosis_row: dict[str, Any],
    observation_row: dict[str, Any] | None = None,
    failure_rows: list[dict[str, Any]] | None = None,
) -> str:
    observation = observation_row or {}
    failures = failure_rows or []
    tokens = _split_tokens(
        diagnosis_row.get("failure_type"),
        diagnosis_row.get("secondary_failure_type"),
        observation.get("manual_review_reason"),
        ";".join(_text(row.get("failure_type")) for row in failures),
    )
    reasons: list[str] = []
    if "abnormal_return" in tokens:
        reasons.append("abnormal_return")
    if "unknown" in tokens:
        reasons.append("unknown_quality_finding")
    if _text(diagnosis_row.get("price_quality_status")) not in {"", "ok", "unknown"}:
        reasons.append(_text(diagnosis_row.get("price_quality_status")))
    if _text(diagnosis_row.get("history_status")) == "very_short_history":
        reasons.append("very_short_history")
    if _text(diagnosis_row.get("liquidity_status")) == "low_liquidity" or _bool_value(observation.get("low_liquidity_flag")):
        reasons.append("low_liquidity")
    if not reasons:
        reason_text = _text(diagnosis_row.get("reason")) or _text(observation.get("manual_review_reason"))
        reasons.append(reason_text or "manual_review_required")
    return ";".join(dict.fromkeys(reasons))


def _review_priority(requires_manual_review: bool, metadata_status: str = "") -> str:
    if requires_manual_review:
        return "P0_manual_review"
    if metadata_status in {"missing", "warning"}:
        return "P2_metadata_check"
    return "P1_data_watch"


def _review_status(*, requires_manual_review: bool, evidence_incomplete: bool, candidate_status: str) -> str:
    if evidence_incomplete:
        return "evidence_incomplete"
    if requires_manual_review and candidate_status == "blocked_manual_review":
        return "blocked_until_review"
    if requires_manual_review:
        return "pending_manual_review"
    return "unknown"


def _evidence_fields(
    *,
    diagnosis_row: dict[str, Any],
    observation_row: dict[str, Any],
    candidate_row: dict[str, Any],
    quality_row: dict[str, Any],
    failure_rows: list[dict[str, Any]],
) -> str:
    tokens = _split_tokens(diagnosis_row.get("failure_type"), diagnosis_row.get("secondary_failure_type"))
    evidence: list[str] = []
    for item in ["abnormal_return", "insufficient_rows", "unknown", "zero_or_low_liquidity", "low_liquidity"]:
        if item in tokens:
            evidence.append(item)
    history_status = _text(diagnosis_row.get("history_status"))
    if history_status:
        evidence.append(history_status)
    price_status = _text(diagnosis_row.get("price_quality_status"))
    if price_status and price_status not in {"ok", "unknown"}:
        evidence.append(f"price_quality_status:{price_status}")
    if _text(diagnosis_row.get("liquidity_status")) == "low_liquidity" or _bool_value(observation_row.get("low_liquidity_flag")):
        evidence.append("low_liquidity")
    if _text(diagnosis_row.get("cache_status")) == "missing":
        evidence.append("missing_cache")
    if _text(candidate_row.get("candidate_status")):
        evidence.append(f"candidate_status:{candidate_row.get('candidate_status')}")
    if _text(observation_row.get("observation_status")):
        evidence.append(f"observation_status:{observation_row.get('observation_status')}")
    if _text(quality_row.get("warnings")):
        evidence.append("data_quality_report:warnings")
    if _text(quality_row.get("errors")):
        evidence.append("data_quality_report:errors")
    if failure_rows:
        evidence.append("data_failure_summary:failure_reason")
    return ";".join(dict.fromkeys([item for item in evidence if item]))


def _recommended_checks(
    *,
    abnormal_return: bool,
    low_liquidity: bool,
    very_short_history: bool,
    missing_cache: bool,
    failure_tokens: set[str],
) -> str:
    checks: list[str] = []
    if abnormal_return:
        checks.append("inspect daily return outlier and adjustment/source consistency")
        checks.append("compare source data before any refresh acceptance")
    if low_liquidity:
        checks.append("verify avg_amount_20 and practical tradability")
    if very_short_history:
        checks.append("confirm listing/new-fund status and wait for minimum history")
    if missing_cache:
        checks.append("confirm cache absence before targeted repair planning")
    if "unknown" in failure_tokens:
        checks.append("inspect quality warnings/errors for unknown validation finding")
    if "insufficient_rows" in failure_tokens:
        checks.append("confirm row_count and rows_needed against minimum history rule")
    if not checks:
        checks.append("review diagnosis, quality report, and candidate gate evidence")
    return ";".join(dict.fromkeys(checks))


def _possible_outcomes(
    *,
    abnormal_return: bool,
    low_liquidity: bool,
    short_history: bool,
    missing_cache: bool,
) -> str:
    outcomes = ["keep_blocked"]
    if short_history:
        outcomes.append("observe_until_history_sufficient")
    if abnormal_return or missing_cache:
        outcomes.append("investigate_source_data")
        outcomes.append("refresh_after_manual_confirmation")
    if low_liquidity:
        outcomes.append("exclude_from_candidate_pool")
    if "exclude_from_candidate_pool" not in outcomes:
        outcomes.append("exclude_from_candidate_pool")
    return ";".join(dict.fromkeys(outcomes))


def _recommended_action(*, abnormal_return: bool, low_liquidity: bool, very_short_history: bool) -> str:
    parts = ["keep blocked until manual review is recorded"]
    if abnormal_return:
        parts.append("verify abnormal return source/adjustment evidence")
    if low_liquidity:
        parts.append("keep liquidity risk visible before any candidate use")
    if very_short_history:
        parts.append("do not promote by manual review alone; history must also accumulate")
    parts.append("do not auto-clear manual_review_required")
    return "; ".join(parts)


def build_manual_review_list(
    *,
    output_dir: str | Path = "output",
    diagnosis_path: str | Path | None = None,
    observation_pool_path: str | Path | None = None,
    candidate_gate_path: str | Path | None = None,
    quality_report_path: str | Path | None = None,
    failure_summary_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
    diagnosis: pd.DataFrame | None = None,
    observation_pool: pd.DataFrame | None = None,
    candidate_gate: pd.DataFrame | None = None,
    quality_report: pd.DataFrame | None = None,
    failure_summary: pd.DataFrame | None = None,
    metadata: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    diagnosis_frame = diagnosis if diagnosis is not None else _read_csv(diagnosis_path or output_path / "data_quality_diagnosis.csv")
    observation_frame = observation_pool if observation_pool is not None else _read_csv(observation_pool_path or output_path / "short_history_observation_pool.csv")
    candidate_frame = candidate_gate if candidate_gate is not None else _read_csv(candidate_gate_path or output_path / "candidate_gate.csv")
    quality_frame = quality_report if quality_report is not None else _read_csv(quality_report_path or output_path / "data_quality_report.csv")
    failure_frame = failure_summary if failure_summary is not None else _read_csv(failure_summary_path or output_path / "data_failure_summary.csv")
    metadata_frame = metadata if metadata is not None else _read_csv(metadata_path or output_path / "etf_metadata.csv")

    validation = validate_manual_review_inputs(
        diagnosis=diagnosis_frame,
        observation_pool=observation_frame,
        candidate_gate=candidate_frame,
        metadata=metadata_frame,
    )
    if not validation["valid"]:
        raise ValueError(f"manual review inputs missing required fields: {validation['missing_required']}")
    if diagnosis_frame.empty:
        return []

    observation_by_symbol = _symbol_map(observation_frame)
    candidate_by_symbol = _symbol_map(candidate_frame)
    quality_by_symbol = _symbol_map(quality_frame)
    metadata_by_symbol = _symbol_map(metadata_frame)
    failures_by_symbol = _failure_map(failure_frame)

    manual_mask = diagnosis_frame["requires_manual_review"].astype(str).str.lower().isin(["true", "1", "yes", "y"])
    rows: list[dict[str, Any]] = []
    for raw in diagnosis_frame.loc[manual_mask].to_dict("records"):
        symbol = str(raw.get("symbol", "")).zfill(6)
        observation = observation_by_symbol.get(symbol, {})
        candidate = candidate_by_symbol.get(symbol, {})
        quality = quality_by_symbol.get(symbol, {})
        meta = metadata_by_symbol.get(symbol, {})
        failures = failures_by_symbol.get(symbol, [])

        failure_tokens = _split_tokens(raw.get("failure_type"), raw.get("secondary_failure_type"))
        abnormal_return = "abnormal_return" in failure_tokens or _bool_value(observation.get("abnormal_return_flag"))
        low_liquidity = (
            _text(raw.get("liquidity_status")) == "low_liquidity"
            or _bool_value(observation.get("low_liquidity_flag"))
            or "low_liquidity" in _text(candidate.get("observation_reason"))
        )
        very_short_history = _text(raw.get("history_status")) == "very_short_history"
        short_history = _text(raw.get("history_status")) in {"short_history", "very_short_history"}
        missing_cache = _text(raw.get("cache_status")) == "missing"
        row_count = _int_value(raw.get("row_count"), default=0)
        min_required_rows = _int_value(raw.get("min_required_rows"), default=250)
        rows_needed = _int_value(observation.get("rows_needed"), default=max(min_required_rows - row_count, 0))
        evidence_incomplete = bool(validation["missing_optional"]) or not candidate or not observation
        candidate_status = _text(candidate.get("candidate_status") or "unknown")
        review_priority = _review_priority(
            _bool_value(raw.get("requires_manual_review")),
            metadata_status=_text(raw.get("metadata_status")),
        )
        review_status = _review_status(
            requires_manual_review=_bool_value(raw.get("requires_manual_review")),
            evidence_incomplete=evidence_incomplete,
            candidate_status=candidate_status,
        )
        out = {
            "symbol": symbol,
            "name": _text(raw.get("name") or candidate.get("name") or observation.get("name") or meta.get("name")),
            "category": _text(raw.get("category") or candidate.get("category") or observation.get("category") or meta.get("category") or meta.get("inferred_category")),
            "sub_category": _text(raw.get("sub_category") or candidate.get("sub_category") or observation.get("sub_category") or meta.get("sub_category")),
            "review_priority": review_priority,
            "review_status": review_status,
            "manual_review_reason": classify_manual_review_reason(
                diagnosis_row=raw,
                observation_row=observation,
                failure_rows=failures,
            ),
            "primary_failure_type": _text(raw.get("primary_failure_type")),
            "secondary_failure_type": _text(raw.get("secondary_failure_type")),
            "history_status": _text(raw.get("history_status") or "unknown"),
            "row_count": row_count,
            "min_required_rows": min_required_rows,
            "rows_needed": rows_needed,
            "first_date": _date_text(raw.get("first_date")),
            "last_date": _date_text(raw.get("last_date")),
            "latest_expected_date": _date_text(raw.get("latest_expected_date")),
            "end_date_gap_days": _int_value(raw.get("end_date_gap_days"), default=0),
            "liquidity_status": _text(raw.get("liquidity_status") or "unknown"),
            "abnormal_return_flag": bool(abnormal_return),
            "low_liquidity_flag": bool(low_liquidity),
            "missing_cache_flag": bool(missing_cache),
            "cache_status": _text(raw.get("cache_status") or "unknown"),
            "candidate_status": candidate_status,
            "observation_status": _text(observation.get("observation_status") or "unknown"),
            "evidence_fields": _evidence_fields(
                diagnosis_row=raw,
                observation_row=observation,
                candidate_row=candidate,
                quality_row=quality,
                failure_rows=failures,
            ),
            "recommended_checks": _recommended_checks(
                abnormal_return=abnormal_return,
                low_liquidity=low_liquidity,
                very_short_history=very_short_history,
                missing_cache=missing_cache,
                failure_tokens=failure_tokens,
            ),
            "possible_outcomes": _possible_outcomes(
                abnormal_return=abnormal_return,
                low_liquidity=low_liquidity,
                short_history=short_history,
                missing_cache=missing_cache,
            ),
            "recommended_action": _recommended_action(
                abnormal_return=abnormal_return,
                low_liquidity=low_liquidity,
                very_short_history=very_short_history,
            ),
            "notes": "; ".join(
                item
                for item in dict.fromkeys(
                    [
                        "manual review report only; does not clear candidate gate block",
                        "cache presence is not a review clearance",
                        _text(raw.get("reason")),
                        _text(raw.get("notes")),
                    ]
                )
                if item
            ),
        }
        rows.append({column: out.get(column, "") for column in MANUAL_REVIEW_COLUMNS})

    priority_order = {"P0_manual_review": 0, "P1_data_watch": 1, "P2_metadata_check": 2}
    rows.sort(key=lambda item: (priority_order.get(str(item["review_priority"]), 9), str(item["symbol"])))
    return rows


def _examples(frame: pd.DataFrame, mask: pd.Series, limit: int = 5) -> str:
    examples = frame.loc[mask, ["symbol", "name"]].head(limit).to_dict("records")
    return ";".join(f"{item['symbol']} {item['name']}" for item in examples)


def _summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    total = max(1, len(frame))
    bool_true = {"true", "1", "yes", "y"}
    abnormal_mask = frame["abnormal_return_flag"].astype(str).str.lower().isin(bool_true)
    low_liquidity_mask = frame["low_liquidity_flag"].astype(str).str.lower().isin(bool_true)
    very_short_mask = frame["history_status"].eq("very_short_history")
    p0_mask = frame["review_priority"].eq("P0_manual_review")
    specs = [
        ("manual_review_count", pd.Series(True, index=frame.index), "high", "keep blocked until human review is recorded", "manual review list size"),
        ("p0_manual_review", p0_mask, "high", "review before any candidate gate reconsideration", "P0 manual review cannot be auto-cleared"),
        ("abnormal_return_review", abnormal_mask, "high", "inspect return outlier and adjustment/source evidence", "abnormal returns may reflect source or adjustment issues"),
        ("low_liquidity_review", low_liquidity_mask, "medium", "verify tradability and liquidity evidence", "low liquidity remains a watch risk"),
        ("very_short_history_review", very_short_mask, "medium", "confirm listing/new-fund status and wait for history", "very short history cannot be promoted by review alone"),
    ]
    out: list[dict[str, Any]] = []
    for item, mask, severity, action, notes in specs:
        count = int(mask.sum())
        out.append(
            {
                "review_item": item,
                "count": count,
                "ratio": round(count / total, 6),
                "severity": severity if count else "info",
                "examples": _examples(frame, mask),
                "suggested_action": action if count else "no action",
                "notes": notes,
            }
        )
    for value, count in frame["review_status"].value_counts().sort_index().items():
        mask = frame["review_status"].eq(value)
        out.append(
            {
                "review_item": f"review_status:{value}",
                "count": int(count),
                "ratio": round(int(count) / total, 6),
                "severity": "high" if str(value) in {"blocked_until_review", "pending_manual_review"} else "medium",
                "examples": _examples(frame, mask),
                "suggested_action": "see per-symbol recommended_action",
                "notes": "grouped by review_status",
            }
        )
    for value, count in frame["review_priority"].value_counts().sort_index().items():
        mask = frame["review_priority"].eq(value)
        out.append(
            {
                "review_item": f"review_priority:{value}",
                "count": int(count),
                "ratio": round(int(count) / total, 6),
                "severity": "high" if str(value) == "P0_manual_review" else "medium",
                "examples": _examples(frame, mask),
                "suggested_action": "see per-symbol recommended_action",
                "notes": "grouped by review_priority",
            }
        )
    return out


def write_manual_review_report(
    rows: list[dict[str, Any]],
    *,
    report_path: str | Path = "output/manual_review_list.csv",
    summary_path: str | Path = "output/manual_review_summary.csv",
) -> tuple[Path, Path]:
    report = Path(report_path)
    summary = Path(summary_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=MANUAL_REVIEW_COLUMNS).to_csv(report, index=False, encoding="utf-8-sig")
    pd.DataFrame(_summary_rows(rows), columns=MANUAL_REVIEW_SUMMARY_COLUMNS).to_csv(summary, index=False, encoding="utf-8-sig")
    return report, summary


def summarize_manual_review(
    rows: list[dict[str, Any]] | None = None,
    *,
    report_path: str | Path | None = None,
    example_limit: int = 10,
) -> dict[str, Any]:
    frame = pd.DataFrame(rows) if rows is not None else _read_csv(report_path or "output/manual_review_list.csv")
    if frame.empty:
        return {
            "manual_review_count": 0,
            "p0_manual_review_count": 0,
            "abnormal_return_review_count": 0,
            "low_liquidity_review_count": 0,
            "very_short_history_review_count": 0,
            "review_status_counts": {},
            "review_priority_counts": {},
            "top_examples": [],
        }
    bool_true = {"true", "1", "yes", "y"}
    top_examples = frame.head(example_limit)[
        [
            "symbol",
            "name",
            "manual_review_reason",
            "review_priority",
            "review_status",
            "recommended_action",
        ]
    ].to_dict("records")
    return {
        "manual_review_count": int(len(frame)),
        "p0_manual_review_count": int(frame["review_priority"].eq("P0_manual_review").sum()),
        "abnormal_return_review_count": int(frame["abnormal_return_flag"].astype(str).str.lower().isin(bool_true).sum()),
        "low_liquidity_review_count": int(frame["low_liquidity_flag"].astype(str).str.lower().isin(bool_true).sum()),
        "very_short_history_review_count": int(frame["history_status"].eq("very_short_history").sum()),
        "review_status_counts": {str(k): int(v) for k, v in frame["review_status"].value_counts().sort_index().to_dict().items()},
        "review_priority_counts": {str(k): int(v) for k, v in frame["review_priority"].value_counts().sort_index().to_dict().items()},
        "top_examples": top_examples,
    }


def merge_manual_review_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    rows: list[dict[str, Any]] | None = None,
) -> bool:
    path = Path(qa_report_path)
    if not path.exists():
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    output_dir = path.parent
    summary = summarize_manual_review(rows, report_path=output_dir / "manual_review_list.csv")
    data_layer = report.setdefault("data_layer", {})
    data_layer.update(
        {
            "manual_review_list_report": str(output_dir / "manual_review_list.csv"),
            "manual_review_summary_report": str(output_dir / "manual_review_summary.csv"),
            "manual_review": summary,
            "manual_review_count": summary["manual_review_count"],
            "p0_manual_review_count": summary["p0_manual_review_count"],
            "abnormal_return_review_count": summary["abnormal_return_review_count"],
            "low_liquidity_review_count": summary["low_liquidity_review_count"],
            "very_short_history_review_count": summary["very_short_history_review_count"],
            "manual_review_top_examples": summary["top_examples"],
        }
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
