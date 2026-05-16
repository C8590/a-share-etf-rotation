from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


OBSERVATION_POOL_COLUMNS = [
    "symbol",
    "name",
    "category",
    "sub_category",
    "row_count",
    "min_required_rows",
    "rows_needed",
    "first_date",
    "last_date",
    "latest_expected_date",
    "end_date_gap_days",
    "history_status",
    "liquidity_status",
    "observation_status",
    "observation_priority",
    "estimated_trading_days_until_eligible",
    "estimated_calendar_date_until_eligible",
    "requires_manual_review",
    "manual_review_reason",
    "low_liquidity_flag",
    "abnormal_return_flag",
    "candidate_status",
    "recommended_action",
    "notes",
]

OBSERVATION_SUMMARY_COLUMNS = [
    "summary_item",
    "count",
    "ratio",
    "severity",
    "examples",
    "suggested_action",
    "notes",
]

OBSERVATION_STATUSES = {
    "waiting_for_history",
    "very_short_history",
    "waiting_but_low_liquidity",
    "manual_review_required",
    "unknown",
}

OBSERVATION_PRIORITIES = {
    "P0_manual_review",
    "P1_wait_for_history",
    "P2_low_liquidity_watch",
    "P3_archive_watch",
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


def _open_calendar_dates(calendar: pd.DataFrame) -> list[pd.Timestamp]:
    if calendar.empty or "date" not in calendar.columns:
        return []
    frame = calendar.copy()
    if "is_open" in frame.columns:
        mask = frame["is_open"].astype(str).str.lower().isin(["true", "1", "yes", "y", "open"])
        frame = frame.loc[mask]
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna().sort_values()
    return [date.normalize() for date in dates.tolist()]


def estimate_days_until_eligible(
    *,
    row_count: Any,
    min_required_rows: Any = 250,
    calendar: pd.DataFrame | None = None,
    anchor_date: Any = "",
) -> dict[str, Any]:
    rows = _int_value(row_count, default=0)
    minimum = _int_value(min_required_rows, default=250)
    rows_needed = max(minimum - rows, 0)
    anchor = pd.to_datetime(anchor_date, errors="coerce")
    estimated_date = "unknown"
    if rows_needed == 0:
        estimated_date = _date_text(anchor_date) or "unknown"
    elif calendar is not None and not pd.isna(anchor):
        future_dates = [date for date in _open_calendar_dates(calendar) if date > anchor.normalize()]
        if len(future_dates) >= rows_needed:
            estimated_date = str(future_dates[rows_needed - 1].date())
    return {
        "rows_needed": rows_needed,
        "estimated_trading_days_until_eligible": rows_needed,
        "estimated_calendar_date_until_eligible": estimated_date,
    }


def _manual_review_reason(row: dict[str, Any]) -> str:
    reasons: list[str] = []
    failure_type = _text(row.get("failure_type"))
    price_quality_status = _text(row.get("price_quality_status"))
    reason = _text(row.get("reason"))
    if "abnormal_return" in failure_type:
        reasons.append("abnormal_return")
    if price_quality_status and price_quality_status not in {"ok", "unknown"}:
        reasons.append(price_quality_status)
    if reason and not reasons:
        reasons.append(reason)
    return ";".join(dict.fromkeys(reasons))


def classify_observation_priority(
    *,
    history_status: str = "",
    requires_manual_review: Any = False,
    low_liquidity_flag: Any = False,
) -> str:
    if _bool_value(requires_manual_review):
        return "P0_manual_review"
    if _text(history_status) == "very_short_history":
        return "P3_archive_watch"
    if _bool_value(low_liquidity_flag):
        return "P2_low_liquidity_watch"
    return "P1_wait_for_history"


def _classify_observation_status(
    *,
    history_status: str,
    requires_manual_review: bool,
    low_liquidity_flag: bool,
) -> str:
    if requires_manual_review:
        return "manual_review_required"
    if history_status == "very_short_history":
        return "very_short_history"
    if history_status == "short_history" and low_liquidity_flag:
        return "waiting_but_low_liquidity"
    if history_status == "short_history":
        return "waiting_for_history"
    return "unknown"


def _recommended_action(
    *,
    observation_status: str,
    observation_priority: str,
    rows_needed: int,
    estimated_date: str,
) -> str:
    if observation_priority == "P0_manual_review":
        return "manual review required before candidate gate reconsideration"
    if observation_status == "very_short_history":
        return "archive watch; revisit after substantially more trading history accumulates"
    if observation_status == "waiting_but_low_liquidity":
        return "watch history accumulation and liquidity; do not use as a low score"
    if estimated_date != "unknown":
        return f"recheck candidate gate after at least {rows_needed} new trading day(s), around {estimated_date}"
    return "watch row count; calendar has no reliable eligible-date estimate"


def build_short_history_observation_pool(
    *,
    output_dir: str | Path = "output",
    diagnosis_path: str | Path | None = None,
    candidate_gate_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
    calendar_path: str | Path | None = None,
    diagnosis: pd.DataFrame | None = None,
    candidate_gate: pd.DataFrame | None = None,
    metadata: pd.DataFrame | None = None,
    calendar: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    output_path = Path(output_dir)
    diagnosis_frame = diagnosis if diagnosis is not None else _read_csv(diagnosis_path or output_path / "data_quality_diagnosis.csv")
    candidate_frame = candidate_gate if candidate_gate is not None else _read_csv(candidate_gate_path or output_path / "candidate_gate.csv")
    metadata_frame = metadata if metadata is not None else _read_csv(metadata_path or output_path / "etf_metadata.csv")
    calendar_frame = calendar if calendar is not None else _read_csv(calendar_path or Path("data") / "calendar" / "a_share_trading_calendar.csv")

    if diagnosis_frame.empty:
        return []

    candidate_by_symbol = _symbol_map(candidate_frame)
    metadata_by_symbol = _symbol_map(metadata_frame)
    history_mask = diagnosis_frame.get("history_status", pd.Series(dtype=object)).astype(str).isin(["short_history", "very_short_history"])
    rows: list[dict[str, Any]] = []
    for raw in diagnosis_frame.loc[history_mask].to_dict("records"):
        symbol = str(raw.get("symbol", "")).zfill(6)
        candidate = candidate_by_symbol.get(symbol, {})
        meta = metadata_by_symbol.get(symbol, {})
        row_count = _int_value(raw.get("row_count"), default=0)
        min_required_rows = _int_value(raw.get("min_required_rows"), default=250)
        anchor_date = _date_text(raw.get("latest_expected_date")) or _date_text(raw.get("last_date"))
        estimate = estimate_days_until_eligible(
            row_count=row_count,
            min_required_rows=min_required_rows,
            calendar=calendar_frame,
            anchor_date=anchor_date,
        )
        requires_manual_review = _bool_value(raw.get("requires_manual_review"))
        low_liquidity = _text(raw.get("liquidity_status")) == "low_liquidity" or "low_liquidity" in _text(candidate.get("observation_reason"))
        history_status = _text(raw.get("history_status") or "unknown")
        observation_status = _classify_observation_status(
            history_status=history_status,
            requires_manual_review=requires_manual_review,
            low_liquidity_flag=low_liquidity,
        )
        observation_priority = classify_observation_priority(
            history_status=history_status,
            requires_manual_review=requires_manual_review,
            low_liquidity_flag=low_liquidity,
        )
        abnormal_return = "abnormal_return" in _text(raw.get("failure_type"))
        estimated_date = _text(estimate["estimated_calendar_date_until_eligible"])
        notes = [
            "observation only; never enter candidate strategy until candidate gate is rerun cleanly",
            "short history is not a low score",
            _text(raw.get("notes")),
        ]
        out = {
            "symbol": symbol,
            "name": _text(raw.get("name") or candidate.get("name") or meta.get("name")),
            "category": _text(raw.get("category") or candidate.get("category") or meta.get("category") or meta.get("inferred_category")),
            "sub_category": _text(raw.get("sub_category") or candidate.get("sub_category") or meta.get("sub_category")),
            "row_count": row_count,
            "min_required_rows": min_required_rows,
            "rows_needed": estimate["rows_needed"],
            "first_date": _date_text(raw.get("first_date")),
            "last_date": _date_text(raw.get("last_date")),
            "latest_expected_date": _date_text(raw.get("latest_expected_date")),
            "end_date_gap_days": _int_value(raw.get("end_date_gap_days"), default=0),
            "history_status": history_status,
            "liquidity_status": _text(raw.get("liquidity_status") or "unknown"),
            "observation_status": observation_status,
            "observation_priority": observation_priority,
            "estimated_trading_days_until_eligible": estimate["estimated_trading_days_until_eligible"],
            "estimated_calendar_date_until_eligible": estimated_date,
            "requires_manual_review": requires_manual_review,
            "manual_review_reason": _manual_review_reason(raw) if requires_manual_review else "",
            "low_liquidity_flag": bool(low_liquidity),
            "abnormal_return_flag": bool(abnormal_return),
            "candidate_status": _text(candidate.get("candidate_status") or "unknown"),
            "recommended_action": _recommended_action(
                observation_status=observation_status,
                observation_priority=observation_priority,
                rows_needed=int(estimate["rows_needed"]),
                estimated_date=estimated_date,
            ),
            "notes": "; ".join(item for item in dict.fromkeys(notes) if item).strip("; "),
        }
        rows.append({column: out.get(column, "") for column in OBSERVATION_POOL_COLUMNS})

    priority_order = {
        "P0_manual_review": 0,
        "P1_wait_for_history": 1,
        "P2_low_liquidity_watch": 2,
        "P3_archive_watch": 3,
    }
    rows.sort(
        key=lambda item: (
            priority_order.get(str(item["observation_priority"]), 9),
            int(item["rows_needed"]),
            str(item["symbol"]),
        )
    )
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
    low_liquidity_mask = frame["low_liquidity_flag"].astype(str).str.lower().isin(bool_true)
    manual_mask = frame["requires_manual_review"].astype(str).str.lower().isin(bool_true)
    unknown_estimate_mask = frame["estimated_calendar_date_until_eligible"].astype(str).eq("unknown")
    days = pd.to_numeric(frame["estimated_trading_days_until_eligible"], errors="coerce")
    known_estimate_mask = ~unknown_estimate_mask
    specs = [
        ("total_observation_count", pd.Series(True, index=frame.index), "high", "keep outside candidate strategy", "short-history observation universe"),
        ("very_short_history", frame["history_status"].eq("very_short_history"), "medium", "archive watch until enough history exists", "very short history is prioritized as a status"),
        ("waiting_for_history", frame["observation_status"].eq("waiting_for_history"), "medium", "recheck after rows_needed reaches zero", "temporary history blocker"),
        ("low_liquidity_watch", low_liquidity_mask, "medium", "watch liquidity separately from history", "low liquidity is not a low score"),
        ("manual_review_required", manual_mask, "high", "manual review before any candidate gate reconsideration", "manual review takes priority over history wait"),
        ("estimated_eligible_within_20d", known_estimate_mask & days.le(20), "medium", "rerun candidate gate after enough rows accumulate", "calendar estimate is only as good as current snapshot"),
        ("estimated_eligible_within_60d", known_estimate_mask & days.le(60), "medium", "track for next candidate gate cycle", "calendar estimate is only as good as current snapshot"),
        ("unknown_estimate", unknown_estimate_mask, "medium", "extend or refresh trading calendar before dating eligibility", "no fabricated eligible date"),
    ]
    out: list[dict[str, Any]] = []
    for item, mask, severity, action, notes in specs:
        count = int(mask.sum())
        out.append(
            {
                "summary_item": item,
                "count": count,
                "ratio": round(count / total, 6),
                "severity": severity if count else "info",
                "examples": _examples(frame, mask),
                "suggested_action": action if count else "no action",
                "notes": notes,
            }
        )
    for value, count in frame["observation_priority"].value_counts().sort_index().items():
        mask = frame["observation_priority"].eq(value)
        out.append(
            {
                "summary_item": f"observation_priority:{value}",
                "count": int(count),
                "ratio": round(int(count) / total, 6),
                "severity": "high" if str(value).startswith("P0") else "medium",
                "examples": _examples(frame, mask),
                "suggested_action": "see per-symbol recommended_action",
                "notes": "grouped by observation_priority",
            }
        )
    for value, count in frame["observation_status"].value_counts().sort_index().items():
        mask = frame["observation_status"].eq(value)
        out.append(
            {
                "summary_item": f"observation_status:{value}",
                "count": int(count),
                "ratio": round(int(count) / total, 6),
                "severity": "high" if str(value) == "manual_review_required" else "medium",
                "examples": _examples(frame, mask),
                "suggested_action": "see per-symbol recommended_action",
                "notes": "grouped by observation_status",
            }
        )
    return out


def write_observation_pool_report(
    rows: list[dict[str, Any]],
    *,
    report_path: str | Path = "output/short_history_observation_pool.csv",
    summary_path: str | Path = "output/short_history_observation_summary.csv",
) -> tuple[Path, Path]:
    report = Path(report_path)
    summary = Path(summary_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=OBSERVATION_POOL_COLUMNS).to_csv(report, index=False, encoding="utf-8-sig")
    pd.DataFrame(_summary_rows(rows), columns=OBSERVATION_SUMMARY_COLUMNS).to_csv(summary, index=False, encoding="utf-8-sig")
    return report, summary


def summarize_observation_pool(
    rows: list[dict[str, Any]] | None = None,
    *,
    report_path: str | Path | None = None,
    example_limit: int = 10,
) -> dict[str, Any]:
    frame = pd.DataFrame(rows) if rows is not None else _read_csv(report_path or "output/short_history_observation_pool.csv")
    if frame.empty:
        return {
            "total_observation_count": 0,
            "very_short_history_count": 0,
            "low_liquidity_watch_count": 0,
            "manual_review_required_count": 0,
            "estimated_eligible_within_20d_count": 0,
            "estimated_eligible_within_60d_count": 0,
            "unknown_estimate_count": 0,
            "observation_status_counts": {},
            "observation_priority_counts": {},
            "top_examples": [],
        }

    bool_true = {"true", "1", "yes", "y"}
    days = pd.to_numeric(frame["estimated_trading_days_until_eligible"], errors="coerce")
    unknown_estimate = frame["estimated_calendar_date_until_eligible"].astype(str).eq("unknown")
    known_estimate = ~unknown_estimate
    top_examples = frame.head(example_limit)[
        [
            "symbol",
            "name",
            "history_status",
            "rows_needed",
            "estimated_calendar_date_until_eligible",
            "observation_priority",
            "recommended_action",
        ]
    ].to_dict("records")
    return {
        "total_observation_count": int(len(frame)),
        "very_short_history_count": int(frame["history_status"].eq("very_short_history").sum()),
        "low_liquidity_watch_count": int(frame["low_liquidity_flag"].astype(str).str.lower().isin(bool_true).sum()),
        "manual_review_required_count": int(frame["requires_manual_review"].astype(str).str.lower().isin(bool_true).sum()),
        "estimated_eligible_within_20d_count": int((known_estimate & days.le(20)).sum()),
        "estimated_eligible_within_60d_count": int((known_estimate & days.le(60)).sum()),
        "unknown_estimate_count": int(unknown_estimate.sum()),
        "observation_status_counts": {str(k): int(v) for k, v in frame["observation_status"].value_counts().sort_index().to_dict().items()},
        "observation_priority_counts": {str(k): int(v) for k, v in frame["observation_priority"].value_counts().sort_index().to_dict().items()},
        "top_examples": top_examples,
    }


def merge_observation_pool_into_qa_report(
    qa_report_path: str | Path = "output/qa_report.json",
    *,
    rows: list[dict[str, Any]] | None = None,
) -> bool:
    path = Path(qa_report_path)
    if not path.exists():
        return False
    report = json.loads(path.read_text(encoding="utf-8"))
    output_dir = path.parent
    summary = summarize_observation_pool(rows, report_path=output_dir / "short_history_observation_pool.csv")
    data_layer = report.setdefault("data_layer", {})
    data_layer.update(
        {
            "short_history_observation_pool_report": str(output_dir / "short_history_observation_pool.csv"),
            "short_history_observation_summary_report": str(output_dir / "short_history_observation_summary.csv"),
            "observation_pool": summary,
            "total_observation_count": summary["total_observation_count"],
            "very_short_history_count": summary["very_short_history_count"],
            "low_liquidity_watch_count": summary["low_liquidity_watch_count"],
            "manual_review_required_count": summary["manual_review_required_count"],
            "estimated_eligible_within_20d_count": summary["estimated_eligible_within_20d_count"],
            "estimated_eligible_within_60d_count": summary["estimated_eligible_within_60d_count"],
            "unknown_estimate_count": summary["unknown_estimate_count"],
            "observation_pool_top_examples": summary["top_examples"],
        }
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True
